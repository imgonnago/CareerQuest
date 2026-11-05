"""0단계 입력 판단 + 정보 질문·진로 고민 답변 ([66]).

위기 판단 (check_crisis) ([70] LLM 주도, [71] 이전 학생 말까지 봄). 대화 정리보다 먼저 지금 메시지 원문으로 한다
  1. 코드: 명백한 위기 표현(CRISIS_CERTAIN — 죽고 싶다·자살하고 싶다·방법을 가리키는 말 등)이면 LLM 판단과 상관없이 진로 고민 + crisis (안전망)
  2. 위기 판단 LLM(새 대화, CrisisCheck): 돌려 말한 표현까지 판단. 실패하면 넓은 정규식(CRISIS_FALLBACK)으로
대화 정리 (resolve_history) [71]: 이전 대화가 있으면 LLM이 혼자 읽어도 되는 질문과 학생 정보를 만들고, 코드가 학생 말에 실제로 있는지 대조한다
입력 판단 (route_input)
  1. 코드: 질문 칸이 비어 있으면(폼 칸만) LLM 없이 취업 가이드
  2. 입력 판단 LLM(새 대화): 취업 가이드 / 정보 질문 / 진로 고민 / 일반 대화 (RouteDraft). 실패하면 취업 가이드
일반 대화 (answer_chat) [72]: 도구 없이 이전 대화 + 지금 메시지를 모델에 그대로 준다. 위기 판단은 그 앞에서 끝났다
후속 질문 [71]: 이전 추천·근거가 있으면 입력 판단 전에 전용 LLM(check_followup)이 자료 안에 답이 있는지 본다.
  answer_followup이 그 자료만 읽고 답한다. 검색·파이프라인을 다시 돌리지 않는다
취업 가이드 → run.py가 1~5단계. 입력 판단 LLM이 has_goal=false(직무·관심 분야·전공·기업을 하나도 말하지 않음)이고 폼 칸도 비었으면
  1단계 전에 clarify_answer로 되묻는다 (1단계는 정보가 없어도 직무를 지어냄). 1단계 결과가 비어도(needs_more_info) 2단계 전에 되묻는다.
  되묻기는 LLM이 학생 말에 맞춰 쓰고, 실패·검사 탈락이면 CLARIFY_FALLBACK
위기면 안전을 먼저 묻는 COUNSEL_CRISIS_REQUEST (자퇴·진로 이야기로 받지 않게)
정보 질문 (answer_info)
  계획 LLM(검색어 1~3개) → 코드가 검색어마다 RAG(건양대 자료, 문서 종류 제한 없음) → 검색 API를 근거 풀(EvidencePool)에 → 선택 LLM(최대 INFO_SELECT건)
  → 본문 수집 → 답변 LLM(본문 전체, INFO_READ_BUDGET까지 문서째 — 본문은 자르지 않음) → 코드: 없는 [D번호]는 지우고, 근거 번호가 하나도 없으면
  한 번 다시 쓰게 함. 글의 [D번호]를 나온 순으로 [S1]…로 바꾸고 출처(sources)를 붙인다
  고를 문서가 없으면 자료 없음 LLM(no_docs_answer)이 쓰고, 지어낸 숫자가 있으면 NO_DOCS_FALLBACK
진로 고민 (answer_counsel)
  일반 고민: 상담 답변 LLM(검색 없음) → 코드: 지어낸 전화번호를 지우고 상담 권유가 있는지 검사 → 없으면 한 번 다시 → 그래도 없으면 COUNSEL_NOTE를 붙임
  위기 [71]: 코드가 가진 문장만 나간다. LLM은 공감 문장(CRISIS_EMPATHY)을 고르기만 하고, 안전 확인 질문과 상담 번호는 고정(CRISIS_GUIDE)
  위기 안내 뒤에는 위기 흐름이 기본(keep_crisis). 새 질문이 분명할 때만 그 질문에 답하고 코드가 SAFETY_REMINDER를 끝에 붙인다 (run.py)
"""
import json
import re

