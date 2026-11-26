"""확인용 화면 글 (프론트는 GuideResult JSON을 그린다). test_llm.py·run.py가 터미널에 찍을 때 쓴다."""
from engine.schemas.rank import GuideResult


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
