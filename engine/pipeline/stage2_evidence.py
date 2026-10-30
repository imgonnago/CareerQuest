"""2단계 근거 추출: 계획 LLM(검색어) → 선택 LLM(읽을 문서) → 코드(본문 수집) → 읽기 LLM(근거 카드) → 코드(대조 검증).

[37] 계획·선택은 아직 덜 채운 가이드 슬롯마다 따로 부른다 (한 번에 시키면 일부 슬롯만 하거나 이전 계획을 되풀이 — [30]·[34])
1. 계획 LLM이 슬롯마다 검색어를 JSON으로 낸다(SearchPlan). 코드가 검색하고 결과를 근거 풀(EvidencePool)에 쌓으며
   문서마다 어느 슬롯 검색어로 나왔는지 기록한다 (본문은 이 단계에서 수집하지 않음)
2. 선택 LLM이 슬롯마다 새 대화에서 그 슬롯 검색어로 나온 문서의 제목 목록을 보고 읽을 문서를 고른다 (DocSelection)
3. 코드가 고른 문서 중 본문 수집 가능한(pending) 문서만 본문을 수집한다
4. 읽기 LLM이 고른 문서의 본문 전체를 읽고 근거 카드를 JSON으로 낸다 (CardDraftList)
   문서는 자르지 않고, 한 번에 넣을 분량(READ_BUDGET_CHARS)을 넘으면 문서 단위로 나눠 여러 번 부른다
5. 코드가 quote를 원문(본문 또는 제목+요약)과 대조해 통과한 카드만 남긴다

추출 루프 (README "체크리스트 충족까지")
- [33] 가이드 슬롯(SLOTS: 교과목 3·교내 프로그램 2·채용 요건 4·자격증 3·공모전·대외활동 2·추천 포트폴리오 2)마다
  근거 문서를 몇 건 채웠는지 센다. 수는 고유 문서(doc_id) 기준 — 한 문서에서 카드가 쏟아져도 슬롯마다 1건 ([31])
- 1~5를 한 바퀴로 보고, 채우지 못한 슬롯이 남아 있으면 다음 바퀴를 돈다
- 교내 슬롯(교과목·교내 프로그램)은 건양대 문서의 카드만 센다. RAG가 없어 지금은 검색으로 채우고,
  나중에 RAG를 붙이면 RAG 정보가 오래됐을 때 최신 정보를 보충하는 경로가 된다
- 다음 바퀴는 부족한 슬롯만 계획·선택한다. 계획 LLM에는 그 슬롯에서 이미 쓴 검색어를 주고, 같은 검색어는 코드가 버린다
- 이미 읽은 문서는 다음 바퀴 선택 목록에서 뺀다
- MAX_ROUNDS 바퀴 또는 MAX_SECONDS가 지나면 그때까지 모은 카드로 끝낸다 (바퀴 시작 전에 확인)
"""
import json
import re
import time

from pydantic import BaseModel, Field

from engine.llm import LLMConfig, chat
from engine.prompts.stage2_evidence import (
    PLAN_REQUEST, PLAN_SYSTEM, READ_REQUEST, READ_SYSTEM, SELECT_EXTRA, SELECT_REQUEST, SELECT_SYSTEM, SLOT_TASKS,
)
from engine.schemas import CardDraft, CardDraftList, DocSelection, EvidenceCard, EvidenceDoc, SearchPlan
from engine.schemas.job import TargetJob
from engine.tools.requirements import RequirementSearchResult, fetch_texts, search_requirements

# 역할별 LLM 설정
PLANNER = LLMConfig(temperature=0.3)
SELECTOR = LLMConfig(temperature=0)
READER = LLMConfig(temperature=0)

PER_SOURCE = 10         # 검색 소스별 결과 수
READ_BUDGET_CHARS = 40_000  # 읽기 LLM 1회 입력 분량. 한국어 약 2자 = 1토큰 → 2만 토큰 남짓 (num_ctx 32768)

