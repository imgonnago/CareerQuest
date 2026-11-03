"""3단계(근거 집계) 입출력 스키마.

- DocRoleList: 역할 LLM 출력. 근거 문서마다 문서의 성격(채용공고·자격증정보·프로젝트추천 등)
- SkillNameList: 이름 뽑기 LLM 출력. 카드 item(I번호)마다 짧은 Skill 이름 목록 ([21] 여러 개면 나눠서)
- SkillGroupList: 합치기 LLM 출력. 비슷한 이름(N번호) 중 같은 것을 묶고 대표 이름을 붙인다
- SkillEntry: DB Skill 사전(skill·skill_alias 테이블, 인호 담당) 한 항목
- SkillMatchList: 사전 매칭 LLM 출력. 사전에 정확히 없는 Skill(N번호)마다 후보(S번호 = skill_id) 중 하나 또는 "없음"
- NumberedCard: 중복을 지운 상세 근거 카드에 근거 번호(C1, C2 …)와 Skill을 붙인 것
- SkillRow / AggregationResult: 코드가 센 근거 집계 표. 원문(quote)은 넣지 않고 숫자·근거 번호만
  skill_id로 인호 쪽 코드가 학생 보유/미보유(Gap)를 판단한다. 사전에 없는 Skill은 skill_id가 null
"""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field, StringConstraints

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
    # [21] 이름 하나만 받으면 "Git, CLI, Linux"에서 Git만 집계됨 → 목록으로 받음
    names: list[str] = Field(description="항목이 가리키는 자격증·기술·조건의 짧은 이름. 대개 1개, 여러 개 나오면 하나씩 나눠 모두. "
                                         "예: [\"정보처리기사\"], [\"Git\", \"Linux\"]")


class SkillNameList(BaseModel):
    items: list[SkillNameDraft]


class SkillGroup(BaseModel):
    name: str = Field(description="대표 Skill 이름. 짧은 명사. 예: 정보처리기사")
    names: list[Annotated[str, StringConstraints(pattern=r"^N[0-9]+$")]] = Field(
        description="같은 것이라 합칠 이름 번호만. 예: [\"N1\", \"N4\"]")


class SkillGroupList(BaseModel):
    skills: list[SkillGroup]


class SkillEntry(BaseModel):
    skill_id: int
    name: str
    category: str | None = None
    aliases: list[str] = Field(default_factory=list)


class SkillMatch(BaseModel):
    skill: Annotated[str, StringConstraints(pattern=r"^N[0-9]+$")] = Field(description="Skill 번호만. 예: N3")
    choice: Annotated[str, StringConstraints(pattern=r"^(S[0-9]+|없음)$")] = Field(
        description="같은 것을 가리키는 후보 번호. 예: S12. 후보 중 같은 것이 없으면 \"없음\"")


class SkillMatchList(BaseModel):
    matches: list[SkillMatch]


class MatchCheck(BaseModel):
    pair: Annotated[str, StringConstraints(pattern=r"^P[0-9]+$")] = Field(description="짝 번호만. 예: P2")
    ok: Literal["예", "아니오"] = Field(description="사전 Skill을 가진 학생이 근거 Skill 요구를 충족한다고 볼 수 있으면 예")


class MatchCheckList(BaseModel):
    """[44] 사전 매칭 확인 LLM 출력: 매칭 LLM이 고른 (근거 Skill, 사전 Skill) 짝마다 예/아니오."""
    pairs: list[MatchCheck]


class NumberedCard(EvidenceCard):
    card_id: str = Field(description="근거 번호. 예: C3")
    # [21] 카드 하나에 Skill 여러 개. 세 목록은 같은 순서로 짝이 맞는다. 집계에서 뺀 분류(기업정보)는 빈 목록
    skills: list[str] = Field(default_factory=list, description="통일한 Skill 이름 (사전에 있으면 사전 이름)")
    skill_ids: list[int | None] = Field(default_factory=list, description="DB Skill 사전의 skill_id. 사전에 없으면 null")
    skill_names: list[str] = Field(default_factory=list, description="사전 매칭 전 Skill 이름 (이름 뽑기·합치기 LLM 결과)")


class SkillRow(BaseModel):
    """근거 집계 표 한 줄. 수는 모두 고유 문서(doc_id) 기준 — 한 문서의 카드 여러 장은 1건으로 센다."""
    skill_id: int | None = Field(description="DB Skill 사전의 skill_id. 사전에 없거나 사전을 못 읽었으면 null")
    skill: str = Field(description="Skill 이름 (사전에 있으면 사전 이름)")
    slot: str | None = Field(default=None, description="[33] 가이드 슬롯 (카드 분류로 정함, 문서가 가장 많은 슬롯). 기타 분류만 있으면 null")
    doc_count: int
    card_count: int
    fulltext_docs: int = Field(description="본문에서 나온 근거 문서 수 (나머지는 요약)")
    by_category: dict[Category, int] = Field(description="카드 분류별 문서 수. 자격요건=필수, 우대사항=우대")
    by_role: dict[DocRole, int] = Field(description="문서 역할별 문서 수")
    companies: list[str]
    aliases: list[str] = Field(description="이 Skill로 묶인 카드 item 원문")
    names: list[str] = Field(description="사전 매칭 전 Skill 이름 (이름 뽑기·합치기 LLM 결과)")
    card_ids: list[str]
    doc_ids: list[str]

    @computed_field(description="[49] 사전 매칭 결과. 사전에 없는 Skill도 줄을 지우지 않고 '매핑되지 않음'으로 명시 (인호와 합의 2026-11-03)")
    @property
    def mapping(self) -> Literal["매핑됨", "매핑되지 않음"]:
        return "매핑됨" if self.skill_id is not None else "매핑되지 않음"


class AggregationResult(BaseModel):
    job: TargetJob = Field(description="1단계 결과")
    docs: list[EvidenceDoc] = Field(description="근거 풀 전체 (2단계에서 그대로)")
    cards: list[NumberedCard] = Field(description="중복을 지우고 번호·Skill을 붙인 카드")
    doc_roles: dict[str, DocRole] = Field(description="근거 문서 doc_id → 역할")
    skills: list[SkillRow] = Field(description="근거 집계 표 (슬롯 순, 슬롯 안에서는 문서 수 많은 순)")
    skill_dict_loaded: bool = Field(description="DB Skill 사전을 읽었는지. False면 모든 skill_id가 null")
    duplicates: int = Field(description="(doc_id, quote)가 같아 지운 카드 수")
    timings: dict[str, float]
