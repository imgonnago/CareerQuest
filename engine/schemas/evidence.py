"""근거 풀(evidence pool) 항목 스키마.

검색 API 결과 1건 = EvidenceDoc 1개.
- 네이버: 요약(snippet)만 사용 → 본문 수집 안 함
- 카카오: 요약으로 시작, 본문 수집에 성공하면 text가 채워져 fulltext 근거가 됨

상세 근거 카드는 doc_id + 인용문으로 이 항목을 참조하고,
(5) 검증은 인용문이 evidence_text 안에 실제로 있는지 대조한다.
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

    text: str | None = Field(default=None, description="수집한 본문 전체. 수집 전·실패 시 None")
    fetch_status: FetchStatus
    fetch_note: str | None = Field(default=None, description="수집 실패·차단 사유")
    retrieved_at: datetime
    fetched_at: datetime | None = None

    @computed_field
    @property
    def evidence_level(self) -> Literal["snippet", "fulltext"]:
        return "fulltext" if self.text else "snippet"

    @property
    def evidence_text(self) -> str:
        """카드 인용·검증의 기준 문자열."""
        return self.text or self.snippet
