"""4단계(우선순위 판단)·5단계(검증)·결과 생성 입출력 스키마.

- RowGap: 근거 집계 표 한 줄(SkillRow)의 학생 보유 여부. 인호 Gap API(student_id) 또는 1단계 보유 항목으로 판단 (engine/tools/gap.py)
- RankDraft / RankDraftList: 우선순위 LLM 출력. 슬롯 하나의 후보(R번호) 중 고른 것을 중요한 순으로, 행동·이유와 함께
- RankCard: 근거 순위 카드. 코드가 R번호 → 집계 표 줄로 바꾸고 근거 번호(C번호)를 붙인 것. 원문(quote)은 넣지 않는다
- RankIssue: 5단계 검증이 찾은 문제. 판단 오류(→ 4단계 다시)·근거 부족(→ 2단계)·주의(카드에 표시만)
- RankingResult: 4·5단계 결과 전체
- SummaryDraft: 결과 생성 LLM 출력 (총평)
- GuideEvidence / GuideItem / GuideSection / GuideResult: 학생에게 보여 줄 최종 답변 (프론트 JSON)
"""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from engine.schemas.card import Category, CourseType

GapStatus = Literal["보유", "미보유", "수준부족", "매핑되지 않음"]
DeadlineStatus = Literal["모집 중", "마감", "확인 필요"]


class RowGap(BaseModel):
    """집계 표 한 줄의 보유 여부. 키는 SkillRow와 같다(skill_id, 없으면 Skill 이름)."""
    skill_id: int | None
    skill: str
    gap: GapStatus = Field(description="보유 / 미보유 / 수준부족 / 매핑되지 않음(사전에 없고 학생 입력에도 같은 이름이 없음)")
    source: Literal["db", "profile"] = Field(description="db = 인호 Gap API(student_skill), profile = 1단계 보유 항목 대조")
    student_level: str | None = None
    matched: str | None = Field(default=None, description="보유로 판단한 학생 입력 항목 (profile일 때)")


class RankDraft(BaseModel):
    # 칸 순서 = 출력 순서. 번호 → 이유 → 행동 (이유를 먼저 쓰게 해야 행동이 근거와 맞음)
    row: Annotated[str, StringConstraints(pattern=r"^R[0-9]+$")] = Field(description="후보 번호만. 예: R3")
    reason: str = Field(description="이 학생에게 중요한 이유 한두 문장. 후보 줄에 적힌 근거(필수·우대, 문서 역할, 기업)로만. 숫자는 쓰지 않는다")
    action: str = Field(description="학생이 할 일 한 문장. 언제(학년·학기)·무엇을. 예: 3학년 2학기에 ADsP 필기 응시")


class RankDraftList(BaseModel):
    ranks: list[RankDraft] = Field(description="중요한 순서대로")


class RankCard(BaseModel):
    """근거 순위 카드 한 장. 숫자·근거 번호만 담고 원문은 상세 근거 카드(card_ids)에서 찾는다."""
    slot: str
    rank: int = Field(description="슬롯 안 순위 (1부터, 5단계 검증 뒤 다시 매김)")
    skill_id: int | None
    skill: str
    gap: GapStatus
    action: str
    reason: str
    card_ids: list[str] = Field(description="근거 번호 (C번호, 3단계 NumberedCard.card_id)")
    doc_ids: list[str]
    categories: list[Category] = Field(description="근거 카드 분류 (교과목·대외활동 등)")
    doc_count: int
    freq_rank: int = Field(description="같은 슬롯 안 근거 빈도 순위 (문서 수 순). 모델 판단 순위와 비교 실험용")
    by_code: bool = Field(default=False, description="우선순위 LLM이 실패해 코드가 빈도 순으로 채운 카드")
    # 5단계 검증이 채우는 칸
    grade: int | None = Field(default=None, description="교과목: 개설 학년")
    course_type: CourseType | None = None
    deadline: int | None = Field(default=None, description="활동·공모전: 원문에서 찾은 마감일 YYYYMMDD")
    deadline_status: DeadlineStatus | None = None
    notes: list[str] = Field(default_factory=list, description="5단계 주의 사항 (학생 화면에 같이 보여 줌)")


IssueKind = Literal["판단 오류", "근거 부족", "주의"]


