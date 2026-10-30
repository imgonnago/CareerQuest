"""3단계(근거 집계) 입출력 스키마.

- DocRoleList: 역할 LLM 출력. 근거 문서마다 문서의 성격(채용공고·자격증정보·프로젝트추천 등)
- SkillNameList: 이름 뽑기 LLM 출력. 카드 item(I번호)마다 짧은 Skill 이름
- SkillGroupList: 합치기 LLM 출력. 비슷한 이름(N번호) 중 같은 것을 묶고 대표 이름을 붙인다
- NumberedCard: 중복을 지운 상세 근거 카드에 근거 번호(C1, C2 …)와 Skill을 붙인 것
- SkillRow / AggregationResult: 코드가 센 근거 집계 표. 원문(quote)은 넣지 않고 숫자·근거 번호만
"""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from engine.schemas.card import Category, EvidenceCard
from engine.schemas.evidence import EvidenceDoc
from engine.schemas.job import TargetJob

DocRole = Literal["채용공고", "우대사항정리", "자격증정보", "프로젝트추천", "취업후기", "직무소개", "교육프로그램", "기타"]


class DocRoleDraft(BaseModel):
    doc: Annotated[str, StringConstraints(pattern=r"^D[0-9]+$")] = Field(description="문서 번호만. 예: D3")
    role: DocRole


class DocRoleList(BaseModel):
    docs: list[DocRoleDraft]


class SkillNameDraft(BaseModel):
    item: Annotated[str, StringConstraints(pattern=r"^I[0-9]+$")] = Field(description="항목 번호만. 예: I3")
    name: str = Field(description="항목이 가리키는 자격증·기술·조건의 짧은 이름. 예: 정보처리기사, Spring Boot")


class SkillNameList(BaseModel):
    names: list[SkillNameDraft]


class SkillGroup(BaseModel):
    name: str = Field(description="대표 Skill 이름. 짧은 명사. 예: 정보처리기사")
    names: list[Annotated[str, StringConstraints(pattern=r"^N[0-9]+$")]] = Field(
        description="같은 것이라 합칠 이름 번호만. 예: [\"N1\", \"N4\"]")


class SkillGroupList(BaseModel):
    skills: list[SkillGroup]


class NumberedCard(EvidenceCard):
    card_id: str = Field(description="근거 번호. 예: C3")
    skill: str | None = Field(default=None, description="통일한 Skill 이름. 집계에서 뺀 분류(기업정보)는 null")


class SkillRow(BaseModel):
    """근거 집계 표 한 줄. 수는 모두 고유 문서(doc_id) 기준 — 한 문서의 카드 여러 장은 1건으로 센다."""
    skill: str
    slot: str | None = Field(default=None, description="[33] 가이드 슬롯 (카드 분류로 정함, 문서가 가장 많은 슬롯). 기타 분류만 있으면 null")
    doc_count: int
    card_count: int
    fulltext_docs: int = Field(description="본문에서 나온 근거 문서 수 (나머지는 요약)")
    by_category: dict[Category, int] = Field(description="카드 분류별 문서 수. 자격요건=필수, 우대사항=우대")
    by_role: dict[DocRole, int] = Field(description="문서 역할별 문서 수")
    companies: list[str]
    aliases: list[str] = Field(description="이 Skill로 묶인 카드 item 원문")
    card_ids: list[str]
    doc_ids: list[str]


class AggregationResult(BaseModel):
    job: TargetJob = Field(description="1단계 결과")
    docs: list[EvidenceDoc] = Field(description="근거 풀 전체 (2단계에서 그대로)")
    cards: list[NumberedCard] = Field(description="중복을 지우고 번호·Skill을 붙인 카드")
    doc_roles: dict[str, DocRole] = Field(description="근거 문서 doc_id → 역할")
    skills: list[SkillRow] = Field(description="근거 집계 표 (슬롯 순, 슬롯 안에서는 문서 수 많은 순)")
    duplicates: int = Field(description="(doc_id, quote)가 같아 지운 카드 수")
    timings: dict[str, float]
