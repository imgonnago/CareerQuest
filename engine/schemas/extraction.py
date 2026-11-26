"""2단계 근거 추출 결과."""
from pydantic import BaseModel, Field

from engine.schemas import CardDraft, EvidenceCard, EvidenceDoc
from engine.schemas.job import TargetJob


class ExtractionResult(BaseModel):
    job: TargetJob = Field(description="1단계 결과")
    docs: list[EvidenceDoc] = Field(description="근거 풀 전체 (D번호 순)")
    selected: list[str] = Field(description="에이전트 LLM이 읽기로 고른 문서의 doc_id")
    cards: list[EvidenceCard] = Field(description="원문 대조를 통과한 카드")
    rejected: list[CardDraft] = Field(description="원문에서 인용을 찾지 못해 버린 카드")
    queries: list[str]
    query_slots: dict[str, str] = Field(default_factory=dict, description="[37] 검색어 → 그 검색어를 쓴 슬롯")
    slots: dict[str, int] = Field(default_factory=dict, description="[33] 슬롯별 채운 수 (상한 적용 후). 채용 요건·포트폴리오는 고유 근거 문서 수, [57] 항목 슬롯은 서로 다른 과목·활동·자격증 수")
    not_school: int = Field(default=0, description="[33] 교과목 카드인데 건양대 문서가 아니라 버린 수")
    over_cap: int = Field(default=0, description="[43] 문서당 카드 상한(MAX_CARDS_PER_DOC)을 넘어 버린 수")
    rag_error: str | None = Field(default=None, description="[48] RAG 검색 실패 사유 (DB 꺼짐 등). 있으면 검색 API로만 진행함")
    past_grade: int = Field(default=0, description="[54] 학생 학년보다 낮은 학년 교과목이라 버린 카드 수")
    capped: dict[str, int] = Field(default_factory=dict, description="[57][58] 추천 상한(활동·공모전·교과목 8·자격증 4)을 넘어 뺀 항목 수")
    relabeled: int = Field(default=0, description="[57] 건양대 문서가 아닌 교내프로그램 카드를 대외활동으로 고친 수")
    not_course: int = Field(default=0, description="[60] 학교 DB 문서도 아니고 과목 코드도 없어 버린 교과목 카드 수")
    not_activity: int = Field(default=0, description="[60] item에 활동을 나타내는 말이 없어 버린 활동 카드 수")
    to_school: int = Field(default=0, description="[59] 건양대 문서의 대외활동 카드 중 item·quote에 \"건양대\"가 있어 교내프로그램으로 고친 수")
    rounds: list["RoundLog"] = Field(default_factory=list, description="[74] 슬롯 대화별 기록")
    timings: dict[str, float] = Field(description="역할별 소요 시간(초, 합계): agent·search·fetch·read")
    # 5단계 검증(마감일 확인)용 본문. 근거 풀 항목(EvidenceDoc)에는 본문을 넣지 않는 규칙이라 따로 둔다.
    # 카드가 나온 문서만 담고, JSON 출력(model_dump)에는 넣지 않는다
    texts: dict[str, str] = Field(default_factory=dict, exclude=True, description="doc_id → 본문 (카드가 나온 문서만)")


class RoundLog(BaseModel):
    """[74] 슬롯 대화 하나의 기록."""
    slot: str
    queries: list[str]
    selected: list[str] = Field(description="이 대화에서 읽은 문서의 doc_id")
    tool_calls: list[dict] = Field(default_factory=list, description="LLM이 부른 도구 순서: {turn, tool, args, result}")
    turns: int = Field(default=0, description="LLM 턴 수")
    stop: str = Field(default="", description="끝난 이유: filled(슬롯 참) / agent(LLM이 끝냄) / turns / time / idle / error")
    cards: int = Field(description="이 대화에서 새로 통과한 카드 수")
    slots: dict[str, int] = Field(description="[33] 대화가 끝났을 때 슬롯별 채운 수")
    seconds: float


ExtractionResult.model_rebuild()
