"""2단계 근거 추출: 계획 LLM(검색어) → 선택 LLM(읽을 문서) → 코드(본문 수집) → 읽기 LLM(근거 카드) → 코드(대조 검증).

[37] 계획·선택은 아직 덜 채운 가이드 슬롯마다 따로 부른다 (한 번에 시키면 일부 슬롯만 하거나 이전 계획을 되풀이 — [30]·[34])
1. 계획 LLM이 슬롯마다 검색어를 JSON으로 낸다(SearchPlan). 코드가 검색어마다 RAG(벡터 DB) → 검색 API 순으로 검색하고
   결과를 근거 풀(EvidencePool)에 쌓으며 문서마다 어느 슬롯 검색어로 나왔는지 기록한다
   (검색 API 문서의 본문은 이 단계에서 수집하지 않음. RAG 문서는 DB에 본문 전체가 있어 바로 넣음)
2. 선택 LLM이 슬롯마다 새 대화에서 그 슬롯 검색어로 나온 문서의 제목 목록을 보고 읽을 문서를 고른다 (DocSelection)
3. 코드가 고른 문서 중 본문 수집 가능한(pending) 문서만 본문을 수집한다
4. 읽기 LLM이 고른 문서의 본문 전체를 읽고 근거 카드를 JSON으로 낸다 (CardDraftList)
   문서는 자르지 않고, 한 번에 넣을 분량(READ_BUDGET_CHARS)을 넘으면 문서 단위로 나눠 여러 번 부른다
5. 코드가 quote를 원문(본문 또는 제목+요약)과 대조해 통과한 카드만 남긴다

추출 루프 (README "체크리스트 충족까지")
- [33][58] 가이드 슬롯(SLOTS: 채용 요건 4·자격증 4·활동·공모전·교과목 8·추천 포트폴리오 3)마다 몇 건 채웠는지 센다.
  채용 요건·포트폴리오는 고유 문서(doc_id) 기준 — 한 문서에서 카드가 쏟아져도 슬롯마다 1건 ([31]).
  [57] 항목 슬롯(ITEM_SLOTS: 활동·공모전·교과목, 자격증)은 서로 다른 활동·과목·자격증 수로 센다 — 교육과정 PDF 1건(과목 39개)도,
  과목마다 1건인 인호 COURSE 행도 같은 기준. 끝에 apply_caps가 추천 상한(활동·공모전·교과목 8·자격증 4)으로 줄인다
- [58] 활동·공모전·교과목 = 교내 활동(교내프로그램 카드, 건양대) → 외부 활동·공모전(대외활동 카드) → 교과목 순으로 남긴다.
  교과목은 학생이 보고 고르는 것이고 해마다 바뀌어 따로 채우지 않는다(채우느라 바퀴를 쓰지 않음). 교과목 카드 규칙은 그대로:
  [54] 학생 학년보다 낮은 학년 과목은 뺀다(학년 모르면 남김), 직무 관련 과목만, 관련 과목 중 교양 먼저
- [58] 자격증 4개는 채용 공고(채용 요건 카드가 나온 문서)에서 많이 나온 것 → 전체 문서에서 많이 나온 것 → 찾은 순
- 1~5를 한 바퀴로 보고, 채우지 못한 슬롯이 남아 있으면 다음 바퀴를 돈다
- [56] 슬롯 순서(SLOT_PRIORITY): 채용 요건·자격증(필수) → 활동·공모전·교과목 → 포트폴리오
- [57] 건양대 데이터(RAG) → 검색 API 순으로 찾고, 선택 목록·추천에서 건양대 것을 먼저 둔다.
  건양대 문서가 아닌데 교내프로그램으로 분류된 카드는 대외활동으로 고친다. 교과목은 건양대 문서의 카드만 남긴다.
- [48] RAG(건양대 교육과정·로드맵·수업계획서·비교과)가
  1순위 — 슬롯마다 맞는 문서 종류(SLOT_DOC_KINDS, 조각 metadata doc_kind)만 RAG를 먼저 찾는다.
  선택 목록에서 RAG 문서("DB 자료")를 맨 앞에 두고 같은 조건이면 먼저 고르게 한다.
  검색 API는 RAG에 없거나 부족한(오래된) 정보를 보충하는 경로. DB가 꺼져 있으면 경고만 남기고 검색 API로만 진행
- 다음 바퀴는 부족한 슬롯만 계획·선택한다. 계획 LLM에는 그 슬롯에서 이미 쓴 검색어를 주고, 같은 검색어는 코드가 버린다
- 이미 읽은 문서는 다음 바퀴 선택 목록에서 뺀다
- MAX_ROUNDS 바퀴 또는 MAX_SECONDS가 지나면 그때까지 모은 카드로 끝낸다 (바퀴 시작 전에 확인)
"""
import json
import re
import time
from collections import Counter

from pydantic import BaseModel, Field

from engine.llm import LLMConfig, chat
from engine.prompts.stage2_evidence import (
    PLAN_REQUEST, PLAN_SYSTEM, READ_REQUEST, READ_SYSTEM, SELECT_EXTRA, SELECT_REQUEST, SELECT_SYSTEM, SLOT_TASKS,
)
from engine.schemas import CardDraft, CardDraftList, DocSelection, EvidenceCard, EvidenceDoc, SearchPlan
from engine.schemas.job import TargetJob
from engine.tools.rag_search import RagSearchError, search_rag
from engine.tools.requirements import RequirementSearchResult, fetch_texts, search_requirements