from engine.llm import LLMConfig, chat
from engine.pipeline.stage1_job import render_student
from engine.pipeline.stage2_evidence import EvidencePool
from engine.pipeline.stage4_rank import today_text
from engine.pipeline.stage5_verify import tidy
from engine.prompts.stage0_route import (
    CHAT_SYSTEM, CLARIFY_REQUEST, CLARIFY_SYSTEM, CONTEXT_REQUEST, CONTEXT_SYSTEM, COUNSEL_REQUEST, COUNSEL_SYSTEM, CRISIS_EMPATHY_REQUEST,
    CRISIS_EMPATHY_SYSTEM, CRISIS_REQUEST, CRISIS_SYSTEM, FOLLOWUP_CHECK_REQUEST, FOLLOWUP_CHECK_SYSTEM, FOLLOWUP_REQUEST, FOLLOWUP_SYSTEM,
    INFO_ANSWER_REQUEST, INFO_ANSWER_SYSTEM, INFO_PLAN_REQUEST, INFO_PLAN_SYSTEM, INFO_SELECT_REQUEST, INFO_SELECT_SYSTEM,
    NO_DOCS_REQUEST, NO_DOCS_SYSTEM, ROUTE_REQUEST, ROUTE_SYSTEM,
)
from engine.schemas import DocSelection
from engine.schemas.chat import ChatTurn, ContextDraft
from engine.schemas.job import StudentInput, TargetJob
from engine.schemas.rank import GuideEvidence, GuideResult
from engine.schemas.route import CrisisCheck, CrisisEmpathy, FollowupCheck, InfoPlan, RouteDecision, RouteDraft

ROUTER = LLMConfig(temperature=0)
INFO_WRITER = LLMConfig(temperature=0.3, num_predict=2048)
COUNSELOR = LLMConfig(temperature=0.5, num_predict=1024)
CRISIS_JUDGE = LLMConfig(temperature=0)
CONTEXT_WRITER = LLMConfig(temperature=0)
CHAT_WRITER = LLMConfig(temperature=0.5, num_predict=2048)
FOLLOWUP_WRITER = LLMConfig(temperature=0.3, num_predict=1024)
SHORT_WRITER = LLMConfig(temperature=0.3, num_predict=512)  # 되묻기·자료 없음

INFO_SLOT = "정보 질문"     # 근거 풀에서 이 경로 검색어로 나온 문서 표시
INFO_PER_SOURCE = 10
INFO_RAG_K = 3
INFO_SELECT = 3             # 읽을 문서 수
INFO_READ_BUDGET = 25_000   # 답변 LLM 1회 입력 분량(자). 2단계 READ_BUDGET_CHARS와 같음. 넘으면 뒤 문서를 뺀다(본문은 자르지 않음)
INFO_RETRIES = 1
COUNSEL_RETRIES = 1
HISTORY_TURNS = 12          # LLM에 줄 이전 대화 차례 수. 추천 자료(이전 guide)는 이 수와 상관없이 찾는다
FOLLOWUP_RETRIES = 1

# [70] 스스로에 대해 말하는 명백한 표현만. LLM이 놓쳐도 이것은 잡는다. 주제로만 쓰인 말("자살 예방 상담사")은 걸리지 않게 좁혔다
CRISIS_CERTAIN = re.compile(r"죽고\s*싶|죽어\s*버리고\s*싶|자살\s*(하고\s*싶|할\s*(까|래|거)|충동|생각)|자해\s*(를\s*)?(하고\s*싶|했|해요|멈)|살기\s*싫|살고\s*싶지\s*않|목숨을\s*끊|번개탄|투신|목을\s*매")
# 위기 판단 LLM이 실패했을 때만 쓰는 넓은 표현. 오탐이 많지만("과제 끝내고 싶어요") 판단이 없을 때는 놓치는 것보다 낫다
CRISIS_FALLBACK = re.compile(r"죽고\s*싶|죽어\s*버리|자살|자해|목숨|살기\s*싫|살고\s*싶지\s*않|사라지고\s*싶|극단적\s*선택|끝내고\s*싶|없어지고\s*싶")
SAFETY_CONTACTS = ("혼자 견디지 않으셔도 돼요. 곁에 있는 믿을 수 있는 사람에게 지금 연락하거나, **자살예방 상담전화 109**(24시간), "
                   "**정신건강 위기상담 1577-0199**에 전화해 보세요. 위급하면 112나 119에 바로 연락해 주세요.")