class RankIssue(BaseModel):
    slot: str
    kind: IssueKind
    message: str
    row: str | None = Field(default=None, description="문제가 된 후보 번호 (R번호)")


class RankingResult(BaseModel):
    ranks: list[RankCard] = Field(description="검증을 통과한 근거 순위 카드 (슬롯 순, 슬롯 안에서는 순위 순)")
    past: list[RankCard] = Field(default_factory=list, description="[65] 마감된 회차의 활동 — 추천에서 빼고 참고로만 (다시 열릴 수 있음)")
    held: list[RowGap] = Field(description="근거에 나왔지만 학생이 이미 가진 것 (순위에서 뺌)")
    gaps: list[RowGap] = Field(description="집계 표 줄마다 보유 여부")
    gap_source: Literal["db", "profile"] = Field(description="Gap 판단 출처. db 실패 시 profile")
    gap_error: str | None = Field(default=None, description="인호 Gap API를 부르다 난 오류 (있으면 profile로 판단함)")
    issues: list[RankIssue] = Field(default_factory=list, description="5단계 검증 기록 (고친 것 포함)")
    retried: list[str] = Field(default_factory=list, description="판단 오류로 4단계를 다시 부른 슬롯")
    lacking: list[str] = Field(default_factory=list, description="후보가 없어 순위를 못 만든 슬롯 (근거 부족 → 2단계 대상)")
    timings: dict[str, float] = Field(default_factory=dict)


class SummaryDraft(BaseModel):
    summary: str = Field(description="학생에게 하는 총평 3~6문장. 근거를 말할 때 [C3]처럼 근거 번호를 붙인다")


SourceLabel = Literal["건양대 자료", "공식", "웹", "커뮤니티", "검색 요약"]


class GuideEvidence(BaseModel):
    card_id: str
    quote: str
    title: str
    url: str
    source: SourceLabel = Field(description="건양대 자료(RAG) / 공식(정부·학교·기관 도메인) / 웹(그 밖의 웹 문서) / 커뮤니티(블로그·카페) / 검색 요약(본문 못 읽음)")
    published_at: int | None = None


class GuideItem(BaseModel):
    rank: int
    title: str = Field(description="보여 줄 이름. 활동·교과목은 카드 item(대회명·과목명), 나머지는 Skill 이름")
    skill: str = Field(description="집계 표 Skill 이름 (사전에 있으면 사전 이름)")
    gap: GapStatus
    action: str
    reason: str
    kind: str | None = Field(default=None, description="활동·공모전·교과목 슬롯: 대외활동·교내프로그램·교과목")
    grade: int | None = None
    course_type: CourseType | None = None
    deadline: int | None = None
    deadline_status: DeadlineStatus | None = None
    notes: list[str] = Field(default_factory=list)
    evidence: list[GuideEvidence]


class GuideSection(BaseModel):
    slot: str
    items: list[GuideItem]


class GuideResult(BaseModel):
    """학생에게 보여 줄 최종 답변. [64] answer(대화형 글) + sources(글에 나온 근거 번호의 출처)가 채팅 화면용,
    sections는 같은 내용의 카드·링크 화면용 (markdown은 확인용)."""
    job_title: str
    answer: str = Field(default="", description="[64] 학생 질문에 상담하듯 답하는 대화형 글 (마크다운, 근거 번호 [C번호])")
    sources: list[GuideEvidence] = Field(default_factory=list, description="[64] answer에 나온 [C번호]의 출처 (나온 순)")
    answer_by_code: bool = Field(default=False, description="[64] 대화형 답변이 두 번 다 검사에 걸려 코드가 만든 글로 바꿈")
    answer_issues: list[str] = Field(default_factory=list, description="[64] 대화형 답변 검사에서 걸린 것 (다시 쓰기 전 포함)")
    summary: str = Field(description="총평 (대화형 답변의 첫 문단. 근거 번호 [C번호]는 evidence의 card_id)")
    sections: list[GuideSection]
    past: list[GuideItem] = Field(default_factory=list, description="[65] 지난 회차 활동 (참고 — 다음 모집 확인, 추천 아님)")
    held: list[str] = Field(description="근거에 나왔지만 이미 갖춘 것")
    lacking: list[str] = Field(description="근거를 찾지 못한 슬롯")
    summary_by_code: bool = Field(default=False, description="결과 생성 LLM이 실패해 코드가 쓴 총평")