# 역할별 LLM 설정
PLANNER = LLMConfig(temperature=0)  # [43] 0.3·0.2는 실행마다 검색어·문서가 바뀌어 편차·시간만 늘고 품질은 그대로 (0.2 3회: 83~175초)
SELECTOR = LLMConfig(temperature=0)
# [36] 같은 카드를 되풀이하다 컨텍스트 끝(5만 자, 400초)까지 가는 경우가 있어 출력 상한을 둔다.
# 정상 출력은 카드 25장(인용이 길면 1만 7천 자) 안팎 6천 토큰까지 나옴
READER = LLMConfig(temperature=0, num_predict=8192)

PER_SOURCE = 10         # 검색 소스별 결과 수
# 읽기 LLM 1회 입력 분량. 한국어 약 2자 = 1토큰 → 1만 2천 토큰 남짓 (num_ctx 32768)
# [42] 40,000자(문서 11건)씩 읽히면 출력이 길어져 상한에 걸리고 D번호를 헷갈림 → 25,000자로 줄임
READ_BUDGET_CHARS = 25_000

# 추출 루프
# [33] 카드 총수(TARGET_CARDS 15) 대신 가이드 슬롯별 채울 문서 수. 슬롯 이름 → (세는 카드 분류, 채울 문서 수). 합계 16
ACTIVITY = "활동·공모전·교과목"
SLOTS: dict[str, tuple[tuple[str, ...], int]] = {
    "채용 요건": (("자격요건", "우대사항", "기술스택"), 4),  # 근거 문서 수
    "자격증": (("자격증",), 4),        # [57] 서로 다른 자격증 수 (어학 성적 TOEIC·OPIc·TOPIK 포함). 추천 상한도 4
    # [58] 교내 활동 + 외부 활동·공모전 + 교과목. 서로 다른 항목 수, 추천 상한도 8 (교내 → 외부 → 교과목 순으로 남김)
    ACTIVITY: (("교내프로그램", "대외활동", "교과목"), 8),
    "추천 포트폴리오": (("프로젝트",), 3),  # [60] 서로 다른 포트폴리오 수 (한 문서의 서로 다른 포트폴리오도 각각 1개). 추천 상한도 3
}
SLOT_OF = {cat: slot for slot, (cats, _) in SLOTS.items() for cat in cats}  # 카드 분류 → 슬롯 (기업정보·기타는 없음)
SCHOOL_SLOTS: set[str] = set()  # RAG 검색에 school=건양대학교를 거는 슬롯 ([58] 교과목 슬롯이 없어져 비어 있음)
SCHOOL_CATEGORIES = {"교과목", "교내프로그램"}  # 건양대 문서에서만 나올 수 있는 카드 분류
# [57] 항목(과목·활동·자격증) 수로 세는 슬롯. 나머지는 고유 근거 문서 수
ITEM_SLOTS = {ACTIVITY, "자격증", "추천 포트폴리오"}
# [57] 추천 상한
ITEM_CAPS = {ACTIVITY: 8, "자격증": 4, "추천 포트폴리오": 3}
# [60] 활동 카드(교내프로그램·대외활동)의 item에 이 말이 하나도 없으면 활동이 아니라고 보고 버린다
# (프롬프트로 막아도 도구 "Nvidia MONAI, BIO Nemo"·시설 "의료데이터 안심존"·팀 이름이 활동 카드로 나옴)
ACTIVITY_WORD = re.compile(
    r"대회|공모|해커톤|데이터?톤|thon|챌린지|challenge|contest|캠프|camp|프로그램|program|인턴|서포터즈|기자단|멘토링|"
    r"특강|세미나|포럼|설명회|박람회|컨퍼런스|conference|워크숍|workshop|학회|동아리|봉사|연수|현장실습|산학|교육|아카데미|academy|"
    r"부트캠프|스터디|프로젝트|project|페스티벌|축제|경연", re.I)