CRISIS_GUIDE = "지금 안전한 곳에 계신가요? 혼자 계신가요?\n\n" + SAFETY_CONTACTS            # 처음 위기 답변
CRISIS_GUIDE_AGAIN = "지금 곁에 있어 줄 사람이 있을까요? 조금 더 이야기해 주셔도 돼요.\n\n" + SAFETY_CONTACTS  # 이미 안내한 뒤
# [71] LLM이 쓰면 "내일을 기다리시는 마음"처럼 뜻이 뒤집혀서, 고르기만 하게 한다
CRISIS_EMPATHY = {
    "고통": "많이 힘드셨을 것 같아요. 이야기해 주셔서 고마워요.",
    "짐이_된다는_생각": "그런 생각까지 들 만큼 마음이 많이 힘드셨군요. 말해 주셔서 고마워요.",
    "작별": "이렇게 말해 주셔서 고마워요. 지금 마음이 많이 걱정돼요.",
    "방법을_찾음": "많이 힘드신 건 아닐까 걱정돼요. 말을 걸어 주셔서 고마워요.",
    "다른_질문과_섞임": "방금 하신 말씀이 마음에 걸려서 그 이야기를 먼저 하고 싶어요. 말해 주셔서 고마워요. 준비 이야기는 그다음에 같이 봐요.",
}
CRISIS_EMPATHY_AGAIN = "계속 이야기해 주셔서 고마워요."
NEW_QUESTION = re.compile(r"\?|뭐|무엇|언제|어떻게|어디|몇|왜|알려|추천|준비")  # 위기 안내 뒤 새 질문임이 분명한 말
SAFETY_REMINDER = "\n\n아까 이야기하신 마음이 다시 무거워지면 언제든 말해 주세요. **자살예방 상담전화 109**(24시간)에서도 들어 드려요."
CRISIS_NUMBERS = ("109", "1577-0199")   # 위기 답변에 반드시 있어야 함 (CRISIS_GUIDE에 들어 있음)
PHONE = re.compile(r"(?<!\d)\d{2,4}-\d{3,4}(-\d{4})?(?!\d)|(?<![\d-])1\d{2}(?![\d-])(?=\s*(번|으로|로|에|\(|\)))")
NUMBER = re.compile(r"\d+")
HANJA = re.compile(r"[\u4e00-\u9fff]")  # 9B 모델이 한국어 글에 한자를 섞는 경우가 있음
BAD_QUESTION = re.compile(r"(?<![까요])(답니다|습니다|ㅂ니다)\?")  # 평서문 끝에 붙은 물음표
CLARIFY_FALLBACK = ("어떤 방향으로 준비하면 좋을지 같이 정리해 드릴게요. 그러려면 몇 가지만 알려 주세요.\n\n"
                    "- 전공과 학년\n- 관심 있는 분야나 하고 싶은 일 (예: 백엔드 개발, 로봇, 데이터 분석)\n"
                    "- 가 보고 싶은 기업이 있다면 기업 이름\n- 지금 가진 기술·자격증·해 본 프로젝트\n\n"
                    "하나만 알려 주셔도 거기서부터 시작할 수 있어요.")
NO_DOCS_FALLBACK = ("찾아본 자료에서는 이 질문에 답할 내용을 찾지 못했어요. 질문을 조금 더 구체적으로(자격증·대회·과목 이름, 연도 등) "
                    "알려 주시면 다시 찾아볼게요.")
COUNSEL_NOTE = "학교 학생상담센터에서 상담 선생님과 이야기를 나눠 보는 것도 권해 드려요. 혼자 정하지 않아도 괜찮아요."
DOC_REF = re.compile(r"\[(D\d+)\]")
PRIOR_REF = re.compile(r"\[([CS]\d+)\]")
GENERIC_TITLES = {"", "신입", "인턴", "경력", "미정", "없음"}


def _user_turns(history: list[ChatTurn] | None) -> list[str]:
    return [t.content for t in history or [] if t.role == "user"]


def render_history(history: list[ChatTurn] | None) -> str:
    """LLM용 이전 대화. 최근 HISTORY_TURNS 차례 (9B 모델 입력이 길어지지 않게. 추천 자료는 render_prior가 따로 줌)."""
    who = {"user": "학생", "assistant": "도우미"}
    return "\n".join(f"{who[t.role]}: {t.content}" for t in (history or [])[-HISTORY_TURNS:])


def crisis_guided(history: list[ChatTurn] | None) -> bool:
    """이 대화에서 이미 위기 안내를 했는지."""
    return any(t.role == "assistant" and "109" in t.content for t in history or [])


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
    users = _user_turns(history)
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


