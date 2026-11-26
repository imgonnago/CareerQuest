"""근거 풀: 검색으로 모은 문서와 본문 (2단계 슬롯 대화·0단계 정보 질문이 같이 씀).

대화 안 번호(D1, D2 …)를 붙여 관리하고, 검색 결과 목록 순서(건양대 자료 먼저)와 읽기 LLM에 줄 본문을 만든다.
"""
import re

from engine.helpers.school import is_school_doc
from engine.pipeline.slots import ACTIVITY
from engine.schemas import EvidenceDoc
from engine.tools.requirements import RequirementSearchResult, fetch_texts

MIN_READABLE = 5        # [38] 슬롯 목록에 본문 가능 문서가 이만큼 있으면 요약만 있는 문서(네이버 등)는 목록에서 뺀다
LIST_SNIPPET_CHARS = 80 # [78] 검색 결과 목록에 보여 줄 요약 앞부분
PROVIDER_RANK = {"rag": 0, "jobalio": 1}  # [84] 활동 외 슬롯 목록 순서. 나머지(웹)는 2
AGENT_LIST_MAX = 20     # [74] 검색 결과 한 번에 보여 줄 문서 수. [30] 목록이 길면 앞쪽만 보고 고른다
# [92] 활동·공모전·교과목 선택 목록 순서: 건양대 활동(RAG 비교과·교내프로그램, 웹 건양대 문서) → 건양대 교육과정 →
# 외부(RAG 공모전·대외활동·지역프로그램, 웹 비건양대 문서). 같은 단계 안에서는 RAG 먼저. ([61] 실험은 외부 → 건양대 → 교육과정)
ACTIVITY_KIND_RANK = {"비교과": 0, "교내프로그램": 0, "교육과정": 1, "수업계획서": 1, "로드맵": 1,
                      "공모전": 2, "대외활동": 2, "지역프로그램": 2}


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
        domain = "DB 자료" if doc.provider == "rag" else doc.domain
        return f"[{num}] {doc.title}\n출처: {domain}{source} · 작성일 {doc.published_at}"

    def readable(self, num: str) -> bool:
        """본문을 읽을 수 있는 문서인지 (아직 수집 전이거나 이미 수집함)."""
        doc = self.docs[num]
        return doc.fetch_status == "pending" or doc.doc_id in self.texts

    def render_titles(self, nums: list[str] | None = None) -> str:
        """선택 LLM용: [D번호] 제목 (본문 가능/요약만) 목록. nums가 없으면 풀 전체."""
        lines = []
        for num in nums or self.docs:
            doc = self.docs[num]
            label = "DB 자료" if doc.provider == "rag" else "공고 원문" if doc.provider == "jobalio" \
                else "본문 가능" if self.readable(num) else "요약만"
            # [78] 제목만으로는 관계없는 문서를 고른다. 고르는 데 쓸 앞부분만 보여 주고, 본문은 read_documents가 전체를 읽는다
            brief = re.sub(r"\s+", " ", doc.snippet or "").strip()[:LIST_SNIPPET_CHARS]
            lines.append(f"[{num}] {doc.title} ({label})" + (f" — {brief}" if brief else ""))
        return "\n".join(lines)

    def render_slot(self, slot: str, exclude: set[str]) -> tuple[list[str], str]:
        """[37] 선택 LLM용: 이 슬롯 검색어로 나온 문서 중 exclude(이미 읽거나 이번 바퀴에 고른 문서)를 뺀 목록.

        [38] 본문 가능 문서를 앞에 두고, MIN_READABLE건 이상이면 요약만 있는 문서는 뺀다.
        "(본문 가능) 우선" 규칙만으로는 제목이 좋은 네이버 문서(요약만)를 골라 카드가 0장 나옴.
        [48] RAG 문서(DB 자료)는 그보다도 앞에 둔다 (데이터 우선순위 1순위).
        """
        nums = self.order([n for n, slots in self.slots_of.items() if slot in slots and n not in exclude], slot)
        return nums, self.render_titles(nums) if nums else ""

    def order(self, nums: list[str], slot: str) -> list[str]:
        """슬롯 목록 순서로 정렬하고, 본문 가능 문서가 충분하면 요약만 있는 문서를 뺀다."""
        # 안정 정렬: [48] RAG 먼저, [57] 그 안에서 건양대 자료 먼저 (RAG 건양대 → RAG 기타 → 웹 건양대 → 웹)
        if slot == ACTIVITY:  # [92] 건양대 활동 → 건양대 교육과정 → 외부 (같은 단계 안에서는 RAG 먼저)
            nums.sort(key=lambda n: (self._activity_tier(n), self.docs[n].provider != "rag"))
        else:  # [48] RAG 먼저, [84] 그다음 잡알리오 공고 원문, [57] 같은 단계 안에서는 건양대 자료 먼저
            nums.sort(key=lambda n: (PROVIDER_RANK.get(self.docs[n].provider, 2), not is_school_doc(self.docs[n], None)))
        readable = [n for n in nums if self.readable(n)]
        return readable if len(readable) >= MIN_READABLE else readable + [n for n in nums if n not in readable]

    def _activity_tier(self, num: str) -> int:
        """[92] 활동·공모전·교과목 목록 단계: 0 건양대 활동, 1 건양대 교육과정 자료, 2 외부."""
        doc = self.docs[num]
        if doc.provider == "rag":
            return ACTIVITY_KIND_RANK.get(doc.doc_kind, 2)
        return 0 if is_school_doc(doc, None) else 2

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
