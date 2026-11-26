"""0단계 입력 판단 ([66]): 지금 메시지를 어느 경로로 보낼지 정한다. 답변은 경로마다 따로 (engine/pipeline/routes/).

순서 (run.py가 부름)
1. 위기 판단 (check_crisis) ([70] LLM 주도, [71] 이전 학생 말까지 봄). 대화 정리보다 먼저 지금 메시지 원문으로 한다
   - 코드: 명백한 위기 표현(CRISIS_CERTAIN — 죽고 싶다·자살하고 싶다·방법을 가리키는 말 등)이면 LLM 판단과 상관없이 진로 고민 + crisis (안전망)
   - 위기 판단 LLM(새 대화, CrisisCheck): 돌려 말한 표현까지 판단. 실패하면 넓은 정규식(CRISIS_FALLBACK)으로
2. 대화 정리 (resolve_history) [71]: 이전 대화가 있으면 LLM이 혼자 읽어도 되는 질문과 학생 정보를 만들고, 코드가 학생 말에 실제로 있는지 대조한다
3. 후속 판단 (check_followup) [71]: 이전 추천·근거가 있으면 전용 LLM이 자료 안에 답이 있는지 본다 → routes/followup.py
   [86] 출처를 묻는 말(SOURCE_QUESTION)은 코드가 바로 후속 질문으로 보낸다
4. 입력 판단 (route_input)
   - 코드: 질문 칸이 비어 있으면(폼 칸만) LLM 없이 취업 가이드
   - 입력 판단 LLM(새 대화): 취업 가이드 / 정보 질문 / 진로 고민 / 일반 대화 (RouteDraft). 실패하면 취업 가이드
5. 위기 안내 뒤 흐름 유지 (keep_crisis) [71]: 위기 안내 뒤에는 위기 흐름이 기본. 새 질문이 분명할 때만 그 질문에 답한다

경로별 답변
- 취업 가이드 → run.py가 1~5단계. 입력 판단 LLM이 has_goal=false이고 폼 칸도 비었으면 1단계 전에 routes/clarify.clarify_answer로 되묻는다
  (1단계는 정보가 없어도 직무를 지어냄). 1단계 결과가 비어도(needs_more_info) 2단계 전에 되묻는다
- 정보 질문 → routes/info.py, 진로 고민·위기 → routes/counsel.py, 일반 대화 → routes/chat.py, 후속 질문 → routes/followup.py
"""
import json
import re

from engine.helpers.checks import NUMBER
from engine.helpers.history import HISTORY_TURNS, SOURCE_QUESTION, crisis_guided, render_history, render_prior, user_turns
from engine.helpers.student import render_student
from engine.llm import LLMConfig, chat
from engine.prompts.stage0_route import (
    CONTEXT_REQUEST, CONTEXT_SYSTEM, CRISIS_REQUEST, CRISIS_SYSTEM, FOLLOWUP_CHECK_REQUEST, FOLLOWUP_CHECK_SYSTEM,
    ROUTE_REQUEST, ROUTE_SYSTEM,
)
from engine.schemas.chat import ChatTurn, ContextDraft
from engine.schemas.job import StudentInput, TargetJob
from engine.schemas.rank import GuideResult
from engine.schemas.route import CrisisCheck, FollowupCheck, RouteDecision, RouteDraft

ROUTER = LLMConfig(temperature=0)
CRISIS_JUDGE = LLMConfig(temperature=0)
CONTEXT_WRITER = LLMConfig(temperature=0)

# [70] 스스로에 대해 말하는 명백한 표현만. LLM이 놓쳐도 이것은 잡는다. 주제로만 쓰인 말("자살 예방 상담사")은 걸리지 않게 좁혔다
CRISIS_CERTAIN = re.compile(r"죽고\s*싶|죽어\s*버리고\s*싶|자살\s*(하고\s*싶|할\s*(까|래|거)|충동|생각)|자해\s*(를\s*)?(하고\s*싶|했|해요|멈)|살기\s*싫|살고\s*싶지\s*않|목숨을\s*끊|번개탄|투신|목을\s*매")
# 위기 판단 LLM이 실패했을 때만 쓰는 넓은 표현. 오탐이 많지만("과제 끝내고 싶어요") 판단이 없을 때는 놓치는 것보다 낫다
CRISIS_FALLBACK = re.compile(r"죽고\s*싶|죽어\s*버리|자살|자해|목숨|살기\s*싫|살고\s*싶지\s*않|사라지고\s*싶|극단적\s*선택|끝내고\s*싶|없어지고\s*싶")
NEW_QUESTION = re.compile(r"\?|뭐|무엇|언제|어떻게|어디|몇|왜|알려|추천|준비")  # 위기 안내 뒤 새 질문임이 분명한 말
GENERIC_TITLES = {"", "신입", "인턴", "경력", "미정", "없음"}


