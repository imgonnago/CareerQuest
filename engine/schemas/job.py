"""1단계(목표 직무 설정) 입출력 스키마.

- StudentInput: 학생 입력. 프론트(재열)와 형식 합의 전 임시 — README 화면 항목 기준
- TargetJobDraft: LLM이 JSON으로 출력하는 형식
- TargetJob: 코드가 검증을 거쳐 2단계로 넘기는 결과
"""
from typing import Literal

from pydantic import BaseModel, Field


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


class TargetJobDraft(BaseModel):
    job_title: str = Field(description="채용 시장에서 쓰는 일반적인 직무명")
    catalog_job: str | None = Field(default=None, description="표준 직무 목록에서 고른 직무. 목록이 없거나 맞는 게 없으면 null")
    level: Literal["신입", "인턴", "경력"] = "신입"
    interests: list[Interest] = Field(description="학생이 직접 말한 세부 관심 분야·기술")
    companies: list[str] = Field(description="학생이 직접 말한 기업. 없으면 빈 목록")
    search_keywords: list[str] = Field(description="직무 전체 검색용 짧은 키워드 3~5개")


class TargetJob(TargetJobDraft):
    student: StudentInput
    dropped: list[str] = Field(default_factory=list, description="학생 입력에서 찾지 못해 코드가 뺀 항목")
