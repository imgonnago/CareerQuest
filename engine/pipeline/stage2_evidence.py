"""2단계 근거 추출: 계획 LLM(검색) → 선택 LLM(읽을 문서) → 코드(본문 수집) → 읽기 LLM(근거 카드) → 코드(대조 검증).

1. 계획 LLM이 검색어를 정해 도구를 부른다(MIN~MAX_SEARCHES번). 결과는 근거 풀(EvidencePool)에 쌓이고
   LLM에는 제목 목록만 보여 준다 (본문은 이 단계에서 수집하지 않음)
2. 선택 LLM이 새 대화에서 근거 풀 전체의 제목 목록을 보고 읽을 문서를 고른다 (DocSelection)
3. 코드가 고른 문서 중 본문 수집 가능한(pending) 문서만 본문을 수집한다
4. 읽기 LLM이 고른 문서의 본문 전체를 읽고 근거 카드를 JSON으로 낸다 (CardDraftList)
   문서는 자르지 않고, 한 번에 넣을 분량(READ_BUDGET_CHARS)을 넘으면 문서 단위로 나눠 여러 번 부른다
5. 코드가 quote를 원문(본문 또는 제목+요약)과 대조해 통과한 카드만 남긴다
"""
import json
import re
import time

from pydantic import BaseModel, Field

from engine.llm import LLMConfig, chat
from engine.prompts.stage2_evidence import (
    PLAN_SYSTEM, READ_REQUEST, READ_SYSTEM, SEARCH_MORE, SELECT_REQUEST, SELECT_SYSTEM,
)
from engine.schemas import CardDraft, CardDraftList, DocSelection, EvidenceCard, EvidenceDoc
from engine.schemas.job import TargetJob
from engine.tools.requirements import TOOL_SPEC, RequirementSearchResult, fetch_texts, search_requirements

# 역할별 LLM 설정
PLANNER = LLMConfig(temperature=0.3)
SELECTOR = LLMConfig(temperature=0)
READER = LLMConfig(temperature=0)

MIN_SEARCHES = 2        # 계획 LLM이 이보다 적게 검색하고 끝내면 한 번 더 요청 (1번만 하고 끝내는 경향)
MAX_SEARCHES = 4        # 계획 LLM의 도구 호출 상한 (직무 1~2번 + 세부 관심 분야별)
PER_SOURCE = 10         # 검색 소스별 결과 수
MAX_SELECT = 8          # 읽을 문서 상한
READ_BUDGET_CHARS = 40_000  # 읽기 LLM 1회 입력 분량. 한국어 약 2자 = 1토큰 → 2만 토큰 남짓 (num_ctx 32768)


class EvidencePool:
    """근거 풀: 검색으로 모은 문서와 본문. 대화 안 번호(D1, D2 …)를 붙여 관리한다."""

    def __init__(self):
        self.docs: dict[str, EvidenceDoc] = {}   # D번호 → 문서
        self.texts: dict[str, str] = {}          # doc_id → 본문
        self._num: dict[str, str] = {}           # doc_id → D번호

    def add(self, res: RequirementSearchResult) -> list[str]:
        """도구 결과를 풀에 넣고 이번 결과의 D번호 목록을 돌려준다."""
        self.texts.update(res.texts)
        nums = []
        for doc in res.docs:
            if doc.doc_id not in self._num:
                num = f"D{len(self.docs) + 1}"
                self._num[doc.doc_id] = num
                self.docs[num] = doc
            nums.append(self._num[doc.doc_id])
        return nums

    def _header(self, num: str) -> str:
        doc = self.docs[num]
        source = f" · {doc.source_name}" if doc.source_name else ""
        return f"[{num}] {doc.title}\n출처: {doc.domain}{source} · 작성일 {doc.published_at}"

    def render_titles(self, nums: list[str] | None = None) -> str:
        """계획·선택 LLM용: [D번호] 제목 (본문 가능/요약만) 목록. nums가 없으면 풀 전체."""
        lines = []
        for num in nums or self.docs:
            doc = self.docs[num]
            readable = "본문 가능" if doc.fetch_status == "pending" or doc.doc_id in self.texts else "요약만"
            lines.append(f"[{num}] {doc.title} ({readable})")
        return "\n".join(lines)

    def render_doc(self, num: str) -> str:
        """읽기 LLM용: 본문 전체, 없으면 요약. 본문은 자르지 않는다."""
        doc = self.docs[num]
        text = self.texts.get(doc.doc_id)
        body = f"본문:\n{text}" if text is not None else f"요약: {doc.snippet}"
        return f"{self._header(num)}\n{body}"

    def fetch(self, nums: list[str]) -> None:
        """고른 문서 중 pending만 본문을 수집하고 상태를 갱신한다."""
        updated, texts = fetch_texts([self.docs[n] for n in nums], max_fetch=len(nums))
        for num, doc in zip(nums, updated):
            self.docs[num] = doc
        self.texts.update(texts)


