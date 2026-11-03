"""본문 → 검색용 조각(chunk).

조각은 **문서를 찾는 데만** 쓴다. 검색으로 찾은 뒤 LLM에게는 source_document의 본문 전체를 준다
(CLAUDE.md: 코드로 문단을 골라내거나 본문을 잘라 주지 않는다).

- 줄 단위로 CHUNK_CHARS까지 모으고, 다음 조각은 앞 조각 끝 OVERLAP_CHARS만큼의 줄을 겹쳐 시작한다
  (표 한 행·문장이 경계에서 잘려 검색이 안 되는 것 방지).
- PDF는 페이지를 넘지 않게 페이지마다 나눠서 조각에 page 번호를 단다.
- 한 줄이 CHUNK_CHARS보다 길면(줄바꿈 없는 TXT 등) 문장 끝·공백 기준으로 나눈다.
"""
import re
from dataclasses import dataclass

from engine.rag.extract import ExtractedDoc

CHUNK_CHARS = 800    # bge-m3는 8192토큰까지 받지만, 조각이 길면 한 조각에 주제가 섞여 검색 정확도가 떨어진다
OVERLAP_CHARS = 150


@dataclass
class Chunk:
    index: int
    content: str
    page: int | None = None


def _split_long(line: str) -> list[str]:
    out = []
    while len(line) > CHUNK_CHARS:
        cut = max(line.rfind(sep, 0, CHUNK_CHARS) for sep in (". ", "다. ", "? ", "! ", " "))
        cut = cut + 1 if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
        out.append(line[:cut].strip())
        line = line[cut:].strip()
    if line:
        out.append(line)
    return out


def _chunk_text(text: str) -> list[str]:
    lines = [piece for raw in text.split("\n") if raw.strip() for piece in _split_long(raw.strip())]
    chunks, cur, size = [], [], 0
    for line in lines:
        if cur and size + len(line) + 1 > CHUNK_CHARS:
            chunks.append("\n".join(cur))
            # 겹침: 끝에서부터 OVERLAP_CHARS가 될 때까지 줄을 다시 가져온다
            keep, kept = [], 0
            for prev in reversed(cur):
                if kept + len(prev) > OVERLAP_CHARS:
                    break
                keep.insert(0, prev)
                kept += len(prev) + 1
            cur, size = keep, kept
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def chunk_document(doc: ExtractedDoc) -> list[Chunk]:
    if doc.pages:
        parts = [(i, t) for i, t in enumerate(doc.pages, start=1)]
    else:
        parts = [(None, doc.text)]
    out = []
    for page, text in parts:
        for content in _chunk_text(text):
            if len(re.sub(r"\W", "", content)) < 10:  # 쪽 번호·머리말만 있는 조각은 검색 잡음
                continue
            out.append(Chunk(index=len(out), content=content, page=page))
    return out