def latest_guide(history: list[ChatTurn] | None) -> GuideResult | None:
    """가장 최근의, 추천이나 출처가 있는 답변."""
    for turn in reversed(history or []):
        if turn.role == "assistant" and turn.guide and (turn.guide.sections or turn.guide.sources):
            return turn.guide
    return None


def check_followup(student: StudentInput, prior: GuideResult | None, log=print) -> RouteDecision | None:
    """[71] 이전 답변 자료로 답할 후속 질문이면 그 경로, 아니면 None. 세 경로 판단과 같이 시키면 전부 정보 질문으로 가서 새 대화로 따로 묻는다."""
    question = (student.question or "").strip()
    if prior is None or not question:
        return None
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


def _short_text(system: str, request: str) -> str:
    msg = chat([{"role": "system", "content": system}, {"role": "user", "content": request}], SHORT_WRITER)
    return "\n".join(tidy(ln) if ln.strip() else "" for ln in msg["content"].strip().splitlines())


def _history_block(history: list[ChatTurn] | None) -> str:
    return f"## 이전 대화\n{render_history(history)}\n\n" if history else ""


def clarify_answer(student: StudentInput, log=print, history: list[ChatTurn] | None = None) -> GuideResult:
    """[70] 학생 말에 맞춰 LLM이 되묻는다. 실패하거나 묻는 문장이 없으면 고정 문구."""
    by_code = False
    try:
        text = _short_text(CLARIFY_SYSTEM, CLARIFY_REQUEST.format(
            history=_history_block(history), student=render_student(student.model_copy(update={"documents": []}))))
    except Exception as e:
        log(f"되묻기 작성 실패: {type(e).__name__}")
        text = ""
    if not ("?" in text or "알려" in text or "말씀해" in text) or HANJA.search(text):
        text, by_code = CLARIFY_FALLBACK, True
    text = BAD_QUESTION.sub(lambda m: m.group(1) + ".", text)
    return GuideResult(kind="취업 가이드", job_title="", answer=text, summary=text.split("\n\n")[0], sections=[],
                       held=[], lacking=[], answer_by_code=by_code)


# ---------------------------------------------------------------- 정보 질문

def _search(question: str, pool: EvidencePool, log) -> list[str]:
    from engine.tools.rag_search import RagSearchError, search_rag
    from engine.tools.requirements import search_requirements

    today = today_text()
    try:
        msg = chat([{"role": "system", "content": INFO_PLAN_SYSTEM},
                    {"role": "user", "content": INFO_PLAN_REQUEST.format(question=question, today=today)}],
                   ROUTER, format=InfoPlan.model_json_schema())
        queries = [q.strip() for q in InfoPlan.model_validate(json.loads(msg["content"])).queries if q.strip()][:3]
    except Exception:
        queries = []
    queries = queries or [question]
    rag_ok = True
    for q in queries:
        if rag_ok:  # 데이터 우선순위: RAG(건양대 자료) 먼저
            try:
                pool.add(search_rag(q, k=INFO_RAG_K), INFO_SLOT)
            except RagSearchError as e:
                rag_ok = False
                log(f"RAG 검색 실패 — 검색 API로만: {e}")
        pool.add(search_requirements(q, per_source=INFO_PER_SOURCE, max_fetch=0), INFO_SLOT)
    log(f"정보 질문 검색어: {queries} → 문서 {len(pool.docs)}건")
    return queries


def _select(question: str, pool: EvidencePool) -> list[str]:
    nums, titles = pool.render_slot(INFO_SLOT, set())
    if not nums:
        return []
    msg = chat([{"role": "system", "content": INFO_SELECT_SYSTEM},
                {"role": "user", "content": INFO_SELECT_REQUEST.format(question=question, titles=titles, n=INFO_SELECT)}],
               ROUTER, format=DocSelection.model_json_schema())
    picked = [n for n in DocSelection.model_validate(json.loads(msg["content"])).selected if n in nums]
    return list(dict.fromkeys(picked))[:INFO_SELECT]


def _render_docs(pool: EvidencePool, nums: list[str]) -> tuple[list[str], str]:
    """본문 전체를 문서째 넣는다. 분량을 넘으면 뒤 문서를 뺀다 (첫 문서는 길어도 넣음)."""
    kept, parts, size = [], [], 0
    for n in nums:
        part = pool.render_doc(n)
        if kept and size + len(part) > INFO_READ_BUDGET:
            break
        kept.append(n)
        parts.append(part)
        size += len(part)
    return kept, "\n\n".join(parts)


