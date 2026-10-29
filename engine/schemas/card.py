"""2단계(근거 추출) 입출력 스키마: 문서 선택 + 상세 근거 카드.

근거 풀(EvidenceDoc, 원문)에서 LLM이 근거가 되는 문장을 골라 만든 카드.
- CardDraft: LLM이 JSON으로 출력하는 형식. 문서는 대화 안 번호(D3 등)로 가리킨다
- EvidenceCard: 코드가 D번호 → doc_id로 바꾸고 인용 문장을 원문과 대조해 통과한 카드
"""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Category = Literal["자격증", "기술스택", "우대사항", "자격요건", "기업정보", "기타"]


class CardDraft(BaseModel):
    doc: Annotated[str, StringConstraints(pattern=r"^D[0-9]+$")] = Field(description="근거 문서 번호만. 예: D3")
    category: Category
    company: str | None = Field(default=None, description="문서에 나온 기업명. 없으면 null")
    item: str = Field(description="핵심 항목 한 줄. 예: 정보처리기사, Spring Boot 경험 우대")
    quote: str = Field(description="문서에 적힌 문장을 바꾸지 않고 그대로 옮긴 인용")


class CardDraftList(BaseModel):
    cards: list[CardDraft]


class EvidenceCard(BaseModel):
    doc_id: str = Field(description="근거 풀 EvidenceDoc.doc_id")
    category: Category
    company: str | None = None
    item: str
    quote: str
    evidence_level: Literal["snippet", "fulltext"] = Field(description="인용이 본문에서 왔는지 요약에서 왔는지")


class DocSelection(BaseModel):
    """2단계 계획 LLM 출력: 검색 목록에서 본문까지 읽을 문서를 고른다."""
    selected: list[Annotated[str, StringConstraints(pattern=r"^D[0-9]+$")]] = Field(
        description="읽을 문서 번호만. 관련 높은 순. 예: [\"D3\", \"D7\"]")
