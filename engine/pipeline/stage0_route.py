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
  [75] 에이전트 LLM이 도구를 직접 호출한다 (Ollama tool calling, 한 대화). search_rag(건양대·기관 DB) / search_requirements(웹)는 제목 목록,
  read_documents는 본문 전체를 대화에 돌려준다(본문은 자르지 않고, 합계가 INFO_READ_BUDGET을 넘으면 더 읽지 않음).
  읽은 본문에 답이 없으면 LLM이 검색어·도구를 바꿔 다시 찾고, 충분하면 도구 없이 답을 쓴다. 코드: 같은 검색 차단, 턴 상한,
  문서 없이 답하려 하면 되물음. 끝나면 없는 [D번호]는 지우고, 근거 번호가 없으면 한 번 다시 쓰게 함.
  [78] 답변 검토 LLM(새 대화, 질문·읽은 문서 제목·답): 다른 대상·과정 설명이면 같은 대화로 돌려보내 한 번 다시 찾게 하고, 또 걸리면 no_docs_answer.
  없다고 말했는데 안 쓴 검색 도구가 있어도 한 번 더 찾게 함. 검토 LLM이 실패하면 답을 막지 않음.
  글의 [D번호]를 나온 순으로 [S1]…로 바꾸고 출처(sources)를 붙인다
  읽은 문서가 없으면 자료 없음 LLM(no_docs_answer)이 쓰고, 지어낸 숫자가 있으면 NO_DOCS_FALLBACK
진로 고민 (answer_counsel)
  일반 고민: 상담 답변 LLM(검색 없음) → 코드: 지어낸 전화번호를 지우고 상담 권유가 있는지 검사 → 없으면 한 번 다시 → 그래도 없으면 COUNSEL_NOTE를 붙임
  위기 [71]: 코드가 가진 문장만 나간다. LLM은 공감 문장(CRISIS_EMPATHY)을 고르기만 하고, 안전 확인 질문과 상담 번호는 고정(CRISIS_GUIDE)
  위기 안내 뒤에는 위기 흐름이 기본(keep_crisis). 새 질문이 분명할 때만 그 질문에 답하고 코드가 SAFETY_REMINDER를 끝에 붙인다 (run.py)
