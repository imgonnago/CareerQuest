"""근거 풀(evidence pool) 항목 스키마.

검색 API 결과 1건 = EvidenceDoc 1개. 본문은 담지 않는다.
- 네이버: 요약(snippet)만 사용 → 본문 수집 안 함
- 카카오: 본문 수집에 성공하면 fulltext 근거가 됨

본문은 LLM이 전체를 읽고, 근거가 되는 내용은 상세 근거 카드에 담는다.
상세 근거 카드는 doc_id로 이 항목을 참조한다.
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, computed_field

Provider = Literal["naver", "kakao"]
Kind = Literal["blog", "cafe", "web"]
FetchStatus = Literal[
    "skipped",      # 본문 수집 대상 아님 (네이버)
    "pending",      # 아직 수집 안 함
    "ok",           # 본문 수집 성공
    "link_only",    # 정책상 링크만 제공하는 도메인 (채용 플랫폼 등)
    "disallowed",   # robots.txt가 막음
    "failed",       # 네트워크 오류, 본문 추출 실패 등
]

#2단계 근거 추출 단계 중 첫번째 과정인 근거 풀 항목 스키마 정의.
#본문은 담지 않고, 검색 API 결과 1건을 EvidenceDoc 1개로 표현
#
class EvidenceDoc(BaseModel):
    doc_id: str = Field(description="정규화한 URL의 해시. 같은 문서는 같은 id")
    provider: Provider
    kind: Kind
    query: str = Field(description="이 문서를 찾은 검색어")
    rank: int = Field(description="검색 결과 내 순위 (1부터)")

    title: str = Field(description="HTML 태그 제거·엔티티 디코딩된 제목")
    snippet: str = Field(description="검색 API가 준 요약 (잘린 발췌)")
    url: str
    domain: str
    source_name: str | None = Field(default=None, description="블로그·카페 이름")
    published_at: int | None = Field(default=None, description="작성일 YYYYMMDD. 없으면 None")

    fetch_status: FetchStatus
    fetch_note: str | None = Field(default=None, description="수집 실패·차단 사유")
    retrieved_at: datetime
    fetched_at: datetime | None = None

    @computed_field
    @property
    def evidence_level(self) -> Literal["snippet", "fulltext"]:
        return "fulltext" if self.fetch_status == "ok" else "snippet"
