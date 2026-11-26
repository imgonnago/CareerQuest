"""[71] 채팅 이전 대화와 이전 답변(추천·근거)을 LLM에 줄 글로. 대화는 서버에 저장하지 않고 프론트가 history로 보낸다."""
import re

from engine.schemas.chat import ChatTurn
from engine.schemas.rank import GuideEvidence, GuideResult

HISTORY_TURNS = 12          # LLM에 줄 이전 대화 차례 수. 추천 자료(이전 guide)는 이 수와 상관없이 찾는다
MEMO_SOURCES = 12          # [87] 이전 답변 메모에 붙일 출처 수 상한
# [87] 경로별로 답을 어떻게 만들었는지 (메모용)
MEMO_HOW = {"취업 가이드": "검색 도구(DB·웹 검색·잡알리오)로 찾은 문서를 읽고 근거 카드를 만들어 추천함",
            "정보 질문": "검색 도구로 찾은 문서를 읽고 그 내용으로 답함",
            "후속 질문": "앞 답변의 근거 안에서 답함"}
# [86] 이전 답변의 출처를 묻는 말. 후속 판단 LLM이 일반 대화로 보내 "추측으로 썼다"고 지어내서 코드가 먼저 후속 질문으로 보낸다
SOURCE_QUESTION = re.compile(r"출처|어디(서|에서)\s*(가져|찾|나온|나왔|본|봤|얻)|(무슨|어떤|어느)\s*자료|근거(가|는)\s*(뭐|어디|있)|참고한\s*(자료|곳|사이트)")


def user_turns(history: list[ChatTurn] | None) -> list[str]:
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


def history_block(history: list[ChatTurn] | None) -> str:
    return f"## 이전 대화\n{render_history(history, memo=True)}\n\n" if history else ""


def crisis_guided(history: list[ChatTurn] | None) -> bool:
    """이 대화에서 이미 위기 안내를 했는지."""
    return any(t.role == "assistant" and "109" in t.content for t in history or [])


def latest_guide(history: list[ChatTurn] | None) -> GuideResult | None:
    """가장 최근의, 추천이나 출처가 있는 답변."""
    for turn in reversed(history or []):
        if turn.role == "assistant" and turn.guide and (turn.guide.sections or turn.guide.sources):
            return turn.guide
    return None


def prior_evidence(guide: GuideResult) -> dict[str, GuideEvidence]:
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