def no_docs_answer(question: str, queries: list[str], log=print) -> GuideResult:
    """[70] 자료를 못 찾았을 때 LLM이 질문에 맞춰 안내한다. 질문·검색어에 없는 숫자가 있으면 답을 지어낸 것으로 보고 고정 문구."""
    by_code = False
    try:
        text = _short_text(NO_DOCS_SYSTEM, NO_DOCS_REQUEST.format(question=question, queries=", ".join(queries)))
    except Exception as e:
        log(f"자료 없음 안내 작성 실패: {type(e).__name__}")
        text = ""
    allowed = set(NUMBER.findall(" ".join([question, *queries])))
    if not text or set(NUMBER.findall(text)) - allowed:
        text, by_code = NO_DOCS_FALLBACK, True
    return GuideResult(kind="정보 질문", job_title="", answer=text, summary=text, sections=[], held=[], lacking=[],
                       answer_by_code=by_code)


def answer_info(question: str, log=print) -> GuideResult:
    pool = EvidencePool()
    queries = _search(question, pool, log)
    nums = _select(question, pool)
    if nums:
        pool.fetch(nums)
    nums = [n for n in nums if pool.docs[n].doc_id in pool.texts or pool.docs[n].snippet]
    if not nums:
        return no_docs_answer(question, queries, log)
    nums, docs = _render_docs(pool, nums)
    issues, all_issues, text = [], [], ""
    for _ in range(INFO_RETRIES + 1):
        retry = ("\n\n## 지난 답변의 문제 (이번에는 고칠 것)\n" + "\n".join(f"- {i}" for i in issues)) if issues else ""
        msg = chat([{"role": "system", "content": INFO_ANSWER_SYSTEM},
                    {"role": "user", "content": INFO_ANSWER_REQUEST.format(question=question, today=today_text(),
                                                                           docs=docs, retry=retry)}], INFO_WRITER)
        text = "\n".join(tidy(ln) if ln.strip() else "" for ln in msg["content"].strip().splitlines())
        text = DOC_REF.sub(lambda m: m.group(0) if m.group(1) in nums else "", text)
        issues = [] if DOC_REF.search(text) else ["근거 문서 번호([D번호])가 하나도 없음 — 문서에서 가져온 문장 끝에 붙일 것"]
        all_issues += issues
        if not issues:
            break
    # [D번호] → 나온 순 [S1]…, 출처
    order = list(dict.fromkeys(DOC_REF.findall(text)))
    label = {d: f"S{i}" for i, d in enumerate(order, 1)}
    text = DOC_REF.sub(lambda m: f"[{label[m.group(1)]}]", text)
    from engine.pipeline.answer import source_label
    sources = []
    for d in order:
        doc = pool.docs[d]
        sources.append(GuideEvidence(card_id=label[d], quote=doc.snippet or "", title=doc.title, url=doc.url,
                                     source=source_label(doc), published_at=doc.published_at))
    for i in all_issues:
        log(f"정보 답변 검사: {i}")
    return GuideResult(kind="정보 질문", job_title="", answer=text, sources=sources, answer_issues=all_issues,
                       summary=text.split("\n\n")[0], sections=[], held=[], lacking=[])


# ---------------------------------------------------------------- 진로 고민

def _clean_phones(text: str) -> str:
    """지어낸 전화번호를 지운다. 번호는 코드의 고정 위기 안내에만 있다."""
    return PHONE.sub("", text)


def crisis_answer(student: StudentInput, log=print, history: list[ChatTurn] | None = None) -> GuideResult:
    """[71] 위기 답변은 코드가 가진 문장만 나간다. LLM은 어느 공감 문장이 맞는지만 고른다.
    직접 쓰게 하면 검사를 빠져나가는 위험한 문장이 계속 나왔다 ([70]·[71])."""
    question = student.question or student.desired_job or ""
    guided = crisis_guided(history)
    empathy, by_code = CRISIS_EMPATHY_AGAIN if guided else CRISIS_EMPATHY["고통"], False
    if not guided:
        try:
            msg = chat([{"role": "system", "content": CRISIS_EMPATHY_SYSTEM},
                        {"role": "user", "content": CRISIS_EMPATHY_REQUEST.format(question=question)}],
                       CRISIS_JUDGE, format=CrisisEmpathy.model_json_schema())
            empathy = CRISIS_EMPATHY[CrisisEmpathy.model_validate(json.loads(msg["content"])).kind]
        except Exception as e:
            log(f"위기 공감 문장 고르기 실패: {type(e).__name__}")
            by_code = True
    text = empathy + "\n\n" + (CRISIS_GUIDE_AGAIN if guided else CRISIS_GUIDE)
    return GuideResult(kind="진로 고민", job_title="", answer=text, summary=empathy, sections=[], held=[], lacking=[],
                       answer_by_code=by_code, crisis=True)


