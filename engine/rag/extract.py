"""파일 → 본문 텍스트. 업로드(실시간)와 수집 데이터 적재(일괄)가 함께 쓴다.

지원 형식 (프론트 업로드 칸 기준: PDF, HWP/HWPX, DOC/DOCX, TXT, 이미지)
- PDF   : 페이지마다 텍스트 층을 읽고, 글자가 거의 없는 페이지(스캔본)는 OCR. 페이지 앞에 "[페이지 N]".
          글자가 있는 페이지라도 텍스트 층이 없는 큰 그림(붙여 넣은 스캔·캡처)은 그 부분만 OCR
- 이미지 : OCR (engine/rag/ocr.py, PaddleOCR). PNG/JPG/BMP/TIFF/WEBP/GIF, 아이폰 사진(HEIC/HEIF)
- DOCX  : 문단·글상자·표를 문서 순서대로. 표는 한 행을 " | "로 이음
- DOC   : Word 97-2003 이진 형식. 조각 표(piece table)를 따라 본문만 읽음 (그림·글상자 안 글은 빠질 수 있음)
- HWP   : 한글 5.x 이진 형식(OLE). BodyText 구역의 문단 텍스트. 배포용·암호 문서는 읽지 못함
- HWPX  : 한글 XML 형식(zip). Contents/section*.xml의 문단 텍스트
- TXT/MD/CSV : UTF-8 → CP949 순으로 디코딩

DOCX·HWP·HWPX·PDF에 들어 있는 그림(스캔본·캡처를 붙여 넣은 보고서·포트폴리오)도 OCR해 그림이 있던 자리에
"[그림 N]" 다음 줄부터 넣는다 (그림만 있는 문서는 본문이 거의 비기 때문). 작은 그림(아이콘·로고)은 건너뛰고,
같은 그림이 되풀이되면 한 번만 읽고, 문서 하나에 OCR_MAX_EMBEDDED개까지만 읽는다(넘으면 warnings에 남김).

본문은 자르거나 문단을 골라내지 않는다 (CLAUDE.md: LLM은 본문 전체를 읽는다). 줄 정리(빈 줄 합치기)만 한다.
"""
import hashlib
import io
import os
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from lxml import etree  # python-docx 의존 패키지

OCR_DPI = int(os.getenv("OCR_DPI", "300"))  # 스캔본 PDF를 그림으로 바꿀 해상도. 낮추면 빠르지만 작은 글자가 깨진다
MIN_PAGE_CHARS = 30  # PDF 페이지 텍스트 층이 이보다 짧으면 스캔본으로 보고 OCR
MIN_EMBED_SIDE = 300     # 문서 속 그림의 짧은 변이 이보다 작으면(아이콘·로고·글머리 그림) OCR 안 함 (픽셀)
MIN_PDF_IMAGE_AREA = 0.1  # PDF 글자 페이지에서 그림만 OCR할 때 최소 크기 (페이지 넓이 비율)
# 그림 OCR 상한. 1장 약 2초라 캡처 200장짜리 보고서가 업로드를 수 분 붙잡지 않게 한다. 일괄 적재는 .env로 늘릴 것
MAX_EMBEDDED_OCR = int(os.getenv("OCR_MAX_EMBEDDED", "40"))

