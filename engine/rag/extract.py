"""파일 → 본문 텍스트. 업로드(실시간)와 수집 데이터 적재(일괄)가 함께 쓴다.

지원 형식 (프론트 업로드 칸 기준: PDF, HWP/HWPX, DOC/DOCX, TXT, PNG/JPG)
- PDF   : 페이지마다 텍스트 층을 읽고, 글자가 거의 없는 페이지(스캔본)는 OCR. 페이지 앞에 "[페이지 N]"
- 이미지 : OCR (engine/rag/ocr.py, PaddleOCR)
- DOCX  : 문단·표를 문서 순서대로. 표는 한 행을 " | "로 이음
- DOC   : Word 97-2003 이진 형식. 조각 표(piece table)를 따라 본문만 읽음 (그림·글상자 안 글은 빠질 수 있음)
- HWP   : 한글 5.x 이진 형식(OLE). BodyText 구역의 문단 텍스트. 배포용·암호 문서는 읽지 못함
- HWPX  : 한글 XML 형식(zip). Contents/section*.xml의 문단 텍스트
- TXT/MD/CSV : UTF-8 → CP949 순으로 디코딩

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

OCR_DPI = int(os.getenv("OCR_DPI", "300"))  # 스캔본 PDF를 그림으로 바꿀 해상도. 낮추면 빠르지만 작은 글자가 깨진다
MIN_PAGE_CHARS = 30  # PDF 페이지 텍스트 층이 이보다 짧으면 스캔본으로 보고 OCR

TEXT_EXT = {".txt", ".md", ".csv"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
SUPPORTED_EXT = {".pdf", ".docx", ".doc", ".hwp", ".hwpx"} | TEXT_EXT | IMAGE_EXT


class UnsupportedFile(ValueError):
    """지원하지 않는 형식이거나 읽을 수 없는 파일 (배포용 HWP, 암호 문서 등)."""


@dataclass
class ExtractedDoc:
    filename: str
    file_type: str                 # 확장자에서 점을 뺀 것: pdf, hwp, png …
    text: str                      # 본문 전체
    method: str                    # text(텍스트 층) / ocr / mixed(PDF 일부 페이지만 OCR)
    pages: list[str] | None = None  # PDF만: 페이지별 본문 (청크에 페이지 번호를 달 때 씀)
    ocr_pages: list[int] = field(default_factory=list)  # OCR로 읽은 PDF 페이지 번호(1부터)
    warnings: list[str] = field(default_factory=list)
    sha256: str = ""


def _clean(text: str) -> str:
    """줄 끝 공백 제거, 3줄 이상 빈 줄을 1줄로. 내용은 건드리지 않는다."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------- PDF · 이미지

def _pdf(data: bytes, doc: ExtractedDoc) -> None:
    import numpy as np
    import pymupdf

    from engine.rag.ocr import ocr_image

    pdf = pymupdf.open(stream=data, filetype="pdf")
    if pdf.needs_pass:
        raise UnsupportedFile("암호가 걸린 PDF")
    pages = []
    for i, page in enumerate(pdf, start=1):
        text = page.get_text("text", sort=True).strip()
        if len(text) < MIN_PAGE_CHARS and page.get_images():
            pix = page.get_pixmap(dpi=OCR_DPI)
            img = np.frombuffer(pix.samples, dtype="uint8").reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                img = img[:, :, :3]
            text = ocr_image(img)
            doc.ocr_pages.append(i)
        pages.append(_clean(text))
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
    import numpy as np
    from PIL import Image, ImageOps

    from engine.rag.ocr import ocr_image

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    # 휴대폰 사진은 4000px을 넘기도 한다. 검출 모델 입력이 커지면 느리기만 해서 긴 변을 3000px로 줄임
    if max(img.size) > 3000:
        img.thumbnail((3000, 3000))
    doc.text = _clean(ocr_image(np.asarray(img)))
    doc.method = "ocr"


# ---------------------------------------------------------------- Word

def _docx(data: bytes, doc: ExtractedDoc) -> None:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(io.BytesIO(data))
    out = []
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            out.append(Paragraph(el, d).text)
        elif tag == "tbl":
            for row in Table(el, d).rows:
                cells, prev = [], None
                for cell in row.cells:
                    if cell._tc is prev:  # 병합된 칸은 같은 객체가 반복된다
                        continue
                    prev = cell._tc
                    cells.append(cell.text.replace("\n", " ").strip())
                out.append(" | ".join(cells))
            out.append("")
    doc.text = _clean("\n".join(out))
    doc.method = "text"


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
            pos += size
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
    out: list[str] = []

    def walk(el):
        tag = el.tag.rsplit("}", 1)[-1]
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
    if not doc.text:
        doc.warnings.append("추출된 글자가 없음")
    return doc


def extract_path(path: str | Path) -> ExtractedDoc:
    path = Path(path)
    return extract(path.read_bytes(), path.name)