# [58] 활동·공모전·교과목 안에서 남기는 순서 (작을수록 먼저). 교과목은 그 안에서 교양 → 전공 → 모름
# [61] 외부 활동·공모전(검색 API) → 건양대 교내 활동 → 교과목. [58]~[60]은 교내 → 외부 → 교과목이었음
ACTIVITY_RANK = {"대외활동": 0, "교내프로그램": 1, "교과목": 2}
# [59] 루프에서 교과목은 활동·공모전·교과목 8칸 중 이만큼까지만 채운 것으로 센다. 교육과정 1건에서 과목 6개가 한 번에 나와
# 1바퀴에 6칸이 차서 활동 검색이 멈춘 일이 있음 (최종 8개는 apply_caps가 교내 → 외부 → 교과목 순으로 남김)
COURSE_FILL_MAX = 3
# [59] 건양대 문서의 외부 활동 카드인데 item·quote에 이 말이 있으면 건양대 행사로 보고 교내프로그램으로 고친다
SCHOOL_EVENT_MARK = "건양대"
# [57] 활동(교내프로그램·대외활동)은 문서 하나에서 이만큼만 센다. 공모전 공고 1건의 세부(심사 기준·상 이름·혜택)를 활동 여러 개로 써서
# 문서 1건이 활동 6개를 채운 일이 있음 (모집 공고 모음 글이라도 2개까지). 교과목은 교육과정 1건에 과목이 많아 제한 없음
ACTIVITY_PER_DOC = 2
# [56] 계획·선택·읽기 순서 (출력이 잘리거나 시간 상한에 닿으면 뒤쪽 슬롯이 먼저 빠짐)
SLOT_PRIORITY = ("채용 요건", "자격증", ACTIVITY, "추천 포트폴리오")
SCHOOL = "건양대학교"
SCHOOL_MARKS = ("건양", "konyang")  # 건양대 문서 판정: 제목·요약·도메인·출처·본문에 이 말이 있으면
# [37] 슬롯별 검색어 수 (1바퀴, 2바퀴부터). [57] 활동·공모전은 건양대 비교과·건양대 대회·외부 공모전 여러 방면으로
# [58] 활동·공모전·교과목: 1바퀴는 건양대 교내 활동·외부 공모전·대외활동·건양대 교과목 네 방면
FIRST_QUERIES = {"채용 요건": 2, ACTIVITY: 4}  # 없는 슬롯은 1
NEXT_QUERIES = {ACTIVITY: 2}                   # 없는 슬롯은 1
SELECT_PER_SLOT = 2     # 슬롯마다 한 바퀴에 고를 문서 상한 (채울 수가 1 남았으면 1)
# [54] 항목이 짧은 RAG 문서(과목·프로그램·대회 하나짜리)가 나오는 슬롯은 RAG 문서를 한 번에 더 고른다
# (수백 자라 읽기 부담이 작고, 여러 항목을 비교해야 함). 웹 문서는 그대로 SELECT_PER_SLOT까지
RAG_WIDE_SLOTS = {ACTIVITY}
SCHOOL_SELECT_PER_SLOT = 6
COURSE_CODE = re.compile(r"\b\d{5}[A-Z]\b")  # 건양대 교과목 코드 (61922A, 00076K). 과목 중복 판단에 씀
MIN_READABLE = 5        # [38] 슬롯 목록에 본문 가능 문서가 이만큼 있으면 요약만 있는 문서(네이버 등)는 목록에서 뺀다
MAX_CARDS_PER_DOC = 8   # [43] 문서 하나에서 남길 카드 상한 (RAG 구현기 하나에서 기술 이름 20개가 쏟아져 표를 덮음). 프롬프트에도 적음
RAG_K = 3               # [48] 검색어마다 가져올 RAG 문서 수 (문서 단위. 교육과정 1건이 2만 자라 많이 주면 읽기 분량만 늘어남)
RAG_K_SCHOOL = 6        # [54] RAG_WIDE_SLOTS는 더 (과목마다 문서 1건인 COURSE 행, 프로그램마다 1건인 PROGRAM 행)
# [48] 슬롯마다 찾을 RAG 문서 종류 (조각 metadata doc_kind — ingest --doc-kind 또는 index.py가 인호 행에서 정함). 유사도만으로는 못 가름 —
# "의료 AI 개발자 자격증"(0.60)·"건양대 의료 AI 경진대회"(0.62)가 교육과정 문서에 맞은 교내 프로그램 검색어(0.60)보다 높았음.
# 여기 없는 종류로 적재한 문서는 2단계가 찾지 않는다. 종류 이름은 수집(인호)과 맞출 것 (problem.txt [52])
SLOT_DOC_KINDS: dict[str, list[str]] = {
    "채용 요건": ["채용공고"],
    "자격증": ["자격증"],
    # [57][58] 건양대 비교과·교내 대회, 지역 프로그램, 교육과정. 선택 목록은 ACTIVITY_KIND_RANK 순, 추천은 ACTIVITY_RANK 순
    ACTIVITY: ["비교과", "교내프로그램", "공모전", "대외활동", "지역프로그램", "교육과정", "수업계획서", "로드맵"],
    "추천 포트폴리오": ["프로젝트"],
}
# [61] 활동·공모전·교과목 선택 목록 순서: 외부(검색 API 문서, RAG 공모전·지역 프로그램) → 건양대(웹 건양대 문서, RAG 비교과) → 교육과정.
# [58]~[60]은 RAG 먼저·교내 먼저였음 (RAG 6건까지 골라 교육과정 등이 자리를 먼저 차지하고 외부 글은 바퀴당 2건만 읽힘)
ACTIVITY_KIND_RANK = {"공모전": 0, "대외활동": 0, "지역프로그램": 0, "비교과": 1, "교내프로그램": 1,
                      "교육과정": 2, "수업계획서": 2, "로드맵": 2}
ACTIVITY_WEB_SELECT = 4  # [61] 활동·공모전·교과목에서 한 바퀴에 고를 웹 문서 상한 (다른 슬롯은 SELECT_PER_SLOT)
MAX_ROUNDS = 6          # 바퀴 상한. [56] 실험 3회 모두 슬롯을 채우는 데 6바퀴가 필요했음
# [55] 학생에게 꼭 줄 슬롯. 시간·바퀴 상한에 닿아도 근거가 0건이면 그 슬롯만 REQUIRED_EXTRA_ROUNDS바퀴까지 더 돈다
REQUIRED_SLOTS = ("채용 요건", "자격증")
REQUIRED_EXTRA_ROUNDS = 1
# 2단계 시간 상한. 넘으면 새 바퀴를 시작하지 않음(바퀴 도중에는 안 끊어 실제로는 한 바퀴만큼 더 걸릴 수 있음).
# [56] 시간 상한 없이 학생 3명으로 잼: 다 찬 시점 148초·261초(평균 204초), 1명은 6바퀴(158초)에도 교내 활동 1/3 → 평균을 올려 최소값으로
MAX_SECONDS = 205


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
            label = "DB 자료" if doc.provider == "rag" else "본문 가능" if self.readable(num) else "요약만"
            lines.append(f"[{num}] {doc.title} ({label})")
        return "\n".join(lines)

    def render_slot(self, slot: str, exclude: set[str]) -> tuple[list[str], str]:
        """[37] 선택 LLM용: 이 슬롯 검색어로 나온 문서 중 exclude(이미 읽거나 이번 바퀴에 고른 문서)를 뺀 목록.

        [38] 본문 가능 문서를 앞에 두고, MIN_READABLE건 이상이면 요약만 있는 문서는 뺀다.
        "(본문 가능) 우선" 규칙만으로는 제목이 좋은 네이버 문서(요약만)를 골라 카드가 0장 나옴.
        [48] RAG 문서(DB 자료)는 그보다도 앞에 둔다 (데이터 우선순위 1순위).
        """
        nums = [n for n, slots in self.slots_of.items() if slot in slots and n not in exclude]
        # 안정 정렬: [48] RAG 먼저, [57] 그 안에서 건양대 자료 먼저 (RAG 건양대 → RAG 기타 → 웹 건양대 → 웹)
        if slot == ACTIVITY:  # [61] 외부 → 건양대 → 교육과정 (같은 단계 안에서는 웹 먼저)
            nums.sort(key=lambda n: (self._activity_tier(n), self.docs[n].provider == "rag"))
        else:  # [48] RAG 먼저, [57] 그 안에서 건양대 자료 먼저
            nums.sort(key=lambda n: (self.docs[n].provider != "rag", not is_school_doc(self.docs[n], None)))
        readable = [n for n in nums if self.readable(n)]
        nums = readable if len(readable) >= MIN_READABLE else readable + [n for n in nums if n not in readable]
        return nums, self.render_titles(nums) if nums else ""

    def _activity_tier(self, num: str) -> int:
        """[61] 활동·공모전·교과목 목록 단계: 0 외부, 1 건양대, 2 교육과정 자료."""
        doc = self.docs[num]
        if doc.provider == "rag":
            return ACTIVITY_KIND_RANK.get(doc.doc_kind, 0)
        return 1 if is_school_doc(doc, None) else 0

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


