"""입력 판단(0단계)·정보 질문·진로 고민 입출력 스키마 ([66]).

- RouteDraft: 입력 판단 LLM 출력. 학생 입력을 취업 가이드 / 정보 질문 / 진로 고민 중 하나로
- CrisisCheck: 위기 판단 LLM 출력 ([70])
- RouteDecision: 코드가 확정한 경로 (폼 칸만 있으면 LLM 없이 취업 가이드, 위기면 진로 고민 + crisis)
- InfoPlan: 정보 질문의 검색어 (계획 LLM)
"""
from typing import Literal

from pydantic import BaseModel, Field

RouteKind = Literal["취업 가이드", "정보 질문", "진로 고민", "후속 질문", "일반 대화"]  # 후속 질문은 전용 판단(FollowupCheck)이 정한다


class RouteDraft(BaseModel):
    # 칸 순서 = 출력 순서. 이유를 먼저 쓰게 해야 경로가 입력 내용과 맞음
    reason: str = Field(description="판단 이유 한 문장")
    kind: Literal["취업 가이드", "정보 질문", "진로 고민", "일반 대화"]
    # [66] 1단계가 아무 정보 없이도 직무를 지어내서("취업 준비 뭐부터 해야 돼?" → AI 엔지니어) 직무명만으로는 판단 못 함
    has_goal: bool = Field(description="학생이 희망 직무·관심 분야·전공·가고 싶은 기업 중 하나라도 직접 말했으면 true")
    question: str = Field(default="", description="정보 질문일 때: 찾아야 할 것을 한 문장으로. 예: 정보처리기사 2027년 실기 시험 일정")


class CrisisCheck(BaseModel):
    # 칸 순서 = 출력 순서. 이유를 먼저 쓰게 한다
    reason: str = Field(description="판단 이유 한 문장")
    crisis: bool = Field(description="자살·자해 생각, 죽음을 바라는 마음, 삶을 끝내려는 준비나 작별의 신호가 있으면 true. 애매하면 true")


class CrisisEmpathy(BaseModel):
    """[71] 위기 공감 문장은 LLM이 쓰지 않고 코드가 가진 문장 중 고른다 (직접 쓰면 "내일을 기다리시는 마음"처럼 뜻이 뒤집힘)."""
    kind: Literal["고통", "짐이_된다는_생각", "작별", "방법을_찾음", "다른_질문과_섞임"]


class FollowupCheck(BaseModel):
    # 칸 순서 = 출력 순서
    reason: str = Field(description="판단 이유 한 문장")
    followup: bool = Field(description="이전 답변 자료에 있는 추천·내용을 더 묻고, 자료 안에 답이 있으면 true")


class RouteDecision(BaseModel):
    kind: RouteKind
    reason: str
    question: str = ""
    has_goal: bool = Field(default=True, description="학생이 직무·관심 분야·전공·기업 중 하나라도 말함. 취업 가이드인데 false면 1단계 전에 되묻는다")
    crisis: bool = Field(default=False, description="[70] 위기 판단 LLM 또는 명백한 표현 정규식이 위기로 봄 — 답변에 위기 상담 번호가 반드시 들어감")
    by_code: bool = Field(default=False, description="LLM 없이 코드가 정함 (폼 칸만 있음·명백한 위기 표현·LLM 실패)")


class InfoPlan(BaseModel):
    queries: list[str] = Field(description="검색어 1~3개. 2~5단어. 예: [\"정보처리기사 실기 일정\", \"정보처리기사 시험 일정 2027\"]")
