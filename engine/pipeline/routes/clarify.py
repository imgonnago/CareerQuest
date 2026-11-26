"""더 알려 달라는 답: 목표가 없을 때 되묻기([66][70]), 정보 질문에서 자료를 못 찾았을 때 안내([70]).

둘 다 LLM이 학생 말에 맞춰 짧게 쓰고, 실패하거나 검사에 걸리면 고정 문구(CLARIFY_FALLBACK·NO_DOCS_FALLBACK).
"""
import re

from engine.helpers.checks import HANJA, NUMBER
from engine.helpers.history import history_block
from engine.helpers.student import render_student
from engine.helpers.text import tidy_lines
from engine.llm import LLMConfig, chat
from engine.prompts.stage0_route import CLARIFY_REQUEST, CLARIFY_SYSTEM, NO_DOCS_REQUEST, NO_DOCS_SYSTEM
from engine.schemas.chat import ChatTurn
from engine.schemas.job import StudentInput
from engine.schemas.rank import GuideResult

SHORT_WRITER = LLMConfig(temperature=0.3, num_predict=512)  # 되묻기·자료 없음
BAD_QUESTION = re.compile(r"(?<![까요])(답니다|습니다|ㅂ니다)\?")  # 평서문 끝에 붙은 물음표
CLARIFY_FALLBACK = ("어떤 방향으로 준비하면 좋을지 같이 정리해 드릴게요. 그러려면 몇 가지만 알려 주세요.\n\n"
                    "- 전공과 학년\n- 관심 있는 분야나 하고 싶은 일 (예: 백엔드 개발, 로봇, 데이터 분석)\n"
                    "- 가 보고 싶은 기업이 있다면 기업 이름\n- 지금 가진 기술·자격증·해 본 프로젝트\n\n"
                    "하나만 알려 주셔도 거기서부터 시작할 수 있어요.")
NO_DOCS_FALLBACK = ("찾아본 자료에서는 이 질문에 답할 내용을 찾지 못했어요. 질문을 조금 더 구체적으로(자격증·대회·과목 이름, 연도 등) "
                    "알려 주시면 다시 찾아볼게요.")


def _short_text(system: str, request: str) -> str:
    msg = chat([{"role": "system", "content": system}, {"role": "user", "content": request}], SHORT_WRITER)
    return tidy_lines(msg["content"])


def clarify_answer(student: StudentInput, log=print, history: list[ChatTurn] | None = None) -> GuideResult:
    """[70] 학생 말에 맞춰 LLM이 되묻는다. 실패하거나 묻는 문장이 없으면 고정 문구."""
    by_code = False
    try:
        text = _short_text(CLARIFY_SYSTEM, CLARIFY_REQUEST.format(
            history=history_block(history), student=render_student(student.model_copy(update={"documents": []}))))
    except Exception as e:
        log(f"되묻기 작성 실패: {type(e).__name__}")
        text = ""
    if not ("?" in text or "알려" in text or "말씀해" in text) or HANJA.search(text):
        text, by_code = CLARIFY_FALLBACK, True
    text = BAD_QUESTION.sub(lambda m: m.group(1) + ".", text)
    return GuideResult(kind="취업 가이드", job_title="", answer=text, summary=text.split("\n\n")[0], sections=[],
                       held=[], lacking=[], answer_by_code=by_code)


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
