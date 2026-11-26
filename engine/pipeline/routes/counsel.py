"""진로 고민 (answer_counsel)과 위기 답변 (crisis_answer).

일반 고민: 상담 답변 LLM(검색 없음) → 코드: 지어낸 전화번호를 지우고 상담 권유가 있는지 검사 → 없으면 한 번 다시 → 그래도 없으면 COUNSEL_NOTE를 붙임
위기 [71]: 코드가 가진 문장만 나간다. LLM은 공감 문장(CRISIS_EMPATHY)을 고르기만 하고, 안전 확인 질문과 상담 번호는 고정(CRISIS_GUIDE)
위기 안내 뒤 새 질문에 답할 때는 run.py가 SAFETY_REMINDER를 끝에 붙인다. 위기 판단 자체는 0단계(stage0_route.check_crisis·keep_crisis)
"""
import json

from engine.helpers.checks import HANJA, clean_phones
from engine.helpers.history import crisis_guided, history_block
from engine.helpers.student import render_student
from engine.helpers.text import tidy_lines
from engine.llm import LLMConfig, chat
from engine.prompts.stage0_route import COUNSEL_REQUEST, COUNSEL_SYSTEM, CRISIS_EMPATHY_REQUEST, CRISIS_EMPATHY_SYSTEM
from engine.schemas.chat import ChatTurn
from engine.schemas.job import StudentInput
from engine.schemas.rank import GuideResult
from engine.schemas.route import CrisisEmpathy

COUNSELOR = LLMConfig(temperature=0.5, num_predict=1024)
EMPATHY_PICKER = LLMConfig(temperature=0)  # [71] 위기 공감 문장 고르기 (0단계 위기 판단 CRISIS_JUDGE와 같은 설정)
COUNSEL_RETRIES = 1
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
SAFETY_REMINDER = "\n\n아까 이야기하신 마음이 다시 무거워지면 언제든 말해 주세요. **자살예방 상담전화 109**(24시간)에서도 들어 드려요."
COUNSEL_NOTE = "학교 학생상담센터에서 상담 선생님과 이야기를 나눠 보는 것도 권해 드려요. 혼자 정하지 않아도 괜찮아요."


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
                       EMPATHY_PICKER, format=CrisisEmpathy.model_json_schema())
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
    request = COUNSEL_REQUEST.format(history=history_block(history),
                                     student=render_student(student.model_copy(update={"documents": []})))
    text, issues, all_issues, failed = "", [], [], False
    for _ in range(COUNSEL_RETRIES + 1):
        retry = ("\n\n## 지난 답변의 문제 (이번에는 고칠 것)\n" + "\n".join(f"- {i}" for i in issues)) if issues else ""
        try:
            msg = chat([{"role": "system", "content": COUNSEL_SYSTEM}, {"role": "user", "content": request + retry}], COUNSELOR)
            text = clean_phones(tidy_lines(msg["content"]))
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
