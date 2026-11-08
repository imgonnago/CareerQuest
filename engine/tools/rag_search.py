"""RAG 검색 도구: 벡터 DB(engine/rag/store.py)의 문서를 2단계 근거 풀 항목(EvidenceDoc)으로 바꿔 준다.

검색 API(requirements.search_requirements)와 같은 RequirementSearchResult를 돌려줘서 근거 풀에 그대로 넣는다.
- 조각(chunk)은 문서를 찾는 데만 쓰고, 근거 풀 본문(texts)에는 source_document의 본문 전체를 넣는다
  (CLAUDE.md: LLM은 본문 전체를 읽는다). EvidenceDoc에는 본문을 넣지 않는다.
- doc_id는 "rag://source_document/<id>"를 정규화 URL처럼 해시한 값 — 검색 문서와 같은 16자 형식이라 3단계가 그대로 쓴다.
  실제 출처 URL(source_url)이 있으면 url 칸에 그것을 둔다 (학생에게 보여 줄 링크).
- 본문을 이미 가지고 있으니 fetch_status="ok" (크롤링하지 않음).

DB가 꺼져 있거나 RAG 테이블이 비어 있으면 RagSearchError. 2단계는 경고만 남기고 검색 API로만 진행한다.
"""
import logging
from datetime import datetime, timezone

from engine.schemas import EvidenceDoc
from engine.tools.requirements import RequirementSearchResult
from engine.tools.text import make_doc_id

log = logging.getLogger(__name__)

# store.search 시험(problem.txt [48]): 관련 조각 0.59~0.68, 무관 0.42~0.47 (bge-m3)
MIN_SCORE = 0.55
SNIPPET_CHARS = 300  # 선택 LLM·근거 풀 표시용 발췌 길이 (본문은 texts에 전체)


# [74] LLM 도구 호출용 정의 (Ollama 형식). 문서 종류·건수는 슬롯에 맞게 코드가 정하므로 인자는 검색어만 받는다
TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "search_rag",
        "description": "건양대 교육과정·비교과·수업계획서와 수집해 둔 채용 공고·자격증·공모전 자료(DB)를 찾는다. "
                       "결과는 문서 번호와 제목 목록이다.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어. 예: '건양대 인공지능 교육과정'"},
            },
            "required": ["query"],
        },
    },
}


class RagSearchError(RuntimeError):
    """RAG DB에 접속·조회하지 못함."""


def rag_doc_id(source_document_id: int) -> str:
    return make_doc_id(f"rag://source_document/{source_document_id}")


def _published(meta: dict) -> int | None:
    """작성일 YYYYMMDD. 적재 때 metadata에 published_at(정수)을 넣었으면 그것, 없으면 None."""
    v = meta.get("published_at")
    return int(v) if isinstance(v, int) or (isinstance(v, str) and v.isdigit() and len(v) == 8) else None


def search_rag(query: str, k: int = 5, filters: dict | None = None, doc_kinds: list[str] | None = None,
               min_score: float = MIN_SCORE) -> RequirementSearchResult:
    """질문과 비슷한 RAG 문서 k건 → 근거 풀 항목 + 본문 전체.

    filters  : 조각 metadata 포함 조건 (예: {"school": "건양대학교"})
    doc_kinds: 조각 metadata doc_kind가 이 중 하나인 문서만 (예: ["교육과정", "수업계획서"])
    """
    from engine.rag.store import get_document, search  # DB 패키지는 쓸 때만

    try:
        hits = search(query, k=k, filters=filters, min_score=min_score, chunks_per_doc=1, doc_kinds=doc_kinds)
    except Exception as e:
        raise RagSearchError(f"{type(e).__name__}: {e}") from e
    now = datetime.now(timezone.utc)
    docs, texts = [], {}
    for rank, hit in enumerate(hits, start=1):
        try:
            row = get_document(hit.source_document_id)
        except Exception as e:
            raise RagSearchError(f"{type(e).__name__}: {e}") from e
        if not row or not row.get("content"):
            continue
        meta = row.get("raw_metadata") or {}
        doc_id = rag_doc_id(hit.source_document_id)
        snippet = " ".join((hit.chunks[0].content if hit.chunks else row["content"]).split())[:SNIPPET_CHARS]
        docs.append(EvidenceDoc(
            doc_id=doc_id, provider="rag", kind="document", query=query, rank=rank,
            title=row["title"] or meta.get("file_name") or f"문서 {hit.source_document_id}",
            snippet=snippet,
            url=row.get("source_url") or f"rag://source_document/{hit.source_document_id}",
            domain="rag", source_name=row.get("source_name"), doc_kind=hit.doc_kind,
            published_at=_published(meta),
            fetch_status="ok", fetch_note=f"RAG 유사도 {hit.score:.2f}",
            retrieved_at=now, fetched_at=row.get("fetched_at"),
        ))
        texts[doc_id] = row["content"]
    return RequirementSearchResult(query=query, docs=docs, texts=texts)