def _norm(s: str) -> str:
    """대조용: 공백·말줄임표·따옴표를 모두 지운다.

    모델이 인용을 옮길 때 "AI나" → "AI 나", "3점" → "3 점"처럼 띄어쓰기를 넣는 경우가 많아 공백은 무시한다.
    """
    s = s.replace("…", "").replace("...", "")
    return re.sub(r"[\s\"'“”‘’]", "", s)


def verify(draft: CardDraft, pool: EvidencePool) -> EvidenceCard | None:
    """인용이 원문에 있으면 EvidenceCard, 없으면 None."""
    doc = pool.docs.get(draft.doc.strip("[] "))
    if doc is None or not _norm(draft.quote):
        return None
    text = pool.texts.get(doc.doc_id)
    source = text if text is not None else f"{doc.title} {doc.snippet}"
    if _norm(draft.quote) not in _norm(source):
        return None
    return EvidenceCard(
        doc_id=doc.doc_id, category=draft.category, company=draft.company, item=draft.item,
        quote=draft.quote, evidence_level="fulltext" if text is not None else "snippet",
    )


class ExtractionResult(BaseModel):
    job: TargetJob = Field(description="1단계 결과")
    docs: list[EvidenceDoc] = Field(description="근거 풀 전체 (D번호 순)")
    selected: list[str] = Field(description="계획 LLM이 읽기로 고른 문서의 doc_id")
    cards: list[EvidenceCard] = Field(description="원문 대조를 통과한 카드")
    rejected: list[CardDraft] = Field(description="원문에서 인용을 찾지 못해 버린 카드")
    queries: list[str]
    timings: dict[str, float] = Field(description="단계별 소요 시간(초)")


def plan_search(target: str, pool: EvidencePool, log=print) -> list[str]:
    """계획 LLM: 검색어를 정해 도구를 부른다. 반환: 검색어 목록."""
    queries = []
    messages = [{"role": "system", "content": PLAN_SYSTEM},
                {"role": "user", "content": target}]
    while len(queries) < MAX_SEARCHES:
        msg = chat(messages, PLANNER, tools=[TOOL_SPEC])
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            if len(queries) >= MIN_SEARCHES:
                break
            messages.append({"role": "user", "content": SEARCH_MORE})
            continue
        for call in calls:
            query = call["function"]["arguments"]["query"]
            if len(queries) >= MAX_SEARCHES:
                content = "검색 횟수를 모두 썼다. 더 검색하지 않는다."
            else:
                queries.append(query)
                res = search_requirements(query, per_source=PER_SOURCE, max_fetch=0)
                nums = pool.add(res)
                log(f"검색: {query} → 문서 {len(nums)}건")
                content = f"검색어: {query}  (문서 {len(nums)}건)\n{pool.render_titles(nums)}"
                if res.errors:
                    content += f"\n검색 실패: {', '.join(res.errors)}"
            messages.append({"role": "tool", "tool_name": "search_requirements", "content": content})
    return queries