def _source(pool: EvidencePool, num: str) -> str:
    """대조용 원문: 본문, 없으면 제목+요약."""
    doc = pool.docs[num]
    text = pool.texts.get(doc.doc_id)
    return text if text is not None else f"{doc.title} {doc.snippet}"


# [44] 채용 전형 절차. 프롬프트로 "전형 절차는 기업정보"라고 해도 기술스택·자격요건으로 쓰는 경우가 잦아 코드가 고친다
SELECTION_STEP = re.compile(r"코딩\s*테스트|인적성|Fit\s*Test|면접|직무\s*특화\s*검사|서류\s*전형|필기\s*전형|AI\s*역량\s*검사", re.I)
SELECTION_FIX_CATEGORIES = {"기술스택", "자격요건", "우대사항", "기타"}  # 대외활동("AI 면접 경진대회") 등은 그대로


def verify(draft: CardDraft, pool: EvidencePool, batch: list[str] = ()) -> EvidenceCard | None:
    """인용이 원문에 있으면 EvidenceCard, 없으면 None.

    [42] 적힌 문서에 인용이 없으면 같이 읽은 문서(batch)에서 찾고, 한 문서에만 있으면 그 문서로 고친다(draft.doc도 고침).
    문서 10건 남짓을 한 번에 읽히면 9B 모델이 다른 문서의 인용을 한 D번호로 몰아 적는다 (19장 중 19장이 D104).
    """
    num = draft.doc.strip("[] ")
    quote = _norm(draft.quote)
    if num not in pool.docs or not quote:
        return None
    if quote not in _norm(_source(pool, num)):
        found = [n for n in batch if n != num and n in pool.docs and quote in _norm(_source(pool, n))]
        if len(found) == 1:
            num = draft.doc = found[0]
        elif draft.category == "교과목" and (fixed := _course_row(pool, num, batch, draft)):
            num, draft.quote = fixed
            draft.doc = num
        else:
            return None
    doc = pool.docs[num]
    text = pool.texts.get(doc.doc_id)
    category = draft.category
    if category in SELECTION_FIX_CATEGORIES and SELECTION_STEP.search(draft.item):
        category = "기업정보"
    grade, course_type = None, None
    if category == "교과목":
        grade, course_type = course_info(_source(pool, num), draft.quote)
    return EvidenceCard(
        doc_id=doc.doc_id, category=category, company=draft.company, item=draft.item,
        quote=draft.quote, evidence_level="fulltext" if text is not None else "snippet",
        grade=grade, course_type=course_type,
    )


def _course_row(pool: "EvidencePool", num: str, batch, draft: CardDraft) -> tuple[str, str] | None:
    """[60] 교과목 인용이 원문과 안 맞을 때: 적힌 문서와 같이 읽은 문서에서 같은 과목 코드와 과목명이 함께 있는 표 행을 찾아
    (D번호, 그 원문 줄)을 돌려준다. 한 곳에서만 찾으면 인용을 그 줄로 바꾼다 — 근거 문장은 원문 그대로.
    읽기 LLM이 표 행에 다른 행의 비고("| 요소설계")를 붙여 8과목이 모두 대조에서 떨어진 일이 있음."""
    code = COURSE_CODE.search(draft.quote) or COURSE_CODE.search(draft.item)
    name = _norm(draft.item)
    if not code or not name:
        return None
    hits = []
    for n in dict.fromkeys([num, *batch]):
        if n not in pool.docs:
            continue
        for line in _source(pool, n).split("\n"):
            if code.group() in line and name in _norm(line):
                if n == num:  # LLM이 적은 문서에 있으면 그것 (같은 과목이 교육과정 표·교과목 개요 두 문서에 다 있을 수 있음)
                    return n, line.strip()
                hits.append((n, line.strip()))
                break
    return hits[0] if len(hits) == 1 else None


GRADE_TEXT = re.compile(r"([1-6])\s*학년")
TERM = re.compile(r"^([1-6])\s*[-−]\s*[12]$")  # 이수시기 칸 "3-1"
# 표 제목: "교양필수교육과정일람표", "Q 전공 교육과정 일람표", "현장실습교과목일람표"
TABLE_TITLE = re.compile(r"(교양|전공|현장실습).{0,12}(일람표|교육과정)")


