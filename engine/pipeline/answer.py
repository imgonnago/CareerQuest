"""결과 생성: 검증 통과 근거 순위 카드 + (card_id로 찾은) 상세 근거 카드 → 최종 답변(GuideResult).

- 코드가 슬롯별 항목을 만든다: 순위·이름·보유 여부·할 일·이유(4단계) + 마감일·학년·주의(5단계) + 근거 인용·출처 링크·출처 구분
- [64] 답변 LLM이 같은 목록으로 학생 질문에 대화하듯 답하는 글을 쓴다 (prompts/answer.py ANSWER_*, JSON이 아닌 글).
  코드가 글을 검사(check_answer): 없는 [C번호]는 지우고, R번호·지난 학기·학기와 방학이 안 맞음·근거에 없는 기업·추천을 절반 넘게 빠뜨림·
  너무 짧음은 문제로 적어 한 번 다시 쓰게 한다. 두 번 다 걸리면 총평 LLM(SUMMARY_*) + 코드가 쓴 항목 글로 바꾼다(answer_by_code).
  글에 나온 [C번호]의 출처(제목·링크·출처 구분)는 코드가 sources로 붙인다
"""
import json
import re

from engine.llm import LLMConfig, chat
from engine.pipeline.stage2_evidence import ACTIVITY, make_target
from engine.pipeline.stage5_verify import ROW_REF, TERM_MISMATCH, _companies, past_terms, tidy
from engine.pipeline.stage4_rank import render_held, today_text
from engine.prompts.answer import ANSWER_REQUEST, ANSWER_RETRY, ANSWER_SYSTEM, SUMMARY_REQUEST, SUMMARY_SYSTEM
from engine.schemas.aggregate import AggregationResult, NumberedCard
from engine.schemas.evidence import EvidenceDoc
from engine.schemas.rank import GuideEvidence, GuideItem, GuideResult, GuideSection, RankCard, RankingResult, SourceLabel, SummaryDraft

WRITER = LLMConfig(temperature=0.3)
ANSWER_WRITER = LLMConfig(temperature=0.3, num_predict=4096)  # [64] 긴 글. 되풀이 출력이 끝없이 가지 않게 상한
ANSWER_RETRIES = 1
MIN_ANSWER_CHARS = 300
MIN_COVERAGE = 0.5        # 추천 이름 중 글에 나와야 하는 비율
MAX_EVIDENCE = 3          # 항목마다 보여 줄 근거 (서로 다른 문서)
SUMMARY_QUOTES = 2        # 총평 LLM에 항목마다 줄 인용 수
OFFICIAL_SUFFIX = (".go.kr", ".ac.kr", ".or.kr", ".re.kr", ".gov", ".edu")
CARD_REF = re.compile(r"\[(C\d+)\]")
ACTIVITY_KINDS = ("대외활동", "교내프로그램", "교과목")


def source_label(doc: EvidenceDoc) -> SourceLabel:
    if doc.provider == "rag":
        return "건양대 자료"
    if doc.evidence_level != "fulltext":
        return "검색 요약"
    if doc.kind in ("blog", "cafe"):
        return "커뮤니티"
    return "공식" if doc.domain.endswith(OFFICIAL_SUFFIX) else "웹"


SOURCE_ORDER = {"건양대 자료": 0, "공식": 1, "웹": 2, "커뮤니티": 3, "검색 요약": 4}


def pick_evidence(rank: RankCard, cards: dict[str, NumberedCard], docs: dict[str, EvidenceDoc],
                  roles: dict[str, str], n: int = MAX_EVIDENCE) -> list[GuideEvidence]:
    """근거 카드 중 문서가 겹치지 않게 n개: 건양대 자료 → 공식 → 채용공고 문서 → 본문 → 나온 순."""
    cs = [cards[c] for c in rank.card_ids if c in cards]
    cs.sort(key=lambda c: (SOURCE_ORDER[source_label(docs[c.doc_id])], roles.get(c.doc_id) != "채용공고"))
    out, used = [], set()
    for c in cs:
        if c.doc_id in used:
            continue
        used.add(c.doc_id)
        d = docs[c.doc_id]
        out.append(GuideEvidence(card_id=c.card_id, quote=c.quote, title=d.title, url=d.url,
                                 source=source_label(d), published_at=d.published_at))
        if len(out) >= n:
            break
    return out