def is_crisis(text: str, log=print) -> tuple[bool, str, bool]:
    """[70] 위기 판단. 반환: (위기인지, 이유, 코드가 판단했는지). 명백한 표현은 코드, 나머지는 LLM, LLM이 실패하면 넓은 정규식."""
    if not text.strip():
        return False, "", True
    if CRISIS_CERTAIN.search(text):
        return True, "명백한 위기 표현", True
    try:
        msg = chat([{"role": "system", "content": CRISIS_SYSTEM},
                    {"role": "user", "content": CRISIS_REQUEST.format(text=text)}],
                   CRISIS_JUDGE, format=CrisisCheck.model_json_schema())
        check = CrisisCheck.model_validate(json.loads(msg["content"]))
        return check.crisis, check.reason, False
    except Exception as e:
        log(f"위기 판단 실패 → 정규식으로: {type(e).__name__}")
        return bool(CRISIS_FALLBACK.search(text)), "위기 판단 실패, 정규식으로 판단", True


def check_crisis(student: StudentInput, log=print, history: list[ChatTurn] | None = None) -> RouteDecision | None:
    """위기면 진로 고민 경로, 아니면 None. 지금 메시지 원문으로 판단한다 (대화 정리가 실패해도 영향받지 않게).
    [71] 이전 대화는 판단에 넣지 않는다. 넣으면 "전공 수업이 이해가 안 돼요"도 앞선 고민에 묶여 위기로 잡힌다.
    위기 안내 뒤의 이어지는 말은 keep_crisis가 따로 본다."""
    text = " ".join(filter(None, [student.question, student.desired_job]))
    crisis, reason, by_code = is_crisis(text, log)
    return RouteDecision(kind="진로 고민", reason=reason, crisis=True, by_code=by_code) if crisis else None


def keep_crisis(current: str | None, route: RouteDecision, history: list[ChatTurn] | None) -> RouteDecision:
    """[71] 위기 안내 뒤에는 위기 흐름이 기본이다. "네 혼자 있어요"가 일반 상담으로 넘어가면 "혼자 있는 건 나쁜 일이 아니라…" 같은 답이 나간다.
    정보·취업 준비·후속을 묻는 말이 분명할 때만 그 질문에 답한다 (run.py가 SAFETY_REMINDER를 붙임)."""
    if not crisis_guided(history):
        return route
    if route.kind != "진로 고민" and NEW_QUESTION.search(current or ""):
        return route
    return RouteDecision(kind="진로 고민", reason="위기 안내 뒤 이어지는 말", crisis=True)


def _keeps_message(question: str, current: str) -> bool:
    """다시 쓴 질문에 지금 메시지의 말이 남아 있는지 (글자 2개씩 비교). 없으면 새 질문이 앞 대화로 바뀐 것이다. "네"처럼 짧은 말은 보지 않는다."""
    text = re.sub(r"\s+", "", current)
    compact = re.sub(r"\s+", "", question)
    return len(text) < 2 or any(text[i:i + 2] in compact for i in range(len(text) - 1))


def _in_user_words(value: str, words: str) -> bool:
    return re.sub(r"\s+", "", value).lower() in words


def resolve_history(student: StudentInput, history: list[ChatTurn] | None, log=print) -> StudentInput:
    """[71] 이전 대화 + 지금 메시지 → 혼자 읽어도 되는 질문과 학생 정보. 폼 칸 값이 있으면 폼 칸이 우선.
    학생 말에 없는 정보는 버리고, LLM이 실패하거나 없는 숫자를 지어내면 학생 말을 이어 붙인 것으로 진행한다."""
    current = (student.question or "").strip()
    if not history or not current:
        return student
    users = user_turns(history)
    fallback = " ".join([*users[-HISTORY_TURNS:], current])
    words = re.sub(r"\s+", "", fallback).lower()
    try:
        msg = chat([{"role": "system", "content": CONTEXT_SYSTEM},
                    {"role": "user", "content": CONTEXT_REQUEST.format(history=render_history(history), question=current)}],
                   CONTEXT_WRITER, format=ContextDraft.model_json_schema())
        d = ContextDraft.model_validate(json.loads(msg["content"]))
    except Exception as e:
        log(f"대화 정리 실패 → 학생 말을 이어 붙임: {type(e).__name__}")
        return student.model_copy(update={"question": fallback})
    context = " ".join([fallback, *(t.content for t in history)])
    question = d.question.strip()
    said_by_helper = any(question and question in t.content for t in history if t.role == "assistant")  # 도우미 말을 옮겨 적음
    if question and not _keeps_message(question, current):
        log("대화 정리가 지금 메시지를 바꿈 → 지금 메시지를 그대로 씀")
        question = current
    if not question or said_by_helper or set(NUMBER.findall(question)) - set(NUMBER.findall(context)):
        log("대화 정리 질문을 쓰지 않음 → 학생 말을 이어 붙임")
        question = fallback

    def keep(values: list[str], form: list[str]) -> list[str]:
        return list(dict.fromkeys([*form, *(v.strip() for v in values if v.strip() and _in_user_words(v, words))]))

    major = d.major if d.major and _in_user_words(d.major, words) else None
    job = d.desired_job if d.desired_job and _in_user_words(d.desired_job, words) else None
    said_grade = re.search(r"([1-6])\s*학년", fallback)  # 학년은 표현이 정해져 있어 코드로 읽는다
    grade = int(said_grade.group(1)) if said_grade else None
    return student.model_copy(update={
        "question": question, "major": student.major or major, "desired_job": student.desired_job or job,
        "grade": student.grade or grade, "skills": keep(d.skills, student.skills),
        "certificates": keep(d.certificates, student.certificates), "projects": keep(d.projects, student.projects),
        "activities": keep(d.activities, student.activities)})


