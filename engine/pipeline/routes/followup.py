"""후속 질문 (answer_followup) [71]: 이전 답변의 추천·근거 안에서만 답한다. 검색·파이프라인을 다시 돌리지 않는다.

자료에 없는 숫자가 있으면 한 번 다시 쓰고, 그래도 있으면 고정 안내. [86] 출처를 물으면 이전 답변의 출처를 모두 붙인다.
"""
import re

from engine.helpers.checks import HANJA, NUMBER
from engine.helpers.dates import today_text
from engine.helpers.history import SOURCE_QUESTION, latest_guide, prior_evidence, render_prior
from engine.helpers.text import tidy_lines
from engine.llm import LLMConfig, chat
from engine.prompts.stage0_route import FOLLOWUP_REQUEST, FOLLOWUP_SYSTEM
from engine.schemas.chat import ChatTurn
from engine.schemas.rank import GuideResult

FOLLOWUP_WRITER = LLMConfig(temperature=0.3, num_predict=1024)
FOLLOWUP_RETRIES = 1
PRIOR_REF = re.compile(r"\[([CS]\d+)\]")


def answer_followup(question: str, history: list[ChatTurn] | None, log=print) -> GuideResult:
    """[71] 이전 답변의 추천·근거 안에서만 답한다. 자료에 없는 숫자가 있으면 한 번 다시 쓰고, 그래도 있으면 고정 안내."""
    guide = latest_guide(history)
    evidence = prior_evidence(guide)
    prior = render_prior(guide)
    allowed = set(NUMBER.findall(" ".join([prior, question, today_text()])))
    issues, all_issues, text = [], [], ""
    for _ in range(FOLLOWUP_RETRIES + 1):
        retry = ("\n## 지난 답변의 문제 (이번에는 고칠 것)\n" + "\n".join(f"- {i}" for i in issues) + "\n") if issues else ""
        try:
            msg = chat([{"role": "system", "content": FOLLOWUP_SYSTEM},
                        {"role": "user", "content": FOLLOWUP_REQUEST.format(today=today_text(), prior=prior, question=question, retry=retry)}],
                       FOLLOWUP_WRITER)
            text = tidy_lines(msg["content"])
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