# 추출 루프
# [33] 카드 총수(TARGET_CARDS 15) 대신 가이드 슬롯별 채울 문서 수. 슬롯 이름 → (세는 카드 분류, 채울 문서 수). 합계 16
SLOTS: dict[str, tuple[tuple[str, ...], int]] = {
    "교과목": (("교과목",), 3),
    "교내 프로그램": (("교내프로그램",), 2),
    "채용 요건": (("자격요건", "우대사항", "기술스택"), 4),
    "자격증": (("자격증",), 3),
    "공모전·대외활동": (("대외활동",), 2),
    "추천 포트폴리오": (("프로젝트",), 2),
}
SLOT_OF = {cat: slot for slot, (cats, _) in SLOTS.items() for cat in cats}  # 카드 분류 → 슬롯 (기업정보·기타는 없음)
SCHOOL_SLOTS = {"교과목", "교내 프로그램"}  # 건양대 문서의 카드만 세는 슬롯
SCHOOL = "건양대학교"
SCHOOL_MARKS = ("건양", "konyang")  # 건양대 문서 판정: 제목·요약·도메인·출처·본문에 이 말이 있으면
# [37] 슬롯별 검색어 수 (1바퀴, 2바퀴부터). 공모전·대외활동은 건양대·외부 공모전·해커톤·대외활동 여러 방면으로
FIRST_QUERIES = {"채용 요건": 2, "공모전·대외활동": 3}  # 없는 슬롯은 1
NEXT_QUERIES = {"공모전·대외활동": 2}                   # 없는 슬롯은 1
SELECT_PER_SLOT = 2     # 슬롯마다 한 바퀴에 고를 문서 상한 (채울 수가 1 남았으면 1)
MIN_READABLE = 5        # [38] 슬롯 목록에 본문 가능 문서가 이만큼 있으면 요약만 있는 문서(네이버 등)는 목록에서 뺀다
MAX_ROUNDS = 3          # 바퀴 상한
MAX_SECONDS = 90        # 2단계 시간 상한. 넘으면 새 바퀴를 시작하지 않음


class EvidencePool:
    """근거 풀: 검색으로 모은 문서와 본문. 대화 안 번호(D1, D2 …)를 붙여 관리한다."""

    def __init__(self):
        self.docs: dict[str, EvidenceDoc] = {}   # D번호 → 문서
        self.texts: dict[str, str] = {}          # doc_id → 본문
        self._num: dict[str, str] = {}           # doc_id → D번호
        self.slots_of: dict[str, list[str]] = {}  # [37] D번호 → 이 문서가 나온 검색어의 슬롯 (선택을 슬롯별로 할 때)

    def add(self, res: RequirementSearchResult, slot: str) -> list[str]:
        """검색 결과를 풀에 넣고 이번 결과의 D번호 목록을 돌려준다. slot = 이 검색어를 만든 가이드 슬롯."""
        self.texts.update(res.texts)
        nums = []
        for doc in res.docs:
            if doc.doc_id not in self._num:
                num = f"D{len(self.docs) + 1}"
                self._num[doc.doc_id] = num
                self.docs[num] = doc
            num = self._num[doc.doc_id]
            if slot not in self.slots_of.setdefault(num, []):
                self.slots_of[num].append(slot)
            nums.append(num)
        return nums

    def _header(self, num: str) -> str:
        doc = self.docs[num]
        source = f" · {doc.source_name}" if doc.source_name else ""
        return f"[{num}] {doc.title}\n출처: {doc.domain}{source} · 작성일 {doc.published_at}"

    def readable(self, num: str) -> bool:
        """본문을 읽을 수 있는 문서인지 (아직 수집 전이거나 이미 수집함)."""
        doc = self.docs[num]
        return doc.fetch_status == "pending" or doc.doc_id in self.texts

    def render_titles(self, nums: list[str] | None = None) -> str:
        """선택 LLM용: [D번호] 제목 (본문 가능/요약만) 목록. nums가 없으면 풀 전체."""
        lines = []
        for num in nums or self.docs:
            lines.append(f"[{num}] {self.docs[num].title} ({'본문 가능' if self.readable(num) else '요약만'})")
        return "\n".join(lines)

    def render_slot(self, slot: str, exclude: set[str]) -> tuple[list[str], str]:
        """[37] 선택 LLM용: 이 슬롯 검색어로 나온 문서 중 exclude(이미 읽거나 이번 바퀴에 고른 문서)를 뺀 목록.

        [38] 본문 가능 문서를 앞에 두고, MIN_READABLE건 이상이면 요약만 있는 문서는 뺀다.
        "(본문 가능) 우선" 규칙만으로는 제목이 좋은 네이버 문서(요약만)를 골라 카드가 0장 나옴.
        """
        nums = [n for n, slots in self.slots_of.items() if slot in slots and n not in exclude]
        readable = [n for n in nums if self.readable(n)]
        nums = readable if len(readable) >= MIN_READABLE else readable + [n for n in nums if n not in readable]
        return nums, self.render_titles(nums) if nums else ""

    def render_doc(self, num: str, slot: str | None = None) -> str:
        """읽기 LLM용: 본문 전체, 없으면 요약. 본문은 자르지 않는다. slot = 이 문서를 고른 가이드 슬롯 ([37])."""
        doc = self.docs[num]
        text = self.texts.get(doc.doc_id)
        body = f"본문:\n{text}" if text is not None else f"요약: {doc.snippet}"
        picked = f"\n고른 슬롯: {slot}" if slot else ""
        return f"{self._header(num)}{picked}\n{body}"

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
    query_slots: dict[str, str] = Field(default_factory=dict, description="[37] 검색어 → 그 검색어를 만든 슬롯")
    slots: dict[str, int] = Field(default_factory=dict, description="[33] 슬롯별 채운 근거 문서 수")
    not_school: int = Field(default=0, description="[33] 교과목·교내프로그램 카드인데 건양대 문서가 아니라 버린 수")
    rounds: list["RoundLog"] = Field(default_factory=list, description="추출 루프 바퀴별 기록")
    timings: dict[str, float] = Field(description="단계별 소요 시간(초, 바퀴 합계)")