def since_last_guide(student: StudentInput, history: list[ChatTurn] | None) -> StudentInput:
    """[71] 취업 가이드로 갈 때: 마지막 추천 이후 학생이 한 말을 질문에 함께 준다. 되묻기가 두 번 이어지면 앞서 말한 전공이 사라지기 때문이다.
    그 전의 말은 넣지 않는다 (목표를 바꾼 학생에게 이전 목표가 섞이지 않게)."""
    said = []
    for turn in reversed(history or []):
        if turn.role == "assistant" and turn.guide and (turn.guide.sections or turn.guide.sources):
            break
        if turn.role == "user":
            said.insert(0, turn.content)
    return student.model_copy(update={"question": f"{student.question} (앞서 학생이 한 말: {' / '.join(said)})"}) if said else student


def check_followup(student: StudentInput, prior: GuideResult | None, log=print) -> RouteDecision | None:
    """[71] 이전 답변 자료로 답할 후속 질문이면 그 경로, 아니면 None. 세 경로 판단과 같이 시키면 전부 정보 질문으로 가서 새 대화로 따로 묻는다."""
    question = (student.question or "").strip()
    if prior is None or not question:
        return None
    if SOURCE_QUESTION.search(question):
        return RouteDecision(kind="후속 질문", reason="이전 답변의 출처를 물음", question=question, by_code=True)
    try:
        msg = chat([{"role": "system", "content": FOLLOWUP_CHECK_SYSTEM},
                    {"role": "user", "content": FOLLOWUP_CHECK_REQUEST.format(prior=render_prior(prior), question=question)}],
                   ROUTER, format=FollowupCheck.model_json_schema())
        d = FollowupCheck.model_validate(json.loads(msg["content"]))
    except Exception as e:
        log(f"후속 판단 실패 → 일반 판단으로: {type(e).__name__}")
        return None
    return RouteDecision(kind="후속 질문", reason=d.reason, question=question) if d.followup else None


def route_input(student: StudentInput, log=print, question_only: bool = False) -> RouteDecision:
    """위기 판단(check_crisis)은 먼저 끝났다고 본다.
    [71] question_only: 경로는 지금 질문만 보고 정한다. 대화에서 모은 전공·기술까지 보면 "토익 스피킹 몇 점?"도 "자기 상황을 말하고 묻는 것"으로 판단해 취업 가이드로 간다."""
    if not (student.question or "").strip():
        return RouteDecision(kind="취업 가이드", reason="질문 없이 폼 칸만 입력", by_code=True)
    messages = [{"role": "system", "content": ROUTE_SYSTEM},
                {"role": "user", "content": ROUTE_REQUEST.format(
                    student=f"학생 입력\n- 학생 질문: {student.question}" if question_only
                    else render_student(student.model_copy(update={"documents": []})))}]
    try:
        d = RouteDraft.model_validate(json.loads(chat(messages, ROUTER, format=RouteDraft.model_json_schema())["content"]))
    except Exception as e:
        log(f"입력 판단 실패 → 취업 가이드로: {type(e).__name__}")
        return RouteDecision(kind="취업 가이드", reason="입력 판단 실패", by_code=True)
    question = d.question.strip() or (student.question or "").strip()
    has_goal = d.has_goal or bool(student.desired_job or student.major)  # 폼 칸에 적었으면 목표가 있는 것
    return RouteDecision(kind=d.kind, reason=d.reason, question=question if d.kind == "정보 질문" else "", has_goal=has_goal)


def needs_more_info(job: TargetJob) -> bool:
    """1단계 결과에 2단계 검색을 시작할 목표가 없는지: 희망 직무·전공·관심 분야·기업·보유 항목이 모두 없고 직무명도 일반적."""
    s = job.student
    has = s.desired_job or s.major or job.interests or job.companies or job.skills or job.certificates or job.projects
    return not has and job.job_title.strip() in GENERIC_TITLES