def answer_counsel(student: StudentInput, crisis: bool, log=print, history: list[ChatTurn] | None = None) -> GuideResult:
    """[70] 글은 LLM이 쓰고 코드는 검사만 한다. 다시 써도 상담 권유가 없을 때만 코드가 붙인다."""
    if crisis:
        return crisis_answer(student, log, history)
    request = COUNSEL_REQUEST.format(history=_history_block(history),
                                     student=render_student(student.model_copy(update={"documents": []})))
    text, issues, all_issues, failed = "", [], [], False
    for _ in range(COUNSEL_RETRIES + 1):
        retry = ("\n\n## 지난 답변의 문제 (이번에는 고칠 것)\n" + "\n".join(f"- {i}" for i in issues)) if issues else ""
        try:
            msg = chat([{"role": "system", "content": COUNSEL_SYSTEM}, {"role": "user", "content": request + retry}], COUNSELOR)
            text = _clean_phones("\n".join(tidy(ln) if ln.strip() else "" for ln in msg["content"].strip().splitlines()))
            failed = False
            issues = ["한자가 섞임 — 한국어로만 쓸 것"] if HANJA.search(text) else []
            if "상담" not in text and "지도교수" not in text:
                issues.append("학생상담센터나 지도교수 권유가 없음 — 글 안에 한 번 넣을 것")
        except Exception as e:
            log(f"상담 답변 실패: {type(e).__name__}")
            failed = True
            issues = [f"상담 답변 실패 ({type(e).__name__})"]
        all_issues += issues
        if not issues:
            break
    by_code = False
    text = HANJA.sub("", text)  # 다시 써도 남은 한자
    if failed or not text:
        text, by_code = "많이 지치고 힘드셨겠어요. 지금 마음이 어떤지, 무엇이 가장 힘든지 조금 더 이야기해 주실 수 있을까요?", True
    if "상담" not in text and "지도교수" not in text:
        text, by_code = text + "\n\n" + COUNSEL_NOTE, True
    return GuideResult(kind="진로 고민", job_title="", answer=text, summary=text.split("\n\n")[0], sections=[], held=[], lacking=[],
                       answer_by_code=by_code, answer_issues=all_issues)


# ---------------------------------------------------------------- 일반 대화

def answer_chat(message: str, history: list[ChatTurn] | None, log=print) -> GuideResult:
    """[72] 도구가 필요 없는 말은 모델이 바로 답한다. 이전 대화는 대화 형식 그대로 준다. 출처가 없는 답이라 sources는 비어 있다."""
    messages = [{"role": "system", "content": CHAT_SYSTEM}]
    messages += [{"role": t.role, "content": t.content} for t in (history or [])[-HISTORY_TURNS:]]
    messages.append({"role": "user", "content": message})
    text, issues, all_issues = "", [], []
    for _ in range(COUNSEL_RETRIES + 1):
        # 다시 쓸 때는 지난 답을 보여 주지 않고 지시만 더한다. 대화로 말하면 "죄송합니다, 방금 답변에는…"이라고 그대로 답한다
        attempt = [{"role": "system", "content": CHAT_SYSTEM + "\n- 한자를 절대 쓰지 않는다."}, *messages[1:]] if issues else messages
        try:
            text = chat(attempt, CHAT_WRITER)["content"].strip()
            issues = ["한자가 섞임"] if HANJA.search(text) else []
        except Exception as e:
            log(f"일반 대화 실패: {type(e).__name__}")
            issues = [f"일반 대화 실패 ({type(e).__name__})"]
        all_issues += issues
        if not issues:
            break
    by_code = not text
    text = _clean_phones(HANJA.sub("", text)) or "죄송해요, 지금 답을 만들지 못했어요. 다시 한 번 말씀해 주시겠어요?"
    return GuideResult(kind="일반 대화", job_title="", answer=text, summary=text.split("\n\n")[0], sections=[], held=[], lacking=[],
                       answer_by_code=by_code, answer_issues=all_issues)