def course_info(text: str, quote: str) -> tuple[int | None, str | None]:
    """[54] 교과목 카드의 (개설 학년, 교양/전공)을 원문에서 코드로 읽는다. 못 읽으면 None.

    9B 모델은 표 행에서 학점 칸을 학년으로 읽는 경우가 많아(4학년 과목 2개를 3학년으로) LLM 값보다 이것을 먼저 쓴다.
    - 인용이 든 줄을 찾고, 위로 올라가며 칸 수가 같고 "학년" 칸(또는 "이수 시기" 칸)이 있는 머리글 줄을 찾아 그 칸 값을 읽는다
      (engine/rag/table.py가 빈 칸 자리까지 남겨 칸 위치가 맞음)
    - 표가 아니면 그 줄의 "3학년"
    - 교양/전공: 위쪽에서 가장 가까운 표 제목(교양… 일람표 → 교양, 전공·현장실습 → 전공)
    """
    lines = text.split("\n")
    q = _norm(quote)
    code = COURSE_CODE.search(quote)
    at = next((i for i, line in enumerate(lines) if q and q in _norm(line)), None)
    if at is None and code:
        at = next((i for i, line in enumerate(lines) if code.group() in line), None)
    if at is None:
        return None, None
    row = [c.strip() for c in lines[at].split("|")]
    grade = None
    for i in range(at - 1, max(at - 80, -1), -1):
        head = [c.strip().replace(" ", "") for c in lines[i].split("|")]
        if len(head) != len(row) or len(row) < 3:
            continue
        if "학년" in head:
            v = row[head.index("학년")]
            grade = int(v) if v.isdigit() and 1 <= int(v) <= 6 else None
            break
        if "이수시기" in head and (m := TERM.match(row[head.index("이수시기")])):
            grade = int(m.group(1))
            break
    if grade is None and (m := GRADE_TEXT.search(lines[at])):
        grade = int(m.group(1))
    course_type = None
    for i in range(at, -1, -1):
        if (m := TABLE_TITLE.search(lines[i])) and len(lines[i]) <= 40:
            course_type = "교양" if m.group(1) == "교양" else "전공"
            break
    return grade, course_type


def course_key(card: EvidenceCard) -> str:
    """[54] 같은 과목 판단: 교과목 코드가 있으면 코드, 없으면 공백 뺀 과목명."""
    m = COURSE_CODE.search(card.quote)
    return m.group() if m else _norm(card.item).lower()


CERT_SPLIT = re.compile(r"\s*(?:[,/·]|및|또는)\s*")


def item_keys(card: EvidenceCard) -> list[str]:
    """[57] 항목 슬롯에서 카드가 채우는 항목. 교과목은 course_key, 자격증은 item을 쉼표·슬래시로 나눈 이름들
    ("ADsP / AICE Associate" → 2개), 활동은 공백 뺀 item."""
    if card.category == "교과목":
        return [course_key(card)]
    if card.category == "자격증":
        return [_norm(n).lower() for n in CERT_SPLIT.split(card.item) if _norm(n)] or [_norm(card.item).lower()]
    return [_norm(card.item).lower()]


def _counted_keys(cards: list[EvidenceCard]) -> list[tuple[EvidenceCard, list[str]]]:
    """카드마다 세는 항목. [57] 활동(교내프로그램·대외활동)은 문서마다 처음 나온 ACTIVITY_PER_DOC개까지만 (나머지 카드는 빈 목록)."""
    per_doc: dict[str, list[str]] = {}
    out = []
    for c in cards:
        keys = item_keys(c)
        if c.category in ("교내프로그램", "대외활동"):
            seen = per_doc.setdefault(c.doc_id, [])
            keys = [k for k in keys if k in seen or (len(seen) < ACTIVITY_PER_DOC and not seen.append(k))]
        out.append((c, keys))
    return out


def apply_caps(cards: list[EvidenceCard]) -> tuple[list[EvidenceCard], dict[str, int]]:
    """[57][58][60] 추천 상한으로 줄인다. 반환: (남긴 카드, 슬롯별 뺀 항목 수). 포트폴리오 3은 여러 문서에 나온 순 → 찾은 순.
    - 활동·공모전·교과목 8: 교내프로그램 → 대외활동 → 교과목(교양 → 전공 → 모름), 같은 순위 안에서는 찾은 순
    - 자격증 4: 채용 공고(채용 요건 카드가 나온 문서)에 나온 문서 수 → 전체 문서 수 → 찾은 순"""
    counted = _counted_keys(cards)
    course_rank = {"교양": 0, "전공": 1, None: 2}
    posting_docs = {c.doc_id for c in cards if SLOT_OF.get(c.category) == "채용 요건"}

    def activity_ranked() -> list[str]:
        order: dict[str, int] = {}
        best: dict[str, tuple] = {}
        for c, ks in counted:
            if SLOT_OF.get(c.category) != ACTIVITY:
                continue
            r = (ACTIVITY_RANK[c.category], course_rank[c.course_type] if c.category == "교과목" else 0)
            for k in ks:
                order.setdefault(k, len(order))
                best[k] = min(best.get(k, r), r)
        return sorted(order, key=lambda k: (best[k], order[k]))

    def cert_ranked() -> list[str]:
        order: dict[str, int] = {}
        docs: dict[str, set[str]] = {}
        for c, ks in counted:
            if c.category != "자격증":
                continue
            for k in ks:
                order.setdefault(k, len(order))
                docs.setdefault(k, set()).add(c.doc_id)
        return sorted(order, key=lambda k: (-len(docs[k] & posting_docs), -len(docs[k]), order[k]))

    def portfolio_ranked() -> list[str]:  # [60] 여러 문서에 나온 것 → 찾은 순
        order: dict[str, int] = {}
        docs: dict[str, set[str]] = {}
        for c, ks in counted:
            if c.category != "프로젝트":
                continue
            for k in ks:
                order.setdefault(k, len(order))
                docs.setdefault(k, set()).add(c.doc_id)
        return sorted(order, key=lambda k: (-len(docs[k]), order[k]))

    lists = {ACTIVITY: activity_ranked(), "자격증": cert_ranked(), "추천 포트폴리오": portfolio_ranked()}
    keep = {slot: set(ks[:ITEM_CAPS[slot]]) for slot, ks in lists.items()}
    capped = {slot: len(ks) - len(keep[slot]) for slot, ks in lists.items() if len(ks) > ITEM_CAPS[slot]}
    out = [c for c, ks in counted if SLOT_OF.get(c.category) not in keep
           or any(k in keep[SLOT_OF[c.category]] for k in ks)]
    return out, capped