def to_item(r: RankCard, agg: AggregationResult) -> GuideItem:
    cards = {c.card_id: c for c in agg.cards}
    docs = {d.doc_id: d for d in agg.docs}
    kind = next((c for c in ACTIVITY_KINDS if c in r.categories), None) if r.slot == ACTIVITY else None
    # 활동·교과목은 Skill 이름(예: Deep Learning)보다 카드 item(과목명·대회명)이 학생에게 맞는 이름
    title = next((cards[c].item for c in r.card_ids if c in cards and cards[c].category == kind), r.skill) if kind else r.skill
    # [63] 한 카드 item에 활동 여러 개("Hecto AI Challenge / 2025 Bias-A-Thon / ETRI …")면 3단계가 나눈 이름을 쓴다
    if title != r.skill and re.search(r"\s[/,·]\s|,\s", title):
        title = r.skill
    return GuideItem(
        rank=r.rank, title=title, skill=r.skill, gap=r.gap, action=r.action, reason=r.reason, kind=kind,
        grade=r.grade, course_type=r.course_type, deadline=r.deadline, deadline_status=r.deadline_status,
        notes=r.notes, schedule=r.schedule, evidence=pick_evidence(r, cards, docs, agg.doc_roles),
    )


def build_sections(ranking: RankingResult, agg: AggregationResult) -> list[GuideSection]:
    sections: dict[str, list[GuideItem]] = {}
    for r in ranking.ranks:
        sections.setdefault(r.slot, []).append(to_item(r, agg))
    return [GuideSection(slot=s, items=items) for s, items in sections.items()]


def render_ranks(sections: list[GuideSection]) -> str:
    """총평 LLM용 추천 목록: 슬롯 · 순위 · 이름(보유 여부) · 할 일 · 이유 · [C번호] 인용 — 출처 제목."""
    lines = []
    for s in sections:
        lines.append(f"### {s.slot}")
        for it in s.items:
            lines.append(f"{it.rank}. {it.title} ({it.gap}) — 할 일: {it.action} / 이유: {it.reason}")
            for e in it.evidence[:SUMMARY_QUOTES]:
                lines.append(f"   [{e.card_id}] {' '.join(e.quote.split())} — {e.title}")
    return "\n".join(lines)


def render_past(past: list[GuideItem]) -> str:
    """[65] 답변 LLM용 지난 회차 목록: 이름·마감·근거 하나 (할 일·이유는 주지 않음 — 권하지 않게)."""
    lines = []
    for it in past:
        when = f"{it.deadline // 10000}.{it.deadline // 100 % 100}.{it.deadline % 100} 마감" if it.deadline else "이미 끝난 회차"
        ref = f" [{it.evidence[0].card_id}]" if it.evidence else ""
        lines.append(f"- {it.title} ({when}){ref}")
    return "\n".join(lines) or "없음"


def render_answer_input(sections: list[GuideSection]) -> str:
    """[64] 답변 LLM용 추천 목록: 총평용 목록에 종류·학년·마감·주의를 더함."""
    lines = []
    for s in sections:
        lines.append(f"### {s.slot}")
        for it in s.items:
            extra = [x for x in (it.kind, f"{it.grade}학년 개설" if it.grade else None, it.course_type,
                                 f"모집: {it.deadline_status}" if it.deadline_status else None) if x]
            lines.append(f"- {it.title} ({it.gap}{', ' + ', '.join(extra) if extra else ''})")
            lines.append(f"  할 일: {it.action}")
            lines.append(f"  이유: {it.reason}")
            for d in it.schedule:
                lines.append(f"  일정: {d}")
            for n in it.notes:
                lines.append(f"  주의: {n}")
            for e in it.evidence[:SUMMARY_QUOTES]:
                lines.append(f"  근거 [{e.card_id}] {' '.join(e.quote.split())} — {e.title}")
    return "\n".join(lines)


def _mentioned(it: GuideItem, text: str) -> bool:
    t = re.sub(r"\s+", "", text).lower()
    return any(re.sub(r"\s+", "", n).lower() in t for n in (it.title, it.skill) if n)


