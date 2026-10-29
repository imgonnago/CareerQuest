"""요구사항 검색 도구: 네이버 + 카카오 검색 → 근거 풀 후보 → (카카오만) 본문 수집.

합격 후기 · 우대사항 · 자격증 정보처럼 RAG(건양대 자료)에 없는 근거를 보충할 때 쓴다.

흐름
1. collect_candidates: 소스별 검색 → 카카오 소스끼리 번갈아 섞은 뒤 네이버를 뒤에 붙임 → doc_id 중복 제거
2. (2단계 파이프라인) 계획 LLM이 title + snippet 목록을 보고 읽을 문서를 고르는 자리
3. fetch_texts: 앞에서부터 pending 문서만 max_fetch개까지 본문 수집

본문은 EvidenceDoc에 넣지 않고 texts[doc_id]로 따로 돌려준다.
"""
from itertools import zip_longest

import requests
from pydantic import BaseModel, Field

from engine.schemas import EvidenceDoc
from engine.tools.crawl import fetch_fulltext
from engine.tools.kakao import search_kakao
from engine.tools.naver import search_naver

# 소스 이름 → (provider, kind)
SOURCES = {
    "naver_blog": ("naver", "blog"),
    "naver_cafe": ("naver", "cafe"),
    "naver_web": ("naver", "web"),
    "kakao_blog": ("kakao", "blog"),   # 티스토리만 남음
    "kakao_cafe": ("kakao", "cafe"),
    "kakao_web": ("kakao", "web"),
}
DEFAULT_SOURCES = ("kakao_blog", "kakao_web", "naver_blog", "naver_cafe")
# 결과 순서. 카카오는 본문까지 읽을 수 있어 1순위, 네이버는 요약만이라 보충용
PROVIDER_ORDER = ("kakao", "naver")


class RequirementSearchResult(BaseModel):
    query: str
    docs: list[EvidenceDoc] = Field(description="근거 풀 항목. 앞쪽일수록 우선")
    texts: dict[str, str] = Field(default_factory=dict, description="doc_id → 수집한 본문")
    errors: list[str] = Field(default_factory=list, description="실패한 소스와 사유")


def _search(source: str, query: str, size: int) -> list[EvidenceDoc]:
    provider, kind = SOURCES[source]
    if provider == "naver":
        return search_naver(query, kind=kind, display=size)
    # 카카오 blog는 티스토리만 남기므로 걸러질 몫까지 최대(50)로 받는다
    docs, _ = search_kakao(query, kind=kind, size=50 if kind == "blog" else size)
    return docs[:size]


def collect_candidates(
    query: str,
    sources: tuple[str, ...] = DEFAULT_SOURCES,
    per_source: int = 20,
) -> tuple[list[EvidenceDoc], list[str]]:
    """소스별 검색 결과를 섞고 중복을 뺀다. 카카오가 앞, 네이버가 뒤 (같은 제공자 안에서는 순위대로 번갈아).

    반환: (후보 목록, 오류 메시지 목록). 한 소스가 실패해도 나머지는 돌려준다.
    """
    by_provider: dict[str, list[list[EvidenceDoc]]] = {p: [] for p in PROVIDER_ORDER}
    errors = []
    for source in sources:
        try:
            by_provider[SOURCES[source][0]].append(_search(source, query, per_source))
        except requests.RequestException as e:
            errors.append(f"{source}: {type(e).__name__}")

    # 카카오(본문 수집 가능)를 전부 앞에, 네이버(요약만)는 뒤에 보충으로
    seen, docs = set(), []
    for provider in PROVIDER_ORDER:
        for row in zip_longest(*by_provider[provider]):
            for doc in row:
                if doc is not None and doc.doc_id not in seen:
                    seen.add(doc.doc_id)
                    docs.append(doc)
    return docs, errors


def fetch_texts(docs: list[EvidenceDoc], max_fetch: int) -> tuple[list[EvidenceDoc], dict[str, str]]:
    """앞에서부터 pending 문서만 max_fetch개까지 본문 수집을 시도한다.

    시도한 문서는 상태(ok/failed/disallowed)가 갱신된 것으로 바뀐다.
    """
    out, texts, tried = [], {}, 0
    for doc in docs:
        if doc.fetch_status == "pending" and tried < max_fetch:
            tried += 1
            doc, text = fetch_fulltext(doc)
            if text is not None:
                texts[doc.doc_id] = text
        out.append(doc)
    return out, texts


def search_requirements(
    query: str,
    sources: tuple[str, ...] = DEFAULT_SOURCES,
    per_source: int = 20,
    max_fetch: int = 5,
) -> RequirementSearchResult:
    """요구사항 검색 도구 본체. max_fetch=0이면 본문 수집 없이 요약만."""
    docs, errors = collect_candidates(query, sources, per_source)
    docs, texts = fetch_texts(docs, max_fetch)
    return RequirementSearchResult(query=query, docs=docs, texts=texts, errors=errors)


# LLM 도구 호출용 정의 (Ollama · OpenAI 공통 function 형식).
# 본문을 읽을 문서는 LLM이 목록을 보고 따로 고르므로(2단계 계획 LLM) 인자는 검색어만 받는다
TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "search_requirements",
        "description": "네이버·카카오 검색으로 기업 및 직무 요구사항, 우대사항, 합격 후기, 자격증 정보를 찾는다. "
                       "건양대 자료(RAG)에 근거가 없거나 부족할 때 사용한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어. 예: '데이터 분석가 신입 우대사항'"},
            },
            "required": ["query"],
        },
    },
}