TEXT_EXT = {".txt", ".md", ".csv"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif", ".heic", ".heif"}
SUPPORTED_EXT = {".pdf", ".docx", ".doc", ".hwp", ".hwpx"} | TEXT_EXT | IMAGE_EXT


class UnsupportedFile(ValueError):
    """지원하지 않는 형식이거나 읽을 수 없는 파일 (배포용 HWP, 암호 문서 등)."""


@dataclass
class ExtractedDoc:
    filename: str
    file_type: str                 # 확장자에서 점을 뺀 것: pdf, hwp, png …
    text: str                      # 본문 전체
    method: str                    # text(텍스트 층) / ocr / mixed(일부 페이지·문서 속 그림만 OCR)
    pages: list[str] | None = None  # PDF만: 페이지별 본문 (청크에 페이지 번호를 달 때 씀)
    ocr_pages: list[int] = field(default_factory=list)  # OCR로 읽은 PDF 페이지 번호(1부터)
    ocr_images: int = 0            # 문서 속 그림을 OCR한 수 (DOCX·HWP·HWPX·PDF 글자 페이지)
    warnings: list[str] = field(default_factory=list)
    sha256: str = ""


def _clean(text: str) -> str:
    """줄 끝 공백 제거, 3줄 이상 빈 줄을 1줄로. 내용은 건드리지 않는다."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------- 그림 OCR (이미지 파일·문서 속 그림 공용)

def _open_image(data: bytes):
    """그림 바이트 → PIL RGB 이미지. 열 수 없는 형식(EMF·WMF 벡터 그림 등)이면 None."""
    from PIL import Image, ImageOps
    try:
        import pillow_heif  # 아이폰 HEIC. 없으면 HEIC만 못 연다
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    try:
        img = Image.open(io.BytesIO(data))
        img.seek(0)  # GIF·TIFF 여러 장이면 첫 장
        return ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        return None


def _ocr_pil(img) -> str:
    import numpy as np

    from engine.rag.ocr import ocr_image
    # 휴대폰 사진은 4000px을 넘기도 한다. 검출 모델 입력이 커지면 느리기만 해서 긴 변을 3000px로 줄임
    if max(img.size) > 3000:
        img.thumbnail((3000, 3000))
    return _clean(ocr_image(np.asarray(img)))


class _EmbeddedOCR:
    """문서 하나에 든 그림들을 OCR한다. 같은 그림은 한 번만, 작은 그림은 건너뜀, 상한까지만."""

    def __init__(self, doc: "ExtractedDoc"):
        self.doc = doc
        self.seen: set[str] = set()
        self.over = 0

    def __call__(self, data: bytes) -> str:
        """그림 바이트 → "[그림 N]\n텍스트" (읽을 글자가 없거나 건너뛰면 "")."""
        h = hashlib.sha1(data).hexdigest()
        if h in self.seen:
            return ""
        self.seen.add(h)
        img = _open_image(data)
        if img is None or min(img.size) < MIN_EMBED_SIDE:
            return ""
        return self.ocr(img)

    def ocr(self, img) -> str:
        if self.doc.ocr_images >= MAX_EMBEDDED_OCR:
            self.over += 1
            return ""
        self.doc.ocr_images += 1
        text = _ocr_pil(img)
        return f"[그림 {self.doc.ocr_images}]\n{text}" if text else ""

    def finish(self) -> None:
        if self.over:
            self.doc.warnings.append(f"그림 {self.over}개는 OCR 상한({MAX_EMBEDDED_OCR}개)을 넘어 읽지 않음")


# ---------------------------------------------------------------- PDF · 이미지

def _pdf(data: bytes, doc: ExtractedDoc) -> None:
    import numpy as np
    import pymupdf

    from engine.rag.ocr import ocr_image

    pdf = pymupdf.open(stream=data, filetype="pdf")
    if pdf.needs_pass:
        raise UnsupportedFile("암호가 걸린 PDF")
    def render(page, clip=None):
        pix = page.get_pixmap(dpi=OCR_DPI, clip=clip)
        img = np.frombuffer(pix.samples, dtype="uint8").reshape(pix.height, pix.width, pix.n)
        return img[:, :, :3] if pix.n == 4 else img

    embedded = _EmbeddedOCR(doc)
    pages = []
    for i, page in enumerate(pdf, start=1):
        text = page.get_text("text", sort=True).strip()
        if len(text) < MIN_PAGE_CHARS and page.get_images():
            text = ocr_image(render(page))
            doc.ocr_pages.append(i)
        elif page.get_images():
            # 글자 페이지 속 큰 그림 중 텍스트 층이 없는 것(붙여 넣은 스캔·캡처)만. 텍스트 층이 덮인 스캔 PDF는 건너뜀
            from PIL import Image
            area = page.rect.width * page.rect.height
            parts = [text]
            for info in page.get_image_info():
                box = pymupdf.Rect(info["bbox"]) & page.rect
                if box.is_empty or box.width * box.height < area * MIN_PDF_IMAGE_AREA:
                    continue
                if len(page.get_text("text", clip=box).strip()) >= MIN_PAGE_CHARS:
                    continue
                t = embedded.ocr(Image.fromarray(render(page, box)))
                if t:
                    parts.append(t)
            text = "\n\n".join(parts)
        pages.append(_clean(text))
    embedded.finish()
    doc.pages = pages
    doc.text = "\n\n".join(f"[페이지 {i}]\n{t}" for i, t in enumerate(pages, start=1) if t)
    text_pages = [i for i, t in enumerate(pages, start=1) if t and i not in doc.ocr_pages]
    if not doc.ocr_pages:
        doc.method = "text"
    elif not text_pages:  # 빈 페이지는 세지 않는다
        doc.method = "ocr"
    else:
        doc.method = "mixed"


def _image(data: bytes, doc: ExtractedDoc) -> None:
    img = _open_image(data)
    if img is None:
        raise UnsupportedFile("그림 파일을 열 수 없음 (손상되었거나 지원하지 않는 그림 형식)")
    doc.text = _ocr_pil(img)
    doc.method = "ocr"


# ---------------------------------------------------------------- Word

def _docx(data: bytes, doc: ExtractedDoc) -> None:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(io.BytesIO(data))
    embedded = _EmbeddedOCR(doc)

    def images(el) -> list[str]:
        """요소 안 그림(DrawingML a:blip, 옛 VML v:imagedata)의 OCR 결과."""
        out = []
        for rid in _X_IMAGE_IDS(el):
            part = d.part.related_parts.get(rid)
            if part is not None and (t := embedded(part.blob)):
                out.append(t)
        return out

    def textboxes(el) -> list[str]:
        """글상자 문단. mc:Fallback(옛 형식으로 한 번 더 든 같은 글상자)은 빼서 두 번 나오지 않게."""
        return ["".join(_X_TEXT(p)) for p in _X_TEXTBOX_PARAS(el)]

    out = []
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            out.append(Paragraph(el, d).text)
            out += textboxes(el)
            out += images(el)
        elif tag == "tbl":
            for row in Table(el, d).rows:
                cells, prev = [], None
                for cell in row.cells:
                    if cell._tc is prev:  # 병합된 칸은 같은 객체가 반복된다
                        continue
                    prev = cell._tc
                    cells.append(cell.text.replace("\n", " ").strip())
                out.append(" | ".join(cells))
                out += images(row._tr)  # 칸 속 그림은 그 행 다음에
            out.append("")
    embedded.finish()
    doc.text = _clean("\n".join(out))
    doc.method = "text"


_DOCX_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}
# python-docx 요소의 xpath()는 namespaces 인자를 못 받아 lxml XPath를 미리 만들어 쓴다
_X_IMAGE_IDS = etree.XPath(".//a:blip/@r:embed | .//v:imagedata/@r:id", namespaces=_DOCX_NS)
_X_TEXTBOX_PARAS = etree.XPath(".//w:txbxContent[not(ancestor::mc:Fallback)]/w:p", namespaces=_DOCX_NS)
_X_TEXT = etree.XPath(".//w:t/text()", namespaces=_DOCX_NS)


def _doc(data: bytes, doc: ExtractedDoc) -> None:
    """Word 97-2003(.doc). [MS-DOC] FIB → Clx → 조각 표의 각 조각을 읽어 이어 붙인다."""
    import olefile

    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except OSError as e:
        raise UnsupportedFile(f"DOC 파일을 열 수 없음: {e}") from e
    wd = ole.openstream("WordDocument").read()
    flags = struct.unpack_from("<H", wd, 0x0A)[0]
    if flags & 0x0100:
        raise UnsupportedFile("암호가 걸린 DOC")
    table = ole.openstream("1Table" if flags & 0x0200 else "0Table").read()
    # FibBase(32) + csw + fibRgW + cslw + fibRgLw + cbRgFcLcb 다음이 FibRgFcLcb. fcClx는 34번째 쌍(0부터 33)
    pos = 32
    csw = struct.unpack_from("<H", wd, pos)[0]
    pos += 2 + csw * 2
    cslw = struct.unpack_from("<H", wd, pos)[0]
    pos += 2 + cslw * 4 + 2
    fc_clx, lcb_clx = struct.unpack_from("<II", wd, pos + 33 * 8)
    clx = table[fc_clx:fc_clx + lcb_clx]
    i = 0
    while i < len(clx) and clx[i] == 0x01:  # Prc(서식 정보)는 건너뜀
        i += 3 + struct.unpack_from("<H", clx, i + 1)[0]
    if i >= len(clx) or clx[i] != 0x02:
        raise UnsupportedFile("DOC 조각 표를 찾지 못함")
    lcb = struct.unpack_from("<I", clx, i + 1)[0]
    plc = clx[i + 5:i + 5 + lcb]
    n = (lcb - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    parts = []
    for k in range(n):
        fc = struct.unpack_from("<I", plc, (n + 1) * 4 + k * 8 + 2)[0]
        length = cps[k + 1] - cps[k]
        if fc & 0x40000000:  # 압축 조각: 1바이트 문자(cp1252)
            start = (fc & 0x3FFFFFFF) // 2
            parts.append(wd[start:start + length].decode("cp1252", errors="replace"))
        else:
            parts.append(wd[fc:fc + length * 2].decode("utf-16-le", errors="replace"))
    text = "".join(parts)
    # 필드 코드(\x13 명령 \x14 결과 \x15)는 결과만 남긴다. \x07은 표 칸 끝
    text = re.sub(r"\x13[^\x13\x14\x15]*\x14", "", text)
    text = re.sub(r"\x13[^\x13\x14\x15]*\x15", "", text)
    text = text.replace("\x07", " | ").replace("\x0b", "\n").replace("\x0c", "\n")
    text = re.sub(r"[\x00-\x08\x0e-\x1f]", "", text)
    doc.text = _clean(text)
    doc.method = "text"


# ---------------------------------------------------------------- 한글

HWPTAG_PARA_TEXT = 67  # HWPTAG_BEGIN(16) + 51
HWPTAG_SHAPE_COMPONENT_PICTURE = 85  # HWPTAG_BEGIN + 69. 71바이트 위치(테두리 12 + 좌표 32 + 자르기 16 + 여백 8 + 밝기·명암·효과 3)에 BinItem ID
_HWP_PUA = re.compile("[\ue000-\uf8ff\U000f0000-\U0010ffff]")
_HWP_CHAR_CTRL = {0, 10, 13, *range(24, 32)}  # 1글자 크기 제어 문자. 나머지 0~31은 8글자 크기(인라인·확장 제어)


def _hwp_para_text(raw: bytes) -> str:
    units = struct.unpack(f"<{len(raw) // 2}H", raw[:len(raw) // 2 * 2])
    out, i = [], 0
    while i < len(units):
        c = units[i]
        if c >= 32:
            out.append(chr(c))
            i += 1
        elif c in _HWP_CHAR_CTRL:
            if c in (10, 13):
                out.append("\n")
            elif c in (30, 31):  # 묶음 빈칸, 고정폭 빈칸
                out.append(" ")
            i += 1
        else:
            if c == 9:
                out.append("\t")
            i += 8
    # 한 단위씩 chr로 바꾸면 BMP 밖 글자(이모지·일부 한자)의 서로게이트 쌍이 갈라진다. 다시 묶어 디코딩
    text = "".join(out).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return _HWP_PUA.sub("", text)  # 한글 글머리표 기호(사용자 정의 영역 글자)는 다른 곳에선 □로만 보인다


def _hwp(data: bytes, doc: ExtractedDoc) -> None:
    """한글 5.x(.hwp). FileHeader 속성 → BodyText/SectionN(raw deflate) → 레코드 중 문단 텍스트."""
    import olefile

    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except OSError as e:
        raise UnsupportedFile(f"HWP 파일을 열 수 없음 (한글 97 이전 형식일 수 있음): {e}") from e
    header = ole.openstream("FileHeader").read()
    props = struct.unpack_from("<I", header, 36)[0]
    if props & 0x02:
        raise UnsupportedFile("암호가 걸린 HWP")
    if props & 0x04:
        raise UnsupportedFile("배포용 HWP (본문이 암호화되어 읽을 수 없음)")
    compressed = bool(props & 0x01)
    sections = sorted(
        (e for e in ole.listdir() if len(e) == 2 and e[0] == "BodyText" and e[1].startswith("Section")),
        key=lambda e: int(e[1][7:] or 0))
    # BinData/BIN0001.png … — 그림 레코드의 BinItem ID(1부터)가 이름의 16진수 번호
    bins = {int(e[1][3:7], 16): e for e in ole.listdir()
            if len(e) == 2 and e[0] == "BinData" and re.fullmatch(r"BIN[0-9A-Fa-f]{4}\..+", e[1])}
    embedded = _EmbeddedOCR(doc)
    used: set[int] = set()

    def bin_text(bin_id: int) -> str:
        used.add(bin_id)
        raw = ole.openstream(bins[bin_id]).read()
        try:  # BinData는 보통 raw deflate로 압축돼 있다 (항목마다 다를 수 있어 풀어 보고 안 되면 그대로)
            raw = zlib.decompress(raw, -15)
        except zlib.error:
            pass
        return embedded(raw)

    paras = []
    for entry in sections:
        raw = ole.openstream(entry).read()
        if compressed:
            raw = zlib.decompress(raw, -15)
        pos = 0
        while pos + 4 <= len(raw):
            h = struct.unpack_from("<I", raw, pos)[0]
            tag, size = h & 0x3FF, (h >> 20) & 0xFFF
            pos += 4
            if size == 0xFFF:
                size = struct.unpack_from("<I", raw, pos)[0]
                pos += 4
            if tag == HWPTAG_PARA_TEXT:
                paras.append(_hwp_para_text(raw[pos:pos + size]))
            elif tag == HWPTAG_SHAPE_COMPONENT_PICTURE and size >= 73:
                bin_id = struct.unpack_from("<H", raw, pos + 71)[0]
                if bin_id in bins and (t := bin_text(bin_id)):
                    paras.append(t)
            pos += size
    # 본문 레코드에서 못 찾은 그림(머리말·바탕쪽 등)은 끝에
    for bin_id in sorted(set(bins) - used):
        if t := bin_text(bin_id):
            paras.append(t)
    embedded.finish()
    text = "\n".join(paras)
    if not text.strip() and ole.exists("PrvText"):  # 본문을 못 읽으면 미리보기 텍스트(앞부분만)라도
        text = ole.openstream("PrvText").read().decode("utf-16-le", errors="replace")
        doc.warnings.append("본문 대신 미리보기 텍스트(PrvText)를 사용 — 앞부분만 들어 있음")
    doc.text = _clean(text)
    doc.method = "text"


def _hwpx(data: bytes, doc: ExtractedDoc) -> None:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UnsupportedFile("HWPX 파일을 열 수 없음") from e
    names = sorted((n for n in z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)),
                   key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
    # 그림: section XML의 hc:img binaryItemIDRef="image1" → content.hpf의 opf:item href="BinData/image1.png"
    hrefs = {}
    if "Contents/content.hpf" in z.namelist():
        for item in ET.fromstring(z.read("Contents/content.hpf")).iter():
            if item.tag.rsplit("}", 1)[-1] == "item" and item.get("href", "").startswith("BinData/"):
                hrefs[item.get("id")] = item.get("href")
    embedded = _EmbeddedOCR(doc)
    out: list[str] = []

    def walk(el):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "img" and (href := hrefs.get(el.get("binaryItemIDRef"))) and href in z.namelist():
            if t := embedded(z.read(href)):
                out.append(f"\n{t}\n")
            return
        if tag == "t":
            out.append(el.text or "")
            for ch in el:
                ctag = ch.tag.rsplit("}", 1)[-1]
                out.append("\t" if ctag == "tab" else "\n" if ctag == "lineBreak" else "")
                out.append(ch.tail or "")
            return
        for ch in el:
            walk(ch)
        if tag == "p":
            out.append("\n")
        elif tag == "tc":
            out.append(" | ")
        elif tag == "tr":
            out.append("\n")

    for n in names:
        walk(ET.fromstring(z.read(n)))
    embedded.finish()
    doc.text = _clean("".join(out))
    doc.method = "text"


# ---------------------------------------------------------------- 텍스트

def _txt(data: bytes, doc: ExtractedDoc) -> None:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        for enc in ("utf-8-sig", "cp949"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = data.decode("utf-8", errors="replace")
            doc.warnings.append("인코딩을 알 수 없어 깨진 글자를 대체 문자로 바꿈")
    doc.text = _clean(text)
    doc.method = "text"


_HANDLERS = {".pdf": _pdf, ".docx": _docx, ".doc": _doc, ".hwp": _hwp, ".hwpx": _hwpx,
             **{e: _txt for e in TEXT_EXT}, **{e: _image for e in IMAGE_EXT}}


def extract(data: bytes, filename: str) -> ExtractedDoc:
    """파일 바이트 + 파일 이름(확장자로 형식 판단) → ExtractedDoc. 못 읽으면 UnsupportedFile."""
    ext = Path(filename).suffix.lower()
    if ext not in _HANDLERS:
        raise UnsupportedFile(f"지원하지 않는 형식: {ext or '(확장자 없음)'}")
    doc = ExtractedDoc(filename=filename, file_type=ext[1:], text="", method="text",
                       sha256=hashlib.sha256(data).hexdigest())
    try:
        _HANDLERS[ext](data, doc)
    except UnsupportedFile:
        raise
    except Exception as e:  # 손상된 파일 등. 업로드 API가 파일 하나 때문에 통째로 실패하지 않게 한 형식으로 바꿈
        raise UnsupportedFile(f"{ext[1:].upper()} 읽기 실패: {type(e).__name__}: {e}") from e
    if doc.ocr_images and doc.method == "text":
        doc.method = "mixed"  # 글은 텍스트로, 그림 속 글은 OCR로
    if not doc.text:
        doc.warnings.append("추출된 글자가 없음")
    return doc


def extract_path(path: str | Path) -> ExtractedDoc:
    path = Path(path)
    return extract(path.read_bytes(), path.name)