class RoundLog(BaseModel):
    """추출 루프 한 바퀴 기록."""
    queries: list[str]
    selected: list[str] = Field(description="이번 바퀴에 읽은 문서의 doc_id")
    picked: dict[str, list[str]] = Field(default_factory=dict, description="[37] 슬롯 → 그 슬롯을 위해 고른 doc_id")
    cards: int = Field(description="이번 바퀴에 새로 통과한 카드 수")
    slots: dict[str, int] = Field(description="[33] 바퀴가 끝났을 때 슬롯별 채운 문서 수")
    seconds: float


ExtractionResult.model_rebuild()


def plan_queries(target: str, slot: str, n: int, filled: int, used: list[str]) -> list[str]:
    """[37] 계획 LLM(새 대화): 슬롯 하나의 검색어 n개. used(이 슬롯에서 이미 쓴 검색어)를 보여 준다."""
    task, directions = SLOT_TASKS[slot]
    messages = [{"role": "system", "content": PLAN_SYSTEM},
                {"role": "user", "content": PLAN_REQUEST.format(
                    target=target, slot=slot, filled=filled, need=SLOTS[slot][1], task=task,
                    directions="\n".join(f"- {d}" for d in directions),
                    used=", ".join(f'"{q}"' for q in used) if used else "없음", n=n)}]
    msg = chat(messages, PLANNER, format=SearchPlan.model_json_schema())
    return [q.strip() for q in SearchPlan.model_validate(json.loads(msg["content"])).queries if q.strip()]


def plan_search(target: str, pool: EvidencePool, slots: dict[str, int], filled: dict[str, int],
                used: dict[str, str], log=print) -> list[str]:
    """계획: 슬롯마다 검색어를 받아 코드가 검색한다. slots = 슬롯 → 검색어 수, used = 지금까지 쓴 검색어 → 슬롯.

    같은 검색어(공백·대소문자 무시)는 같은 결과라 다시 검색하지 않는다 ([34]: 2바퀴에서 1바퀴 검색어를 그대로 되풀이).
    """
    seen = {_norm(q).lower() for q in used}
    new = []
    for slot, n in slots.items():
        mine = [q for q, s in used.items() if s == slot]
        for query in plan_queries(target, slot, n, filled.get(slot, 0), mine)[:n]:
            if _norm(query).lower() in seen:
                log(f"[{slot}] 검색어 중복(실행 안 함): {query}")
                continue
            seen.add(_norm(query).lower())
            used[query] = slot
            new.append(query)
            res = search_requirements(query, per_source=PER_SOURCE, max_fetch=0)
            nums = pool.add(res, slot)
            log(f"[{slot}] 검색: {query} → 문서 {len(nums)}건" + (f" (실패: {', '.join(res.errors)})" if res.errors else ""))
    return new