class ExtractionResult(BaseModel):
    job: TargetJob = Field(description="1단계 결과")
    docs: list[EvidenceDoc] = Field(description="근거 풀 전체 (D번호 순)")
    selected: list[str] = Field(description="계획 LLM이 읽기로 고른 문서의 doc_id")
    cards: list[EvidenceCard] = Field(description="원문 대조를 통과한 카드")
    rejected: list[CardDraft] = Field(description="원문에서 인용을 찾지 못해 버린 카드")
    queries: list[str]
    query_slots: dict[str, str] = Field(default_factory=dict, description="[37] 검색어 → 그 검색어를 만든 슬롯")
    slots: dict[str, int] = Field(default_factory=dict, description="[33] 슬롯별 채운 수 (상한 적용 후). 채용 요건·포트폴리오는 고유 근거 문서 수, [57] 항목 슬롯은 서로 다른 과목·활동·자격증 수")
    not_school: int = Field(default=0, description="[33] 교과목 카드인데 건양대 문서가 아니라 버린 수")
    over_cap: int = Field(default=0, description="[43] 문서당 카드 상한(MAX_CARDS_PER_DOC)을 넘어 버린 수")
    rag_error: str | None = Field(default=None, description="[48] RAG 검색 실패 사유 (DB 꺼짐 등). 있으면 검색 API로만 진행함")
    past_grade: int = Field(default=0, description="[54] 학생 학년보다 낮은 학년 교과목이라 버린 카드 수")
    capped: dict[str, int] = Field(default_factory=dict, description="[57][58] 추천 상한(활동·공모전·교과목 8·자격증 4)을 넘어 뺀 항목 수")
    relabeled: int = Field(default=0, description="[57] 건양대 문서가 아닌 교내프로그램 카드를 대외활동으로 고친 수")
    not_course: int = Field(default=0, description="[60] 학교 DB 문서도 아니고 과목 코드도 없어 버린 교과목 카드 수")
    not_activity: int = Field(default=0, description="[60] item에 활동을 나타내는 말이 없어 버린 활동 카드 수")
    to_school: int = Field(default=0, description="[59] 건양대 문서의 대외활동 카드 중 item·quote에 \"건양대\"가 있어 교내프로그램으로 고친 수")
    rounds: list["RoundLog"] = Field(default_factory=list, description="추출 루프 바퀴별 기록")
    timings: dict[str, float] = Field(description="단계별 소요 시간(초, 바퀴 합계)")
    # 5단계 검증(마감일 확인)용 본문. 근거 풀 항목(EvidenceDoc)에는 본문을 넣지 않는 규칙이라 따로 둔다.
    # 카드가 나온 문서만 담고, JSON 출력(model_dump)에는 넣지 않는다
    texts: dict[str, str] = Field(default_factory=dict, exclude=True, description="doc_id → 본문 (카드가 나온 문서만)")


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


class RagState:
    """[48] 한 번의 2단계 실행 동안 RAG 사용 여부. DB 오류가 한 번 나면 그 실행에서는 더 부르지 않는다."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.error: str | None = None

    def search(self, query: str, slot: str, log=print) -> RequirementSearchResult | None:
        if not self.enabled or not SLOT_DOC_KINDS.get(slot):
            return None
        filters = {"school": SCHOOL} if slot in SCHOOL_SLOTS else None
        try:
            k = RAG_K_SCHOOL if slot in RAG_WIDE_SLOTS else RAG_K
            return search_rag(query, k=k, filters=filters, doc_kinds=SLOT_DOC_KINDS[slot])
        except RagSearchError as e:
            self.enabled, self.error = False, str(e)
            log(f"RAG 검색 실패 — 이번 실행은 검색 API로만 진행: {e}")
            return None


def plan_search(target: str, pool: EvidencePool, slots: dict[str, int], filled: dict[str, int],
                used: dict[str, str], log=print, rag: RagState | None = None) -> list[str]:
    """계획: 슬롯마다 검색어를 받아 코드가 검색한다. slots = 슬롯 → 검색어 수, used = 지금까지 쓴 검색어 → 슬롯.

    같은 검색어(공백·대소문자 무시)는 같은 결과라 다시 검색하지 않는다 ([34]: 2바퀴에서 1바퀴 검색어를 그대로 되풀이).
    [48] 검색어마다 RAG를 먼저 찾아 풀에 넣고(D번호가 앞), 이어서 검색 API 결과를 넣는다.
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
            if rag is not None and (hit := rag.search(query, slot, log)) is not None:
                nums = pool.add(hit, slot)
                log(f"[{slot}] RAG: {query} → 문서 {len(nums)}건" + (f" {nums}" if nums else ""))
            res = search_requirements(query, per_source=PER_SOURCE, max_fetch=0)
            nums = pool.add(res, slot)
            log(f"[{slot}] 검색: {query} → 문서 {len(nums)}건" + (f" (실패: {', '.join(res.errors)})" if res.errors else ""))
    return new


