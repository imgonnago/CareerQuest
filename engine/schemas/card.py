"""2단계(근거 추출) 입출력 스키마: 문서 선택 + 상세 근거 카드.

근거 풀(EvidenceDoc, 원문)에서 LLM이 근거가 되는 문장을 골라 만든 카드.
- CardDraft: LLM이 JSON으로 출력하는 형식. 문서는 대화 안 번호(D3 등)로 가리킨다
- EvidenceCard: 코드가 D번호 → doc_id로 바꾸고 인용 문장을 원문과 대조해 통과한 카드
"""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

# [33] 가이드 슬롯(교과목·교내 프로그램·공모전·포트폴리오)을 채우려고 교과목·교내프로그램·대외활동·프로젝트 추가 (2026-10-31)
Category = Literal["자격요건", "우대사항", "기술스택", "자격증", "교과목", "교내프로그램", "대외활동", "프로젝트", "기업정보", "기타"]


class CardDraft(BaseModel):
    doc: Annotated[str, StringConstraints(pattern=r"^D[0-9]+$")] = Field(description="근거 문서 번호만. 예: D3")
    category: Category
    company: str | None = Field(default=None, description="문서에 나온 기업명. 없으면 null")
    # [27] "핵심 항목 한 줄" → 공고 직무명·기업명을 쓰는 경우가 있어 짧은 요구사항 이름으로 좁힘
    item: str = Field(description="quote가 요구하는 자격증·기술·조건 또는 과목·프로그램·공모전·프로젝트의 짧은 이름. 공고 직무명·기업명 금지. 예: 정보처리기사, Spring Boot, TOEIC Speaking 120, 로봇공학개론")
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


class SearchPlan(BaseModel):
    """[37] 2단계 계획 LLM 출력: 가이드 슬롯 하나의 검색어. 검색은 코드가 실행한다."""
    queries: list[str] = Field(description="검색어. \"대상 + 찾는 영역\" 2~5단어. 예: [\"삼성전자 채용 공고\", \"AI 자격증\"]")