# ---------------------------------------------------------------- 후속 질문

def _prior_evidence(guide: GuideResult) -> dict[str, GuideEvidence]:
    items = [it for sec in guide.sections for it in sec.items] + list(guide.past)
    return {e.card_id: e for e in [*guide.sources, *(e for it in items for e in it.evidence)]}


def render_prior(guide: GuideResult) -> str:
    """후속 답변 LLM 입력: 이전 답변 글 + 추천 항목·근거 인용 전체."""
    lines = [f"[이전 답변 글]\n{guide.answer}"]
    for sec in guide.sections:
        lines.append(f"\n[{sec.slot}]")
        for it in sec.items:
            extra = [f"할 일: {it.action}", f"이유: {it.reason}"]
            if it.schedule:
                extra.append("일정: " + "; ".join(it.schedule))
            if it.deadline:
                extra.append(f"마감일: {it.deadline} ({it.deadline_status})")
            lines.append(f"{it.rank}. {it.title} — " + " / ".join(extra))
            lines += [f"   [{e.card_id}] {e.quote} (출처: {e.title})" for e in it.evidence]
    if guide.past:
        lines.append("\n[지난 회차 — 추천 아님]")
        for it in guide.past:
            lines.append(f"- {it.title} (마감일: {it.deadline})")
            lines += [f"   [{e.card_id}] {e.quote} (출처: {e.title})" for e in it.evidence]
    if not guide.sections:
        lines.append("\n[출처]")
        lines += [f"[{e.card_id}] {e.quote} (출처: {e.title})" for e in guide.sources]
    return "\n".join(lines)


def answer_followup(question: str, history: list[ChatTurn] | None, log=print) -> GuideResult:
    """[71] 이전 답변의 추천·근거 안에서만 답한다. 자료에 없는 숫자가 있으면 한 번 다시 쓰고, 그래도 있으면 고정 안내."""
    guide = latest_guide(history)
    evidence = _prior_evidence(guide)
    prior = render_prior(guide)
    allowed = set(NUMBER.findall(" ".join([prior, question, today_text()])))
    issues, all_issues, text = [], [], ""
    for _ in range(FOLLOWUP_RETRIES + 1):
        retry = ("\n## 지난 답변의 문제 (이번에는 고칠 것)\n" + "\n".join(f"- {i}" for i in issues) + "\n") if issues else ""
        try:
            msg = chat([{"role": "system", "content": FOLLOWUP_SYSTEM},
                        {"role": "user", "content": FOLLOWUP_REQUEST.format(today=today_text(), prior=prior, question=question, retry=retry)}],
                       FOLLOWUP_WRITER)
            text = "\n".join(tidy(ln) if ln.strip() else "" for ln in msg["content"].strip().splitlines())
            text = PRIOR_REF.sub(lambda m: m.group(0) if m.group(1) in evidence else "", text)
            issues = []
            plain = PRIOR_REF.sub("", text)
            invented = sorted(set(NUMBER.findall(plain)) - allowed)
            if invented:
                issues.append(f"자료에 없는 숫자({', '.join(invented)}) — 자료에 있는 것만 쓸 것")
            if HANJA.search(text):
                issues.append("한자가 섞임 — 한국어로만 쓸 것")
            if not text:
                issues.append("글이 비어 있음")
        except Exception as e:
            log(f"후속 답변 실패: {type(e).__name__}")
            issues = [f"후속 답변 실패 ({type(e).__name__})"]
        all_issues += issues
        if not issues:
            break
    by_code = bool(issues)
    if by_code:
        text = "이전 답변 자료만으로는 확실히 답하기 어려워요. 궁금한 점을 조금 더 구체적으로 알려 주시면 새로 찾아볼게요."
    order = list(dict.fromkeys(PRIOR_REF.findall(text)))
    return GuideResult(kind="후속 질문", job_title=guide.job_title, answer=text, sources=[evidence[c] for c in order],
                       answer_by_code=by_code, answer_issues=all_issues, summary=text.split("\n\n")[0], sections=[], held=[], lacking=[])