def select_docs(target: str, pool: EvidencePool, slot: str, max_select: int, exclude: set[str]) -> list[str]:
    """[37] 선택 LLM(새 대화): 슬롯 하나의 검색 결과 중 아직 안 읽은 문서에서 max_select건을 고른다. 반환: D번호 목록.

    exclude(이미 읽은 문서·이번 바퀴에 다른 슬롯이 고른 문서)는 목록에서 빼고, LLM이 골라도 코드가 다시 뺀다.
    """
    nums, titles = pool.render_slot(slot, exclude)
    if not nums:
        return []
    messages = [{"role": "system", "content": SELECT_SYSTEM},
                {"role": "user", "content": SELECT_REQUEST.format(
                    target=target, slot=slot, task=SLOT_TASKS[slot][0], titles=titles,
                    max_select=max_select, extra=SELECT_EXTRA.get(slot, ""))}]
    msg = chat(messages, SELECTOR, format=DocSelection.model_json_schema())
    selected = []
    for num in DocSelection.model_validate(json.loads(msg["content"])).selected:
        if num in nums and num not in selected:
            selected.append(num)
    return selected[:max_select]


def _batches(pool: EvidencePool, nums: list[str]) -> list[list[str]]:
    """문서를 자르지 않고 READ_BUDGET_CHARS 안에서 묶는다. 혼자 넘는 문서는 단독으로."""
    batches, cur, size = [], [], 0
    for num in nums:
        n = len(pool.render_doc(num))  # 슬롯 한 줄은 분량에 영향이 거의 없어 빼고 잰다
        if cur and size + n > READ_BUDGET_CHARS:
            batches.append(cur)
            cur, size = [], 0
        cur.append(num)
        size += n
    if cur:
        batches.append(cur)
    return batches


def read_cards(target: str, pool: EvidencePool, nums: list[str], log=print,
               slot_of: dict[str, str] | None = None) -> list[CardDraft]:
    """읽기 LLM: 고른 문서 전체를 읽고 근거 카드 초안을 낸다. slot_of = D번호 → 고른 슬롯 ([37] 참고용으로 보여 줌)."""
    slot_of = slot_of or {}
    drafts = []
    for batch in _batches(pool, nums):
        docs = "\n\n".join(pool.render_doc(n, slot_of.get(n)) for n in batch)
        messages = [{"role": "system", "content": READ_SYSTEM},
                    {"role": "user", "content": READ_REQUEST.format(target=target, docs=docs)}]
        msg = chat(messages, READER, format=CardDraftList.model_json_schema())
        try:
            drafts += CardDraftList.model_validate(json.loads(msg["content"])).cards
        except ValueError:  # [36] 출력이 반복되다 컨텍스트 끝에서 잘려 JSON이 깨지는 경우 → 그 묶음만 버림
            log(f"읽기 출력이 JSON이 아님 ({len(msg['content'])}자) → 문서 {batch} 건너뜀")
    return drafts


def make_target(job: TargetJob) -> str:
    """LLM에 줄 조사 대상 텍스트 (1단계 결과 + 학생 질문 원문)."""
    lines = [f"학교: {SCHOOL}" + (f" {job.student.major}" if job.student.major else ""),  # [33] 교내 슬롯 검색용
             f"목표 직무: {job.job_title} ({job.level})"]
    for it in job.interests:
        lines.append(f"세부 관심 분야: {it.name} (검색 키워드: {', '.join(it.keywords)})")
    for c in job.companies:  # [25] 학생 표기 대신 공식명과 검색 키워드를 넘긴다
        lines.append(f"관심 기업: {c.name} (검색 키워드: {', '.join(c.keywords)})")
    lines.append(f"직무 검색 키워드: {', '.join(job.search_keywords)}")
    if job.student.question:
        lines.append(f"학생 질문: {job.student.question}")
    return "\n".join(lines)


def is_school_doc(doc: EvidenceDoc, text: str | None) -> bool:
    """[33] 건양대 문서인지: 제목·요약·도메인·출처·본문에 SCHOOL_MARKS가 있는지."""
    hay = " ".join([doc.title, doc.snippet or "", doc.domain or "", doc.source_name or "", text or ""]).lower()
    return any(m in hay for m in SCHOOL_MARKS)


def slot_docs(cards: list[EvidenceCard]) -> dict[str, set[str]]:
    """[33] 슬롯별로 근거가 나온 고유 doc_id. 교내 슬롯 카드는 extract_evidence가 건양대 문서만 남겨 둔다."""
    docs = {slot: set() for slot in SLOTS}
    for card in cards:
        if card.category in SLOT_OF:
            docs[SLOT_OF[card.category]].add(card.doc_id)
    return docs


def lacking_slots(docs: dict[str, set[str]]) -> list[str]:
    return [slot for slot, (_, need) in SLOTS.items() if len(docs[slot]) < need]


