"""일반 대화 (answer_chat) [72]: 인사·개념 설명·코드·글 다듬기. 도구 없이 이전 대화 + 지금 메시지를 모델에 그대로 준다. 출처 없음.

[83] 도구를 물으면 답하도록 시스템 프롬프트에 도구 목록·한계를 넣는다(_tool_lines, 실제 TOOL_SPEC에서 만듦).
위기 판단은 그 앞(0단계)에서 끝났다.
"""
import re

from engine.helpers.checks import HANJA, clean_phones
from engine.helpers.history import HISTORY_TURNS, turn_text
from engine.llm import LLMConfig, chat
from engine.pipeline.routes.info import INFO_READ_TOOL_SPEC
from engine.prompts.stage0_route import CHAT_SYSTEM
from engine.schemas.chat import ChatTurn
from engine.schemas.rank import GuideResult
from engine.tools import jobalio
from engine.tools.jobalio import TOOL_SPEC as JOB_TOOL_SPEC
from engine.tools.rag_search import TOOL_SPEC as RAG_TOOL_SPEC
from engine.tools.requirements import TOOL_SPEC as WEB_TOOL_SPEC

CHAT_WRITER = LLMConfig(temperature=0.5, num_predict=2048)
CHAT_RETRIES = 1
# [88] 일반 대화에서 묻지 않은 자기소개. 시스템 프롬프트 첫 줄(ROLE)을 따라 "건양대학교 학생분과 함께…"로 인사함
SELF_INTRO = re.compile(r"건양대(학교)?\s*학생|상담\s*도우미|도우미입니다|취업\s*(및|과|와)\s*진로|진로(와|와\s*취업|·취업)")
ASKS_ABOUT_HELPER = re.compile(r"건양|누구|소개|정체")  # 이것을 물을 때만 자기소개를 허용 (도구·기능 질문에도 "상담 도우미입니다"로 시작해서 뺌)


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
    for _ in range(CHAT_RETRIES + 1):
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
    text = clean_phones(HANJA.sub("", text)) or "죄송해요, 지금 답을 만들지 못했어요. 다시 한 번 말씀해 주시겠어요?"
    return GuideResult(kind="일반 대화", job_title="", answer=text, summary=text.split("\n\n")[0], sections=[], held=[], lacking=[],
                       answer_by_code=by_code, answer_issues=all_issues)