def select_docs(target: str, pool: EvidencePool) -> list[str]:
    """선택 LLM: 근거 풀 전체 제목 목록에서 읽을 문서를 고른다. 반환: D번호 목록."""
    messages = [{"role": "system", "content": SELECT_SYSTEM},
                {"role": "user", "content": SELECT_REQUEST.format(
                    target=target, titles=pool.render_titles(), max_select=MAX_SELECT)}]
    msg = chat(messages, SELECTOR, format=DocSelection.model_json_schema())
    selected = []
    for num in DocSelection.model_validate(json.loads(msg["content"])).selected:
        if num in pool.docs and num not in selected:
            selected.append(num)
    return selected[:MAX_SELECT]


def _batches(pool: EvidencePool, nums: list[str]) -> list[list[str]]:
    """문서를 자르지 않고 READ_BUDGET_CHARS 안에서 묶는다. 혼자 넘는 문서는 단독으로."""
    batches, cur, size = [], [], 0
    for num in nums:
        n = len(pool.render_doc(num))
        if cur and size + n > READ_BUDGET_CHARS:
            batches.append(cur)
            cur, size = [], 0
        cur.append(num)
        size += n
    if cur:
        batches.append(cur)
    return batches


def read_cards(target: str, pool: EvidencePool, nums: list[str]) -> list[CardDraft]:
    """읽기 LLM: 고른 문서 전체를 읽고 근거 카드 초안을 낸다."""
    drafts = []
    for batch in _batches(pool, nums):
        docs = "\n\n".join(pool.render_doc(n) for n in batch)
        messages = [{"role": "system", "content": READ_SYSTEM},
                    {"role": "user", "content": READ_REQUEST.format(target=target, docs=docs)}]
        msg = chat(messages, READER, format=CardDraftList.model_json_schema())
        drafts += CardDraftList.model_validate(json.loads(msg["content"])).cards
    return drafts


def make_target(job: TargetJob) -> str:
    """LLM에 줄 조사 대상 텍스트 (1단계 결과 + 학생 질문 원문)."""
    lines = [f"목표 직무: {job.job_title} ({job.level})"]
    for it in job.interests:
        lines.append(f"세부 관심 분야: {it.name} (검색 키워드: {', '.join(it.keywords)})")
    if job.companies:
        lines.append(f"관심 기업: {', '.join(job.companies)}")
    lines.append(f"직무 검색 키워드: {', '.join(job.search_keywords)}")
    if job.student.question:
        lines.append(f"학생 질문: {job.student.question}")
    return "\n".join(lines)


def extract_evidence(job: TargetJob, log=print) -> ExtractionResult:
    pool, timings = EvidencePool(), {}
    target = make_target(job)

    t = time.time()
    queries = plan_search(target, pool, log)
    timings["plan"] = time.time() - t

    t = time.time()
    selected = select_docs(target, pool)
    timings["select"] = time.time() - t
    log(f"선택: {selected}")

    t = time.time()
    pool.fetch(selected)
    timings["fetch"] = time.time() - t
    log(f"본문 수집: {sum(pool.docs[n].doc_id in pool.texts for n in selected)}/{len(selected)}건")

    t = time.time()
    drafts = read_cards(target, pool, selected)
    timings["read"] = time.time() - t

    cards, rejected = [], []
    for draft in drafts:
        card = verify(draft, pool)
        (cards if card else rejected).append(card or draft)
    log(f"카드 {len(drafts)}장 중 통과 {len(cards)}장, 버림 {len(rejected)}장")
    return ExtractionResult(
        job=job, docs=list(pool.docs.values()), selected=[pool.docs[n].doc_id for n in selected],
        cards=cards, rejected=rejected, queries=queries, timings={k: round(v, 1) for k, v in timings.items()},
    )