def select_docs(target: str, pool: EvidencePool, slot: str, max_select: int, exclude: set[str]) -> list[str]:
    """[37] 선택 LLM(새 대화): 슬롯 하나의 검색 결과 중 아직 안 읽은 문서에서 max_select건을 고른다. 반환: D번호 목록.

    exclude(이미 읽은 문서·이번 바퀴에 다른 슬롯이 고른 문서)는 목록에서 빼고, LLM이 골라도 코드가 다시 뺀다.
    [54] RAG_WIDE_SLOTS 목록에 RAG 문서가 있으면 SCHOOL_SELECT_PER_SLOT건까지 고르게 하되, 웹 문서는 max_select건까지만 남긴다.
    """
    nums, titles = pool.render_slot(slot, exclude)
    if not nums:
        return []
    web_cap = max_select
    if slot == ACTIVITY:  # [61] 외부 활동을 먼저 찾으므로 웹 문서를 더 고른다
        web_cap = max_select = max(max_select, ACTIVITY_WEB_SELECT)
    if slot in RAG_WIDE_SLOTS and any(pool.docs[n].provider == "rag" for n in nums):
        max_select = max(max_select, SCHOOL_SELECT_PER_SLOT)
    messages = [{"role": "system", "content": SELECT_SYSTEM},
                {"role": "user", "content": SELECT_REQUEST.format(
                    target=target, slot=slot, task=SLOT_TASKS[slot][0], titles=titles,
                    max_select=max_select, extra=SELECT_EXTRA.get(slot, ""))}]
    msg = chat(messages, SELECTOR, format=DocSelection.model_json_schema())
    selected = []
    web = 0
    for num in DocSelection.model_validate(json.loads(msg["content"])).selected:
        if num in nums and num not in selected:
            if pool.docs[num].provider != "rag":
                if web >= web_cap:
                    continue
                web += 1
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


def _salvage_cards(content: str) -> list[CardDraft]:
    """[36] 잘린 JSON에서 끝까지 완성된 카드 객체만 살린다. 되풀이된 카드(같은 문서·인용)는 한 번만."""
    dec, out, seen = json.JSONDecoder(), [], set()
    i = content.find("[")
    while i != -1:
        j = content.find("{", i)
        if j == -1:
            break
        try:
            obj, i = dec.raw_decode(content, j)
            card = CardDraft.model_validate(obj)
        except ValueError:  # 잘린 마지막 카드, 스키마에 안 맞는 객체
            break
        if (card.doc, card.quote) not in seen:
            seen.add((card.doc, card.quote))
            out.append(card)
    return out


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
        except ValueError:  # [36] 출력이 반복되다 num_predict에서 잘려 JSON이 깨지는 경우 → 완성된 카드만 살림
            saved = _salvage_cards(msg["content"])
            drafts += saved
            log(f"읽기 출력이 잘림 ({len(msg['content'])}자) → 완성된 카드 {len(saved)}장만 살림 (문서 {batch})")
    return drafts


def make_target(job: TargetJob) -> str:
    """LLM에 줄 조사 대상 텍스트 (1단계 결과 + 학생 질문 원문)."""
    lines = [f"학교: {SCHOOL}" + (f" {job.student.major}" if job.student.major else "")]  # [33] 교내 슬롯 검색용
    if job.student.grade:  # [54] 지난 학년 과목은 추천하지 않음
        lines.append(f"학년: {job.student.grade}학년 ({job.student.grade}학년 이상 과목만 추천)")
    lines.append(f"목표 직무: {job.job_title} ({job.level})")
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
    """[33] 슬롯별로 채운 것: 채용 요건·포트폴리오는 근거가 나온 고유 doc_id, [57] 항목 슬롯은 서로 다른 항목(item_keys).
    교과목 카드는 extract_evidence가 건양대 문서·학년 맞는 과목만 남겨 둔다."""
    docs = {slot: set() for slot in SLOTS}
    for card, keys in _counted_keys(cards):
        if card.category in SLOT_OF:
            slot = SLOT_OF[card.category]
            if card.category == "교과목":  # [59] fill_counts가 따로 세도록 표시
                keys = [f"교과목:{k}" for k in keys]
            docs[slot].update(keys if slot in ITEM_SLOTS else [card.doc_id])
    return docs


def fill_counts(docs: dict[str, set[str]]) -> dict[str, int]:
    """슬롯별 채운 수. [59] 활동·공모전·교과목에서 교과목은 COURSE_FILL_MAX개까지만 센다."""
    filled = {slot: len(d) for slot, d in docs.items()}
    courses = sum(1 for k in docs[ACTIVITY] if k.startswith("교과목:"))
    filled[ACTIVITY] -= max(0, courses - COURSE_FILL_MAX)
    return filled


def lacking_slots(docs: dict[str, set[str]]) -> list[str]:
    filled = fill_counts(docs)
    return [slot for slot, (_, need) in SLOTS.items() if filled[slot] < need]


