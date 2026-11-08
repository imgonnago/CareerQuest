"""채팅 대화 입출력 스키마 ([71]). 대화는 서버에 저장하지 않고 프론트가 요청마다 보낸다.

- ChatTurn: 이전 대화 한 차례. 지금 메시지는 넣지 않는다 (StudentInput.question이 지금 메시지)
  assistant 차례의 guide는 서버가 돌려준 GuideResult 그대로. 후속 질문이 이 안의 추천·근거로 답한다
- ContextDraft: 대화 정리 LLM 출력. 지금 메시지를 혼자 읽어도 되는 질문으로 다시 쓰고, 학생이 말한 정보를 뽑는다
"""
from typing import Literal

from pydantic import BaseModel, Field

from engine.schemas.rank import GuideResult


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    guide: GuideResult | None = Field(default=None, description="assistant 차례: /analyze가 돌려준 guide 그대로 (후속 질문의 근거)")


class ContextDraft(BaseModel):
    question: str = Field(description="지금 메시지를 이전 대화 없이도 이해되게 다시 쓴 질문. 학생이 쓴 표현을 그대로 살린다")
    major: str | None = Field(default=None, description="학생이 직접 말한 전공. 학생이 쓴 표기 그대로 (예: 컴공)")
    grade: int | None = Field(default=None, description="학생이 직접 말한 학년")
    desired_job: str | None = Field(default=None, description="학생이 직접 말한 희망 직무. 학생이 쓴 표기 그대로")
    skills: list[str] = Field(default_factory=list, description="학생이 가졌다고 말한 기술. 학생이 쓴 표기 그대로")
    certificates: list[str] = Field(default_factory=list, description="학생이 가졌다고 말한 자격증. 학생이 쓴 표기 그대로")
    projects: list[str] = Field(default_factory=list, description="학생이 해 봤다고 말한 프로젝트")
    activities: list[str] = Field(default_factory=list, description="학생이 참여했다고 말한 활동")