# [67] 프롬프트 문구를 그대로 옮긴 반말 끝맺음 ("다음 모집을 확인해 보라.")
BANMAL = re.compile(r"(보라|하라|해라|봐라|할 것|볼 것)(?=\s*(\[[CS]\d+\])*\s*([.!]|$))", re.M)
# [65] 지난 회차를 할 일로 권하는 문장 ("○○에 참여하세요") — 같은 문장에 지난·마감·다음 모집 같은 말이 없으면 문제
RECOMMEND_VERB = re.compile(r"참여|참가|지원|출전|신청|도전|준비")
PAST_WORD = re.compile(r"지난|마감|끝난|다음\s*(모집|회차|해|번)|열렸|있었|참고")


def check_answer(text: str, sections: list[GuideSection], agg: AggregationResult, today: int | None = None,
                 past: list[GuideItem] = ()) -> tuple[str, list[str]]:
    """[64] 대화형 답변 검사. 반환: (보여 주지 않은 [C번호]를 지운 글, 다시 쓰게 할 문제 목록)."""
    from engine.pipeline.stage5_verify import _today

    today = _today(today)
    items = [it for s in sections for it in s.items]
    shown = {e.card_id for it in [*items, *past] for e in it.evidence[:SUMMARY_QUOTES]}
    text = CARD_REF.sub(lambda m: m.group(0) if m.group(1) in shown else "", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    issues = []
    if len(text) < MIN_ANSWER_CHARS:
        issues.append(f"답변이 너무 짧음 ({len(text)}자). 추천 목록의 슬롯을 모두 다룰 것")
    if ROW_REF.search(text):
        issues.append("R번호를 씀 — 추천 이름으로 쓸 것")
    if m := TERM_MISMATCH.search(text):
        issues.append(f"시기가 맞지 않음: {m.group(0)} (겨울방학은 2학기 뒤, 여름방학은 1학기 뒤)")
    # [65] 지난 회차를 소개하는 문장("2026년 10월 마감")은 지난 시기 검사에서 뺀다 — 할 일로 권한 것만 본다
    sentences = re.split(r"(?<=[.!?。])\s+|\n", text)
    if gone := past_terms(" ".join(t for t in sentences if not PAST_WORD.search(t)), today):
        issues.append(f"이미 지난 시기를 씀: {', '.join(gone)}")
    # 근거에 없는 기업: 집계 표에 나온 기업 중 추천 목록(이름·할 일·이유·인용·제목)과 학생 관심 기업에 없는 것
    listed = (render_answer_input(sections) + " " + render_past(list(past)) + " "
              + " ".join(n for c in agg.job.companies for n in (c.name, c.original)))
    foreign = [c for c in _companies(agg) if c in text and c not in listed]
    if foreign:
        issues.append(f"추천 목록에 없는 기업을 씀: {', '.join(foreign)}")
    if "○○" in text or "OO" in text:  # [67] 예시의 자리표시자를 그대로 옮김
        issues.append("자리표시자(○○)를 그대로 씀 — 실제 이름으로 쓸 것")
    if m := BANMAL.search(text):
        issues.append(f"반말·지시문 끝맺음이 있음: \"…{text[max(0, m.start() - 20):m.end()]}\" — 존댓말(~해 보세요)로 쓸 것")
    # [67] 일정 목록에 없는 날짜 (오늘 이후 날짜만 — 지난 날짜는 past_terms가 봄)
    allowed = {re.sub(r"\s", "", d.split(" — ")[0]) for it in [*items, *past] for d in it.schedule}
    allowed |= {f"{it.deadline // 10000}.{it.deadline // 100 % 100}.{it.deadline % 100}" for it in [*items, *past] if it.deadline}
    from engine.pipeline.stage5_verify import FULL_DATE
    odd = [m.group(0) for m in FULL_DATE.finditer(text)
           if f"{int(m.group(1))}.{int(m.group(2))}.{int(m.group(3))}" not in allowed
           and int(m.group(1)) * 10000 + int(m.group(2)) * 100 + int(m.group(3)) >= today]
    if odd:
        issues.append(f"일정 목록에 없는 날짜를 씀: {', '.join(odd[:3])} — 날짜는 목록의 '일정'에 있는 것만")
    # [65] 지난 회차를 할 일로 권함
    for it in past:
        for sent in re.split(r"(?<=[.!?。])\s+|\n", text):
            if _mentioned(it, sent) and RECOMMEND_VERB.search(sent) and not PAST_WORD.search(sent):
                issues.append(f"마감된 '{it.title}'를 할 일처럼 권함 — 지난 회차라고 말하고 다음 모집 확인 정도로만: {sent.strip()[:80]}")
                break
    # [65] 슬롯을 통째로 건너뜀 (자격증·활동 문단 없이 빈 줄만 남은 일이 있음)
    skipped = [s.slot for s in sections if s.items and not any(_mentioned(it, text) for it in s.items)]
    if skipped:
        issues.append(f"이 슬롯의 추천을 하나도 말하지 않음: {', '.join(skipped)} — 슬롯마다 추천 이름을 그대로 써서 다룰 것")
    missed = [it.title for it in items if not _mentioned(it, text)]
    if items and len(missed) > len(items) * (1 - MIN_COVERAGE):
        issues.append(f"추천을 많이 빠뜨림 ({len(missed)}/{len(items)}개): {', '.join(missed[:8])}. 목록의 이름 그대로 쓸 것")
    return text, issues


def write_answer(target: str, held: str, sections: list[GuideSection], agg: AggregationResult,
                 today: int | None = None, past: list[GuideItem] = ()) -> tuple[str | None, list[str]]:
    """[64] 대화형 답변. 반환: (검사를 통과한 글 또는 None, 걸린 문제 전부)."""
    ranks = render_answer_input(sections)
    past_text = render_past(list(past))
    all_issues, issues = [], []
    for _ in range(ANSWER_RETRIES + 1):
        retry = ANSWER_RETRY.format(issues="\n".join(f"- {i}" for i in issues)) if issues else ""
        messages = [{"role": "system", "content": ANSWER_SYSTEM},
                    {"role": "user", "content": ANSWER_REQUEST.format(target=target, today=today_text(today), held=held,
                                                                      ranks=ranks, past=past_text, retry=retry)}]
        try:
            text = tidy_answer(chat(messages, ANSWER_WRITER)["content"])
        except Exception as e:
            issues = [f"답변 생성 실패 ({type(e).__name__})"]
            all_issues += issues
            continue
        text, issues = check_answer(text, sections, agg, today, past)
        all_issues += issues
        if not issues:
            return text, all_issues
    return None, all_issues


def tidy_answer(text: str) -> str:
    """줄 구조(문단·목록)는 두고 줄마다 띄어쓰기만 정리 (stage5_verify.tidy는 줄바꿈까지 합침)."""
    return "\n".join(tidy(ln) if ln.strip() else "" for ln in text.strip().splitlines())


def cited_sources(text: str, sections: list[GuideSection], past: list[GuideItem] = ()) -> list[GuideEvidence]:
    by_id = {e.card_id: e for it in [*(it for s in sections for it in s.items), *past] for e in it.evidence}
    return [by_id[c] for c in dict.fromkeys(CARD_REF.findall(text)) if c in by_id]


def code_answer(summary: str, sections: list[GuideSection], past: list[GuideItem] = ()) -> str:
    """대화형 답변이 실패했을 때: 총평 + 슬롯별 추천을 문장으로 (코드)."""
    parts = [summary]
    for s in sections:
        lines = [f"**{s.slot}**"]
        for it in s.items:
            ref = f" [{it.evidence[0].card_id}]" if it.evidence else ""
            note = f" ({'; '.join(it.notes)})" if it.notes else ""
            lines.append(f"- **{it.title}**: {it.action} {it.reason}{ref}{note}")
        parts.append("\n".join(lines))
    if past:
        parts.append("**참고: 지난 회차** — 이번 모집은 끝났지만 다시 열릴 수 있어요. 다음 모집 일정을 확인해 보세요.\n"
                     + render_past(list(past)))
    return "\n\n".join(parts)


def _code_summary(sections: list[GuideSection]) -> str:
    firsts = [f"{s.slot}에서는 {s.items[0].title}({s.items[0].action})" for s in sections if s.items]
    return ("먼저 " + ", ".join(firsts) + "부터 준비하세요.") if firsts else "추천할 근거를 충분히 찾지 못했어요."


def write_summary(target: str, held: str, sections: list[GuideSection]) -> tuple[str, bool]:
    """총평. 반환: (총평, 코드가 썼는지)."""
    if not any(s.items for s in sections):
        return _code_summary(sections), True
    shown = {e.card_id for s in sections for it in s.items for e in it.evidence[:SUMMARY_QUOTES]}
    messages = [{"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": SUMMARY_REQUEST.format(target=target, held=held, ranks=render_ranks(sections))}]
    try:
        msg = chat(messages, WRITER, format=SummaryDraft.model_json_schema())
        summary = SummaryDraft.model_validate(json.loads(msg["content"])).summary.strip()
    except Exception:
        return _code_summary(sections), True
    summary = tidy(CARD_REF.sub(lambda m: m.group(0) if m.group(1) in shown else "", summary))
    summary = re.sub(r"\s+([.,])", r"\1", re.sub(r"[ \t]{2,}", " ", summary)).strip()
    return (summary, False) if summary else (_code_summary(sections), True)


def generate_answer(ranking: RankingResult, agg: AggregationResult, today: int | None = None, log=print) -> GuideResult:
    sections = build_sections(ranking, agg)
    past = [to_item(r, agg) for r in ranking.past]
    target, held = make_target(agg.job), render_held(agg, ranking.held)
    answer, issues = write_answer(target, held, sections, agg, today, past) if any(s.items for s in sections) else (None, [])
    for i in issues:
        log(f"대화형 답변 검사: {i}")
    if answer is not None:
        summary, summary_by_code, answer_by_code = answer.split("\n\n")[0].strip(), False, False
    else:  # 두 번 다 걸리면 짧은 총평 LLM + 코드가 쓴 항목 글
        summary, summary_by_code = write_summary(target, held, sections)
        answer, answer_by_code = code_answer(summary, sections, past), True
    return GuideResult(job_title=agg.job.job_title, answer=answer, sources=cited_sources(answer, sections, past), past=past,
                       answer_by_code=answer_by_code, answer_issues=issues, summary=summary, sections=sections,
                       held=list(dict.fromkeys(g.skill for g in ranking.held)), lacking=ranking.lacking,
                       summary_by_code=summary_by_code)


def render_chat(guide: GuideResult) -> str:
    """[64] 채팅 화면 모양: 대화형 답변 + 글에 나온 근거 번호의 출처."""
    lines = [guide.answer]
    if guide.sources:
        lines += ["", "---", "**출처**"]
        lines += [f"[{e.card_id}] [{e.title}]({e.url}) · {e.source}" for e in guide.sources]
    return "\n".join(lines)


def render_markdown(guide: GuideResult) -> str:
    """확인용 마크다운 (프론트는 JSON을 그린다)."""
    lines = [f"# {guide.job_title} 취업 준비 가이드", "", guide.summary, ""]
    for s in guide.sections:
        lines.append(f"## {s.slot}")
        for it in s.items:
            extra = [x for x in (it.kind, f"{it.grade}학년" if it.grade else None, it.course_type, it.deadline_status) if x]
            name = it.title if it.title == it.skill else f"{it.title} [{it.skill}]"
            lines.append(f"{it.rank}. **{name}** ({it.gap}{', ' + ', '.join(extra) if extra else ''})")
            lines.append(f"   - 할 일: {it.action}")
            lines.append(f"   - 이유: {it.reason}")
            for d in it.schedule:
                lines.append(f"   - 일정: {d}")
            for n in it.notes:
                lines.append(f"   - 주의: {n}")
            for e in it.evidence:
                lines.append(f"   - [{e.card_id}] \"{' '.join(e.quote.split())}\" — [{e.title}]({e.url}) ({e.source})")
        lines.append("")
    if guide.past:
        lines.append("## 참고: 지난 회차 (다시 열릴 수 있음 — 다음 모집 확인)")
        lines += [f"- {it.title} ({it.deadline or '이미 끝남'}) — {it.evidence[0].title if it.evidence else ''}" for it in guide.past]
        lines.append("")
    if guide.held:
        lines.append("이미 갖춘 것: " + ", ".join(guide.held))
    if guide.lacking:
        lines.append("근거를 찾지 못한 항목: " + ", ".join(guide.lacking))
    return "\n".join(lines)