def extract_evidence(job: TargetJob, log=print, use_rag: bool = True) -> ExtractionResult:
    """추출 루프: 모든 슬롯이 채워지거나 MAX_ROUNDS·MAX_SECONDS에 닿을 때까지 검색 → 선택 → 읽기 → 검증.

    use_rag=False면 RAG 없이 검색 API로만 (비교 시험용)."""
    pool, timings = EvidencePool(), {"plan": 0.0, "select": 0.0, "fetch": 0.0, "read": 0.0}
    target = make_target(job)
    queries, read, selected_all, cards, rejected, rounds = [], set(), [], [], [], []
    keys: set[tuple[str, str]] = set()  # 카드 중복 확인 (doc_id, quote) — 3단계 dedup_cards와 같은 기준
    not_school = 0
    over_cap = 0
    past_grade = 0
    relabeled = 0
    to_school = 0
    not_course = 0
    not_activity = 0
    grade = job.student.grade
    start = time.time()

    used: dict[str, str] = {}  # 검색어 → 슬롯
    rag = RagState(enabled=use_rag)
    filled = {slot: 0 for slot in SLOTS}
    extra = 0  # [55] 상한을 넘어 필수 슬롯만 더 돈 바퀴 수

    for rnd in range(1, MAX_ROUNDS + REQUIRED_EXTRA_ROUNDS + 1):
        # [55] 필수 슬롯(채용 요건·자격증)은 먼저 계획·선택·읽기 (출력이 잘려도 앞쪽 카드는 살아남음)
        lacking = sorted((slot for slot, (_, need) in SLOTS.items() if filled[slot] < need),
                         key=SLOT_PRIORITY.index)  # [56] 필수 슬롯 → 활동·공모전 → … → 교과목
        over_time = rnd > 1 and time.time() - start > MAX_SECONDS
        if over_time or rnd > MAX_ROUNDS:
            # [55] 상한에 닿아도 필수 슬롯이 비어 있으면 그 슬롯만 한 바퀴 더
            empty = [slot for slot in lacking if slot in REQUIRED_SLOTS and filled[slot] == 0]
            if not empty or extra >= REQUIRED_EXTRA_ROUNDS:
                why = f"시간 상한 {MAX_SECONDS}초" if over_time else "바퀴 상한"
                log(f"{why}로 루프 종료" + (f" (필수 슬롯 비어 있음: {', '.join(empty)})" if empty else ""))
                break
            extra += 1
            log(f"상한에 닿았지만 필수 슬롯이 비어 있어 그 슬롯만 한 바퀴 더: {', '.join(empty)}")
            lacking = empty
        t_round = time.time()
        log(f"=== {rnd}바퀴 (카드 {len(keys)}장, 부족한 슬롯: {', '.join(lacking)})")

        t = time.time()
        counts = FIRST_QUERIES if rnd == 1 else NEXT_QUERIES
        new_q = plan_search(target, pool, {slot: counts.get(slot, 1) for slot in lacking}, filled, used, log, rag)
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
        per_doc = Counter(c.doc_id for c in cards)
        for draft in drafts:
            card = verify(draft, pool, selected)
            if card is None:
                rejected.append(draft)
                continue
            # [33] 교과목인데 건양대 문서가 아니면(다른 대학 교육과정 등) 버린다.
            # [57] 교내프로그램인데 건양대 문서가 아니면 외부 활동이므로 대외활동으로 고친다
            if card.category in SCHOOL_CATEGORIES and not is_school_doc(
                    pool.docs[draft.doc.strip("[] ")], pool.texts.get(card.doc_id)):
                if card.category == "교과목":
                    not_school += 1
                    continue
                card.category = "대외활동"
                relabeled += 1
            # [59] 건양대 문서의 외부 활동 카드인데 건양대 행사면(예: "AI·SW중심대학 디지털 경진대회", 건양대 SW중심대학) 교내프로그램
            elif card.category == "대외활동" and (SCHOOL_EVENT_MARK in card.item or SCHOOL_EVENT_MARK in card.quote) \
                    and is_school_doc(pool.docs[draft.doc.strip("[] ")], pool.texts.get(card.doc_id)):
                card.category = "교내프로그램"
                to_school += 1
            # [60] 교과목은 학교 DB(RAG) 문서이거나 quote에 과목 코드가 있을 때만 (웹 뉴스의 "의료 AI"·"데이터 분석" 같은 문구가 과목으로 들어옴)
            if card.category == "교과목" and pool.docs[draft.doc.strip("[] ")].provider != "rag" \
                    and not COURSE_CODE.search(card.quote):
                not_course += 1
                continue
            # [60] 활동 카드인데 item에 활동을 나타내는 말(대회·공모전·캠프·프로그램·인턴 …)이 없으면 버린다
            if card.category in ("교내프로그램", "대외활동") and not ACTIVITY_WORD.search(card.item):
                not_activity += 1
                continue
            # [54] 학생 학년보다 낮은 학년 과목 (학년을 모르면 남김)
            if card.category == "교과목" and grade and card.grade and card.grade < grade:
                past_grade += 1
                continue
            # [43] 문서당 카드 상한. LLM이 중요한 것부터 쓰게 했으므로 앞에서부터 남긴다
            if per_doc[card.doc_id] >= MAX_CARDS_PER_DOC:
                over_cap += 1
                continue
            per_doc[card.doc_id] += 1
            cards.append(card)
            if (card.doc_id, _norm(card.quote)) not in keys:
                keys.add((card.doc_id, _norm(card.quote)))
                new_cards += 1
        read.update(selected)
        selected_all += selected
        docs = slot_docs(cards)
        filled = fill_counts(docs)
        rounds.append(RoundLog(queries=new_q, selected=[pool.docs[n].doc_id for n in selected],
                               picked={s: [pool.docs[n].doc_id for n in ns] for s, ns in picked.items()},
                               cards=new_cards, slots=filled, seconds=round(time.time() - t_round, 1)))
        log(f"카드 {len(drafts)}장 중 새로 통과 {new_cards}장 → 슬롯 "
            + ", ".join(f"{s} {n}/{SLOTS[s][1]}" for s, n in filled.items()))
        if not lacking_slots(docs):
            break

    cards, capped = apply_caps(cards)
    if capped:
        log("추천 상한으로 뺀 항목: " + ", ".join(f"{s} {n}개" for s, n in capped.items()))
    return ExtractionResult(
        job=job, docs=list(pool.docs.values()), selected=[pool.docs[n].doc_id for n in selected_all],
        past_grade=past_grade, capped=capped, relabeled=relabeled, to_school=to_school,
        not_course=not_course, not_activity=not_activity,
        cards=cards, rejected=rejected, queries=queries, query_slots=used, rounds=rounds, not_school=not_school, over_cap=over_cap,
        rag_error=rag.error,
        slots=fill_counts(slot_docs(cards)),
        timings={k: round(v, 1) for k, v in timings.items()},
        texts={d: t for d, t in pool.texts.items() if d in {c.doc_id for c in cards}},
    )
