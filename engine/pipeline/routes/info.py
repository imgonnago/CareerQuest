"""정보 질문 (answer_info) [75].

에이전트 LLM이 도구를 직접 호출한다 (Ollama tool calling, 한 대화). search_rag(건양대·기관 DB) / search_requirements(웹)는 제목 목록,
read_documents는 본문 전체를 대화에 돌려준다(본문은 자르지 않고, 합계가 INFO_READ_BUDGET을 넘으면 더 읽지 않음).
읽은 본문에 답이 없으면 LLM이 검색어·도구를 바꿔 다시 찾고, 충분하면 도구 없이 답을 쓴다. 코드: 같은 검색 차단, 턴 상한,
문서 없이 답하려 하면 되물음. 끝나면 없는 [D번호]는 지우고, 근거 번호가 없으면 한 번 다시 쓰게 함.
[78] 답변 검토 LLM(새 대화, 질문·읽은 문서 제목·답): 다른 대상·과정 설명이면 같은 대화로 돌려보내 한 번 다시 찾게 하고, 또 걸리면 no_docs_answer.
없다고 말했는데 안 쓴 검색 도구가 있어도 한 번 더 찾게 함. 검토 LLM이 실패하면 답을 막지 않음.
글의 [D번호]를 나온 순으로 [S1]…로 바꾸고 출처(sources)를 붙인다.
읽은 문서가 없으면 자료 없음 LLM(clarify.no_docs_answer)이 쓰고, 지어낸 숫자가 있으면 NO_DOCS_FALLBACK
"""
import json
import re

from engine.helpers.dates import today_text
from engine.helpers.sources import source_label
from engine.helpers.text import tidy
from engine.llm import LLMConfig, chat
from engine.pipeline.agent_loop import SearchGuard, run_tool_loop
from engine.pipeline.evidence_pool import AGENT_LIST_MAX, EvidencePool
from engine.pipeline.routes.clarify import no_docs_answer
from engine.prompts.stage0_route import (
    INFO_AGENT_REQUEST, INFO_AGENT_SYSTEM, INFO_FINAL, INFO_NUDGE, INFO_READ_NEXT, INFO_REVIEW_FEEDBACK, INFO_REVIEW_REQUEST,
    INFO_REVIEW_SYSTEM, INFO_SEARCH_NEXT,
)
from engine.schemas.rank import GuideEvidence, GuideResult
from engine.schemas.route import AnswerReview
from engine.tools import jobalio
from engine.tools.jobalio import TOOL_SPEC as JOB_TOOL_SPEC, search_public_jobs
from engine.tools.rag_search import TOOL_SPEC as RAG_TOOL_SPEC, RagSearchError, search_rag
from engine.tools.requirements import TOOL_SPEC as WEB_TOOL_SPEC, search_requirements

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
INFO_AGENT = LLMConfig(temperature=0, num_predict=2048)  # [75] 도구 선택과 답변을 한 대화에서 한다
REVIEWER = LLMConfig(temperature=0)  # [78] 답변 검토 (0단계 입력 판단 ROUTER와 같은 설정)
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
DOC_REF = re.compile(r"\[(D\d+)\]")


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
                   REVIEWER, format=AnswerReview.model_json_schema())
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
    sources = []
    for d in order:
        doc = pool.docs[d]
        sources.append(GuideEvidence(card_id=label[d], quote=doc.snippet or "", title=doc.title, url=doc.url,
                                     source=source_label(doc), published_at=doc.published_at))
    for i in all_issues:
        log(f"정보 답변 검사: {i}")
    return GuideResult(kind="정보 질문", job_title="", answer=text, sources=sources, answer_issues=all_issues, tool_calls=agent.calls,
                       summary=text.split("\n\n")[0], sections=[], held=[], lacking=[])
