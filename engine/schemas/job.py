"""1단계(목표 직무 설정) 입출력 스키마.

- StudentInput: 학생 입력. 프론트(재열)와 형식 합의 전 임시 — README 화면 항목 기준
- TargetJobDraft: LLM이 JSON으로 출력하는 형식
- TargetJob: 코드가 검증을 거쳐 2단계로 넘기는 결과
- InterestCheck: 관심 분야 확인 LLM 출력 (관심 / 경험)
- Company / Held: 기업(원문·공식명), 보유 항목(원문·표준 이름). 둘 다 원문을 학생 입력과 대조한다
"""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints


class StudentInput(BaseModel):
    major: str | None = Field(default=None, description="전공")
    grade: int | None = Field(default=None, description="학년")
    desired_job: str | None = Field(default=None, description="희망 직무 (학생이 쓴 그대로)")
    skills: list[str] = Field(default_factory=list, description="보유 기술")
    certificates: list[str] = Field(default_factory=list, description="보유 자격증")
    projects: list[str] = Field(default_factory=list, description="진행한 프로젝트")
    activities: list[str] = Field(default_factory=list, description="참여한 비교과 활동")
    question: str | None = Field(default=None, description="학생이 자유롭게 쓴 질문·고민")


class Interest(BaseModel):
    original: str = Field(description="학생이 쓴 표현 그대로 (오타 포함)")
    name: str = Field(description="바르게 고친 표준 용어")
    keywords: list[str] = Field(description="검색용 짧은 키워드 2~4개 (한국어·영어)")


class Company(BaseModel):
    """학생이 말한 기업. 학생 표기와 공식명이 다를 수 있어 나눠 받는다 ([25]: "현대 로보틱스" → HD현대로보틱스)."""
    original: str = Field(description="학생이 쓴 기업명 그대로 (띄어쓰기·약칭 포함)")
    name: str = Field(description="공식 기업명. 확실히 모르면 지어내지 말고 학생 표기를 그대로 쓴다")
    keywords: list[str] = Field(description="검색용 짧은 키워드 1~3개. 예: [\"HD현대로보틱스\", \"HD현대로보틱스 채용\"]")


class Held(BaseModel):
    """학생이 이미 가진 것(기술·자격증·프로젝트). 관심 분야(앞으로 할 것)와 섞이지 않게 따로 받는다 ([24])."""
    original: str = Field(description="학생이 쓴 표현 그대로")
    name: str = Field(description="표준 이름. 예: 파이썬 → Python. 프로젝트는 짧은 제목")


class TargetJobDraft(BaseModel):
    job_title: str = Field(description="채용 시장에서 쓰는 일반적인 직무명")
    catalog_job: str | None = Field(default=None, description="표준 직무 목록에서 고른 직무. 목록이 없거나 맞는 게 없으면 null")
    level: Literal["신입", "인턴", "경력"] = "신입"
    interests: list[Interest] = Field(description="학생이 앞으로 하고 싶다고 말한 세부 관심 분야·기술 (이미 해 본 것은 projects)")
    # [25] 기업명 문자열 목록 → 원문·공식명·검색 키워드로 나눔
    companies: list[Company] = Field(description="학생이 직접 말한 기업. 없으면 빈 목록")
    # [24] 자유 질문에만 적힌 보유 항목도 정리하도록 추가 (Gap 계산용). 폼 칸(StudentInput)은 코드가 합친다
    skills: list[Held] = Field(description="학생이 할 수 있다고 말한 기술·언어. 없으면 빈 목록")
    certificates: list[Held] = Field(description="학생이 이미 딴 자격증. 없으면 빈 목록")
    projects: list[Held] = Field(description="학생이 이미 해 본 프로젝트·공부. 없으면 빈 목록")
    search_keywords: list[str] = Field(description="직무 전체 검색용 짧은 키워드 3~5개")


class InterestStatus(BaseModel):
    item: Annotated[str, StringConstraints(pattern=r"^K[0-9]+$")] = Field(description="항목 번호만. 예: K1")
    status: Literal["관심", "경험"] = Field(description="관심: 앞으로 하고 싶다고 말함 / 경험: 이미 해 봤다·할 수 있다고 말함")


class InterestCheck(BaseModel):
    """[24] 관심 분야 확인 LLM 출력: 직무 설정 LLM이 interests에 넣은 항목이 관심인지 이미 한 경험인지."""
    items: list[InterestStatus]


class TargetJob(TargetJobDraft):
    student: StudentInput
    dropped: list[str] = Field(default_factory=list, description="학생 입력에서 찾지 못해 코드가 뺀 항목")