def extract_evidence(job: TargetJob, log=print) -> ExtractionResult:
    """추출 루프: 모든 슬롯이 채워지거나 MAX_ROUNDS·MAX_SECONDS에 닿을 때까지 검색 → 선택 → 읽기 → 검증."""
    pool, timings = EvidencePool(), {"plan": 0.0, "select": 0.0, "fetch": 0.0, "read": 0.0}
    target = make_target(job)
    queries, read, selected_all, cards, rejected, rounds = [], set(), [], [], [], []
    keys: set[tuple[str, str]] = set()  # 카드 중복 확인 (doc_id, quote) — 3단계 dedup_cards와 같은 기준
    not_school = 0
    start = time.time()

    used: dict[str, str] = {}  # 검색어 → 슬롯
    filled = {slot: 0 for slot in SLOTS}

    for rnd in range(1, MAX_ROUNDS + 1):
        if rnd > 1 and time.time() - start > MAX_SECONDS:
            log(f"시간 상한 {MAX_SECONDS}초를 넘어 루프 종료")
            break
        t_round = time.time()
        lacking = [slot for slot, (_, need) in SLOTS.items() if filled[slot] < need]
        log(f"=== {rnd}바퀴 (카드 {len(keys)}장, 부족한 슬롯: {', '.join(lacking)})")

        t = time.time()
        counts = FIRST_QUERIES if rnd == 1 else NEXT_QUERIES
        new_q = plan_search(target, pool, {slot: counts.get(slot, 1) for slot in lacking}, filled, used, log)
        queries += new_q
        timings["plan"] += time.time() - t

        t = time.time()
        selected, picked, slot_of = [], {}, {}
        for slot in lacking:  # [37] 슬롯마다 따로 고른다. 이미 읽었거나 이번 바퀴에 다른 슬롯이 고른 문서는 뺀다
            n = min(SELECT_PER_SLOT, SLOTS[slot][1] - filled[slot])
            nums = select_docs(target, pool, slot, n, read | set(selected))
            picked[slot] = nums
            for num in nums:
                slot_of[num] = slot
            selected += nums
        timings["select"] += time.time() - t
        log("선택: " + " / ".join(f"{slot} {nums}" for slot, nums in picked.items()))
        if not selected:
            log("읽을 새 문서가 없어 루프 종료")
            break

        t = time.time()
        pool.fetch(selected)
        timings["fetch"] += time.time() - t
        log(f"본문 수집: {sum(pool.docs[n].doc_id in pool.texts for n in selected)}/{len(selected)}건")

        t = time.time()
        drafts = read_cards(target, pool, selected, log, slot_of)
        timings["read"] += time.time() - t

        new_cards = 0
        for draft in drafts:
            card = verify(draft, pool)
            if card is None:
                rejected.append(draft)
                continue
            # [33] 교과목·교내프로그램인데 건양대 문서가 아니면(다른 대학 교육과정 등) 버린다
            if SLOT_OF.get(card.category) in SCHOOL_SLOTS and not is_school_doc(
                    pool.docs[draft.doc.strip("[] ")], pool.texts.get(card.doc_id)):
                not_school += 1
                continue
            cards.append(card)
            if (card.doc_id, _norm(card.quote)) not in keys:
                keys.add((card.doc_id, _norm(card.quote)))
                new_cards += 1
        read.update(selected)
        selected_all += selected
        docs = slot_docs(cards)
        filled = {slot: len(d) for slot, d in docs.items()}
        rounds.append(RoundLog(queries=new_q, selected=[pool.docs[n].doc_id for n in selected],
                               picked={s: [pool.docs[n].doc_id for n in ns] for s, ns in picked.items()},
                               cards=new_cards, slots=filled, seconds=round(time.time() - t_round, 1)))
        log(f"카드 {len(drafts)}장 중 새로 통과 {new_cards}장 → 슬롯 "
            + ", ".join(f"{s} {n}/{SLOTS[s][1]}" for s, n in filled.items()))
        if not lacking_slots(docs):
            break

    return ExtractionResult(
        job=job, docs=list(pool.docs.values()), selected=[pool.docs[n].doc_id for n in selected_all],
        cards=cards, rejected=rejected, queries=queries, query_slots=used, rounds=rounds, not_school=not_school,
        slots={slot: len(d) for slot, d in slot_docs(cards).items()},
        timings={k: round(v, 1) for k, v in timings.items()},
    )