"""
import json
import re

from engine.llm import LLMConfig, chat
from engine.pipeline.agent_loop import SearchGuard, run_tool_loop
from engine.pipeline.stage1_job import render_student
from engine.pipeline.stage2_evidence import AGENT_LIST_MAX, EvidencePool
from engine.pipeline.stage4_rank import today_text
from engine.pipeline.stage5_verify import tidy
from engine.prompts.stage0_route import (
    CHAT_SYSTEM, CLARIFY_REQUEST, CLARIFY_SYSTEM, CONTEXT_REQUEST, CONTEXT_SYSTEM, COUNSEL_REQUEST, COUNSEL_SYSTEM, CRISIS_EMPATHY_REQUEST,
    CRISIS_EMPATHY_SYSTEM, CRISIS_REQUEST, CRISIS_SYSTEM, FOLLOWUP_CHECK_REQUEST, FOLLOWUP_CHECK_SYSTEM, FOLLOWUP_REQUEST, FOLLOWUP_SYSTEM,
    INFO_AGENT_REQUEST, INFO_AGENT_SYSTEM, INFO_FINAL, INFO_NUDGE, INFO_READ_NEXT, INFO_REVIEW_FEEDBACK, INFO_REVIEW_REQUEST, INFO_REVIEW_SYSTEM,
    INFO_SEARCH_NEXT,
    NO_DOCS_REQUEST, NO_DOCS_SYSTEM, ROUTE_REQUEST, ROUTE_SYSTEM,
)
from engine.schemas.chat import ChatTurn, ContextDraft
from engine.schemas.job import StudentInput, TargetJob
from engine.schemas.rank import GuideEvidence, GuideResult
from engine.tools import jobalio
from engine.tools.jobalio import TOOL_SPEC as JOB_TOOL_SPEC, search_public_jobs
from engine.tools.rag_search import TOOL_SPEC as RAG_TOOL_SPEC, RagSearchError, search_rag
from engine.tools.requirements import TOOL_SPEC as WEB_TOOL_SPEC, search_requirements
from engine.schemas.route import AnswerReview, CrisisCheck, CrisisEmpathy, FollowupCheck, RouteDecision, RouteDraft

# [75] 정보 질문의 읽기 도구. 2단계 read_documents와 달리 본문을 대화에 그대로 돌려준다 (읽은 뒤 답까지 한 대화에서 함)
INFO_READ_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "read_documents",
        "description": "검색 결과 목록에서 고른 문서의 본문 전체를 읽는다. 한 번에 3건까지.",
        "parameters": {
            "type": "object",
            "properties": {
                "docs": {"type": "array", "items": {"type": "string"},
                         "description": "읽을 문서 번호. 관련 높은 순. 예: [\"D3\", \"D7\"]"},
            },
            "required": ["docs"],
        },
    },
}

ROUTER = LLMConfig(temperature=0)
INFO_AGENT = LLMConfig(temperature=0, num_predict=2048)  # [75] 도구 선택과 답변을 한 대화에서 한다
COUNSELOR = LLMConfig(temperature=0.5, num_predict=1024)
CRISIS_JUDGE = LLMConfig(temperature=0)
CONTEXT_WRITER = LLMConfig(temperature=0)
CHAT_WRITER = LLMConfig(temperature=0.5, num_predict=2048)
FOLLOWUP_WRITER = LLMConfig(temperature=0.3, num_predict=1024)
SHORT_WRITER = LLMConfig(temperature=0.3, num_predict=512)  # 되묻기·자료 없음

INFO_SLOT = "정보 질문"     # 근거 풀에서 이 경로 검색어로 나온 문서 표시
INFO_PER_SOURCE = 10
INFO_RAG_K = 3
INFO_SELECT = 3             # read_documents 한 번에 읽을 문서 수
INFO_MAX_TURNS = 8          # [75] 도구 대화 턴 상한
INFO_IDLE_TURNS = 2         # [75] 새로 실행된 도구가 없는 턴이 이만큼 이어지면 답을 쓰게 한다
INFO_READ_BUDGET = 25_000   # 대화에 넣을 본문 합계(자). 2단계 READ_BUDGET_CHARS와 같음. 넘으면 뒤 문서를 뺀다(본문은 자르지 않음)
INFO_NUDGES = 2         # [78] 문서를 읽지 않고 답하려 할 때 되묻는 횟수. 두 번째는 안 쓴 검색 도구가 있을 때만
INFO_REVIEWS = 1        # [78] 답변 검토에 걸렸을 때 다시 찾게 하는 횟수
INFO_RETRY_TURNS = 4    # [78] 다시 찾을 때의 턴 상한
INFO_RETRIES = 1
COUNSEL_RETRIES = 1
MEMO_SOURCES = 12          # [87] 이전 답변 메모에 붙일 출처 수 상한
# [87] 경로별로 답을 어떻게 만들었는지 (메모용)
MEMO_HOW = {"취업 가이드": "검색 도구(DB·웹 검색·잡알리오)로 찾은 문서를 읽고 근거 카드를 만들어 추천함",
            "정보 질문": "검색 도구로 찾은 문서를 읽고 그 내용으로 답함",
            "후속 질문": "앞 답변의 근거 안에서 답함"}
# [88] 일반 대화에서 묻지 않은 자기소개. 시스템 프롬프트 첫 줄(ROLE)을 따라 "건양대학교 학생분과 함께…"로 인사함
SELF_INTRO = re.compile(r"건양대(학교)?\s*학생|상담\s*도우미|도우미입니다|취업\s*(및|과|와)\s*진로|진로(와|와\s*취업|·취업)")
ASKS_ABOUT_HELPER = re.compile(r"건양|누구|소개|정체")  # 이것을 물을 때만 자기소개를 허용 (도구·기능 질문에도 "상담 도우미입니다"로 시작해서 뺌)
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
# [86] 이전 답변의 출처를 묻는 말. 후속 판단 LLM이 일반 대화로 보내 "추측으로 썼다"고 지어내서 코드가 먼저 후속 질문으로 보낸다
SOURCE_QUESTION = re.compile(r"출처|어디(서|에서)\s*(가져|찾|나온|나왔|본|봤|얻)|(무슨|어떤|어느)\s*자료|근거(가|는)\s*(뭐|어디|있)|참고한\s*(자료|곳|사이트)")
GENERIC_TITLES = {"", "신입", "인턴", "경력", "미정", "없음"}


def _user_turns(history: list[ChatTurn] | None) -> list[str]:
    return [t.content for t in history or [] if t.role == "user"]


def answer_memo(guide: GuideResult | None) -> str:
    """[87] 도우미 답변을 어떻게 만들었는지 LLM이 기억하게 붙이는 메모. 글만 넘기면 근거 번호만 보고 "추론해서 썼다"고 지어냈다([86]).

    history의 guide에서 만든다(프론트가 받은 guide를 그대로 돌려보내므로 API를 바꿀 필요 없음). tool_calls는 API로 오가지 않아 쓰지 않는다.
    """
    if guide is None:
        return ""
    if guide.sources:
        how = MEMO_HOW.get(guide.kind, "앞 답변의 근거 안에서 답함")
        lines = [f"[이 답변을 만든 방법] {guide.kind}: {how}. 출처는 답변 아래 목록으로 학생에게 보여 줌"]
        lines += [f"[{e.card_id}] {e.title} · {e.source} · {e.url}" for e in guide.sources[:MEMO_SOURCES]]
        if len(guide.sources) > MEMO_SOURCES:
            lines.append(f"(출처 {len(guide.sources) - MEMO_SOURCES}건 더 있음)")
        return "\n".join(lines)
    return f"[이 답변을 만든 방법] {guide.kind}: 검색 도구 없이 씀, 출처 없음"


def turn_text(t: ChatTurn, memo: bool) -> str:
    """대화 한 차례의 글. memo면 도우미 차례에 answer_memo를 붙인다."""
    note = answer_memo(t.guide) if memo and t.role == "assistant" else ""
    return f"{t.content}\n\n{note}" if note else t.content


def render_history(history: list[ChatTurn] | None, memo: bool = False) -> str:
    """LLM용 이전 대화. 최근 HISTORY_TURNS 차례 (9B 모델 입력이 길어지지 않게. 추천 자료는 render_prior가 따로 줌).
    memo: [87] 도우미 답변에 만든 방법·출처 메모를 붙임 (대화 정리 LLM에는 안 붙임 — 도우미 추천을 학생 정보로 옮기지 않게)."""
    who = {"user": "학생", "assistant": "도우미"}
    return "\n".join(f"{who[t.role]}: {turn_text(t, memo)}" for t in (history or [])[-HISTORY_TURNS:])


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


def _short_text(system: str, request: str) -> str:
    msg = chat([{"role": "system", "content": system}, {"role": "user", "content": request}], SHORT_WRITER)
    return "\n".join(tidy(ln) if ln.strip() else "" for ln in msg["content"].strip().splitlines())


def _history_block(history: list[ChatTurn] | None) -> str:
    return f"## 이전 대화\n{render_history(history, memo=True)}\n\n" if history else ""


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


class InfoAgent:
    """[75] 정보 질문의 도구 실행. LLM이 도구를 고르고, 이 클래스가 실행·저장·상한을 맡는다."""

    def __init__(self, log):
        self.pool, self.log = EvidencePool(), log
        self.rag_ok = True
        self.queries: list[str] = []
        self.guard = SearchGuard()               # 같은 검색 차단, [78] 새 문서 없는 도구 빼기
        self.read: list[str] = []                # 읽은 D번호
        self.size = 0                            # 대화에 넣은 본문 합계(자)
        self.calls: list[dict] = []
        self.nudges = 0
        self.jobs_ok = jobalio.available()       # [84] 잡알리오 키가 있을 때만

    def search_tools(self) -> list[str]:
        return (["search_rag"] if self.rag_ok else []) + ["search_requirements"] + (["search_public_jobs"] if self.jobs_ok else [])

    def tools(self) -> list[dict]:
        specs = [INFO_READ_TOOL_SPEC]
        if not self.guard.exhausted("search_requirements"):
            specs.insert(0, WEB_TOOL_SPEC)
        if self.jobs_ok and not self.guard.exhausted("search_public_jobs"):
            specs.insert(0, JOB_TOOL_SPEC)
        if self.rag_ok and not self.guard.exhausted("search_rag"):
            specs.insert(0, RAG_TOOL_SPEC)
        return specs

    def _search(self, tool: str, query) -> tuple[str, bool]:
        query = str(query or "").strip()
        if not query:
            return "query가 비어 있음. 검색어를 적어 다시 호출해.", False
        if self.guard.exhausted(tool):
            return f"{tool}에서 새 문서가 더 나오지 않는다. {self.guard.hint(self.search_tools())}다른 도구를 쓰거나 읽은 문서로 답해.", False
        if self.guard.repeated(tool, query):
            return f'"{query}"는 이 도구로 이미 검색했다. 검색어나 도구를 바꾸거나, 읽은 문서로 답해.', False
        if query not in self.queries:
            self.queries.append(query)
        if tool == "search_rag":
            try:
                res = search_rag(query, k=INFO_RAG_K)
            except RagSearchError as e:
                self.rag_ok = False
                self.log(f"RAG 검색 실패 — 검색 API로만: {e}")
                return "DB 검색을 쓸 수 없다. search_requirements를 써.", False
        elif tool == "search_public_jobs":
            res = search_public_jobs(query)
        else:
            res = search_requirements(query, per_source=INFO_PER_SOURCE, max_fetch=0)
        nums = self.pool.add(res, INFO_SLOT)
        shown = self.pool.order([n for n in self.guard.fresh(tool, nums) if n not in self.read], INFO_SLOT)
        self.log(f"정보 질문 {tool}: {query} → 문서 {len(nums)}건, 새 문서 {len(shown)}건")
        if not shown:
            return f'"{query}" 결과에 새 문서가 없다. {self.guard.hint(self.search_tools())}앞 목록에서 아직 안 읽은 문서를 읽거나 검색어·도구를 바꿔.', True
        more = f"\n(외 {len(shown) - AGENT_LIST_MAX}건 생략)" if len(shown) > AGENT_LIST_MAX else ""
        return f'"{query}" 결과:\n{self.pool.render_titles(shown[:AGENT_LIST_MAX])}{more}\n\n{INFO_SEARCH_NEXT}', True

    def _read(self, docs) -> tuple[str, bool]:
        """고른 문서의 본문을 그대로 돌려준다. 본문은 자르지 않고, 합계가 INFO_READ_BUDGET을 넘는 문서는 읽지 않는다 (처음 읽는 문서는 길어도 읽음)."""
        nums = list(dict.fromkeys(re.findall(r"D[0-9]+", json.dumps(docs, ensure_ascii=False))))
        new = [n for n in nums if n in self.pool.docs and n not in self.read]
        skipped = [f"{n}: 목록에 없는 번호" for n in nums if n not in self.pool.docs] \
            + [f"{n}: 이미 읽음" for n in nums if n in self.read] + [f"{n}: 한 번에 읽는 수를 넘음" for n in new[INFO_SELECT:]]
        new = new[:INFO_SELECT]
        if new:
            self.pool.fetch(new)
        parts = []
        for n in new:
            doc = self.pool.docs[n]
            if doc.doc_id not in self.pool.texts and not doc.snippet:
                skipped.append(f"{n}: 본문도 요약도 없음")
                continue
            part = self.pool.render_doc(n)
            if self.read and self.size + len(part) > INFO_READ_BUDGET:
                skipped.append(f"{n}: 읽을 분량을 넘음. 지금까지 읽은 문서로 답해")
                continue
            self.read.append(n)
            self.size += len(part)
            parts.append(part)
        note = ("\n\n못 읽음: " + "; ".join(skipped)) if skipped else ""
        self.log(f"정보 질문 읽기: {[n for n in new if n in self.read]}")
        if not parts:
            return f"읽을 문서가 없다.{note}\n검색 결과 목록의 [D번호]를 고르거나 새로 검색해.", False
        return "\n\n".join(parts) + note + f"\n\n{INFO_READ_NEXT}", True

    def run_tool(self, name: str, args: dict) -> tuple[str, bool]:
        if name in self.search_tools():
            return self._search(name, args.get("query"))
        if name == "read_documents":
            return self._read(args.get("docs"))
        return f"{name}이라는 도구는 없다. {', '.join(self.search_tools())}, read_documents 중에서 써.", False

    def _on_text(self, _text: str) -> str | None:
        """LLM이 답을 쓰려 함. 문서를 읽지 않았으면 되묻는다. 두 번째는 아직 안 쓴 검색 도구가 있을 때만."""
        unused = self.guard.unused(self.search_tools())
        if self.read or self.nudges >= INFO_NUDGES or (self.nudges and not unused and not self.guard.listed):
            return None
        self.nudges += 1
        listed = sorted(self.guard.listed, key=lambda n: int(n[1:]))[:5]
        unread = f"검색 결과의 {', '.join(listed)}를 아직 읽지 않았다. " if listed else ""
        return INFO_NUDGE.format(hint=unread + self.guard.hint(self.search_tools()))

    def loop(self, messages: list[dict], max_turns: int = 0) -> str:
        """LLM ↔ 도구 루프. 반환: LLM이 도구 없이 쓴 답 (상한에 닿았거나 오류면 빈 글)."""
        res = run_tool_loop(messages, INFO_AGENT, tools=self.tools, run_tool=self.run_tool, on_text=self._on_text,
                            max_turns=max_turns or INFO_MAX_TURNS, idle_turns=INFO_IDLE_TURNS, log=self.log)
        self.calls += res.calls
        return res.text


def review_answer(question: str, agent: InfoAgent, text: str, log=print) -> AnswerReview | None:
    """[78] 답을 내보내기 전 질문에 맞는 답인지 본다 (새 대화). 실패하면 None — 검토를 못 했다고 답을 막지 않는다."""
    titles = ", ".join(agent.pool.docs[n].title for n in agent.read)
    answer = DOC_REF.sub("", text)
    try:
        msg = chat([{"role": "system", "content": INFO_REVIEW_SYSTEM},
                    {"role": "user", "content": INFO_REVIEW_REQUEST.format(question=question, titles=titles, answer=answer)}],
                   ROUTER, format=AnswerReview.model_json_schema())
        review = AnswerReview.model_validate(json.loads(msg["content"]))
    except Exception as e:
        log(f"정보 답변 검토 실패: {type(e).__name__}")
        return None
    log(f"정보 답변 검토: {review.verdict} — {review.reason}")
    return review


def _with_refs(agent: InfoAgent, messages: list[dict], text: str, log=print) -> tuple[str, list[str]]:
    """없는 [D번호]를 지우고, 근거 번호가 없거나 글이 비었으면 도구 없이 다시 쓰게 한다. 반환: (글, 문제 목록)."""
    issues, all_issues = [], []
    for attempt in range(INFO_RETRIES + 2):
        if attempt or not text:  # 상한에 닿아 답이 없거나, 근거 번호가 없어 다시 쓰게 함. 도구는 주지 않는다
            ask = INFO_FINAL + ("\n\n## 지난 답변의 문제 (이번에는 고칠 것)\n" + "\n".join(f"- {i}" for i in issues) if issues else "")
            messages.append({"role": "user", "content": ask})
            msg = chat(messages, INFO_AGENT)
            messages.append({"role": "assistant", "content": msg.get("content") or ""})
            text = (msg.get("content") or "").strip()
        text = "\n".join(tidy(ln) if ln.strip() else "" for ln in text.splitlines())
        text = DOC_REF.sub(lambda m: m.group(0) if m.group(1) in agent.read else "", text)
        issues = [] if DOC_REF.search(text) else ["근거 문서 번호([D번호])가 하나도 없음 — 문서에서 가져온 문장 끝에 붙일 것"]
        all_issues += issues
        if not issues or attempt >= INFO_RETRIES:
            break
    return text, all_issues


def answer_info(question: str, log=print) -> GuideResult:
    agent = InfoAgent(log)
    pool = agent.pool

    def no_docs() -> GuideResult:
        log(f"정보 질문 도구 {len(agent.calls)}번: {[c['tool'] for c in agent.calls]}")
        return no_docs_answer(question, agent.queries or [question], log).model_copy(update={"tool_calls": agent.calls})

    messages = [{"role": "system", "content": INFO_AGENT_SYSTEM},
                {"role": "user", "content": INFO_AGENT_REQUEST.format(question=question, today=today_text())}]
    text = agent.loop(messages)
    all_issues: list[str] = []
    for attempt in range(INFO_REVIEWS + 1):
        if not agent.read:  # 읽은 문서가 없으면 답을 쓰지 않는다 (모델이 아는 내용으로 쓴 글은 근거가 없음)
            return no_docs()
        text, issues = _with_refs(agent, messages, text, log)
        all_issues += issues
        if not text.strip():  # 다시 써도 빈 글이면 안내로 대신한다
            return no_docs()
        # [78] 질문과 다른 대상을 답하거나 과정만 말한 글은 내보내지 않는다. 없다고 말했으면 안 쓴 검색 도구가 있을 때만 더 찾게 한다
        review = review_answer(question, agent, text, log)
        wrong = review is not None and review.verdict in ("다른 대상", "과정 설명")
        unused = agent.guard.unused(agent.search_tools())
        if not wrong and not (review is not None and review.verdict == "없다고 말함" and unused):
            break
        if attempt >= INFO_REVIEWS:
            if wrong:  # 다시 찾아도 맞는 답이 없다. 틀린 답을 내보내느니 못 찾았다고 한다
                return no_docs()
            break
        messages.append({"role": "user", "content": INFO_REVIEW_FEEDBACK.format(
            reason=review.reason, hint=agent.guard.hint(agent.search_tools()))})
        text = agent.loop(messages, INFO_RETRY_TURNS)
    log(f"정보 질문 도구 {len(agent.calls)}번: {[c['tool'] for c in agent.calls]}")
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
    return GuideResult(kind="정보 질문", job_title="", answer=text, sources=sources, answer_issues=all_issues, tool_calls=agent.calls,
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

def _tool_lines() -> str:
    """[83] 일반 대화 프롬프트에 넣을 도구 목록. 실제 도구 정의에서 만들어 프롬프트와 어긋나지 않게 한다."""
    specs = (RAG_TOOL_SPEC, WEB_TOOL_SPEC, *([JOB_TOOL_SPEC] if jobalio.available() else []), INFO_READ_TOOL_SPEC)
    return "\n".join(f"- {s['function']['name']}: {s['function']['description']}" for s in specs)


TOOLS_ASK = re.compile(r"도구|툴|tool|기능|검색\s*(할\s*수|가능|돼|되)", re.I)
TOOLS_ASK_NOTE = "\n\n(쓸 수 있는 도구를 묻는 말이면 도구마다 이름과 무엇을 찾는지 목록으로 모두 적어.)"
CHAT_FIXES = {"한자가 섞임": "\n- 한자를 절대 쓰지 않는다.",
              "묻지 않은 자기소개": "\n- 학교 이름·학생 호칭·도우미 소개를 넣지 말고 학생의 말에만 짧게 답한다. 예: \"안녕\" → \"안녕하세요! 무엇을 도와드릴까요?\""}


def answer_chat(message: str, history: list[ChatTurn] | None, log=print) -> GuideResult:
    """[72] 도구가 필요 없는 말은 모델이 바로 답한다. 이전 대화는 대화 형식 그대로 준다. 출처가 없는 답이라 sources는 비어 있다."""
    system = CHAT_SYSTEM.format(tools=_tool_lines())
    messages = [{"role": "system", "content": system}]
    messages += [{"role": t.role, "content": turn_text(t, memo=True)} for t in (history or [])[-HISTORY_TURNS:]]  # [87] 만든 방법·출처 메모
    # [88] 도구를 물으면 이름까지 다 알려 주게 지금 메시지 끝에 한 번 더 적는다 (시스템 프롬프트 규칙만으로는 이름을 빼고 답함)
    messages.append({"role": "user", "content": message + (TOOLS_ASK_NOTE if TOOLS_ASK.search(message) else "")})
    text, issues, all_issues = "", [], []
    for _ in range(COUNSEL_RETRIES + 1):
        # 다시 쓸 때는 지난 답을 보여 주지 않고 지시만 더한다. 대화로 말하면 "죄송합니다, 방금 답변에는…"이라고 그대로 답한다
        fix = "".join(CHAT_FIXES[i] for i in issues if i in CHAT_FIXES)
        attempt = [{"role": "system", "content": system + fix}, *messages[1:]] if issues else messages
        try:
            text = chat(attempt, CHAT_WRITER)["content"].strip()
            issues = ["한자가 섞임"] if HANJA.search(text) else []
            if SELF_INTRO.search(text) and not ASKS_ABOUT_HELPER.search(message):  # [88]
                issues.append("묻지 않은 자기소개")
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


def _prior_line(e: GuideEvidence) -> str:
    """[86] 출처를 물으면 링크까지 답하도록 제목·출처 구분·링크를 함께 준다."""
    return f"   [{e.card_id}] {e.quote} (출처: {e.title} · {e.source} · {e.url})"


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
            lines += [_prior_line(e) for e in it.evidence]
    if guide.past:
        lines.append("\n[지난 회차 — 추천 아님]")
        for it in guide.past:
            lines.append(f"- {it.title} (마감일: {it.deadline})")
            lines += [_prior_line(e) for e in it.evidence]
    if not guide.sections:
        lines.append("\n[출처]")
        lines += [_prior_line(e).strip() for e in guide.sources]
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
    sources = [evidence[c] for c in order]
    if SOURCE_QUESTION.search(question):  # [86] 출처를 물으면 글에 번호를 일부만 적어도 이전 답변의 출처를 모두 붙인다
        sources += [e for e in guide.sources if e.card_id not in order]
    return GuideResult(kind="후속 질문", job_title=guide.job_title, answer=text, sources=sources,
                       answer_by_code=by_code, answer_issues=all_issues, summary=text.split("\n\n")[0], sections=[], held=[], lacking=[])
