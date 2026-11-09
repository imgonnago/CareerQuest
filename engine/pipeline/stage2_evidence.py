"""2단계 근거 추출: 도구 호출 에이전트(LLM ↔ 도구 루프) → 코드(대조 검증).

[74] LLM이 도구를 직접 호출한다 (README "LLM이 도구를 직접 골라 근거를 보충"). 가이드 슬롯마다 새 대화에서:
1. 에이전트 LLM이 도구를 고른다 (Ollama tool calling, 어떤 도구를 몇 번 부를지·언제 끝낼지 LLM이 판단)
   - search_rag: 건양대·기관 자료 DB (그 슬롯의 문서 종류만, 코드가 건다)
   - search_requirements: 네이버·카카오 검색 API
   - read_documents: 목록에서 고른 문서를 읽는다
2. 코드가 도구를 실행하고 결과를 대화에 돌려준다. 검색 결과는 [D번호]·제목 목록, 읽기 결과는 카드 요약과 슬롯 현황뿐이다
   ([9] 본문을 대화에 쌓으면 컨텍스트가 넘친다)
3. read_documents 안에서 읽기 LLM(새 대화)이 본문 전체를 읽어 근거 카드를 내고(CardDraftList), 코드가 quote를 원문과 대조한다
   문서는 자르지 않고, 한 번에 넣을 분량(READ_BUDGET_CHARS)을 넘으면 문서 단위로 나눠 여러 번 부른다
4. 슬롯이 차면 코드가 대화를 끝낸다. LLM이 부족한 채 끝내려 하면 현황을 알려 주고 한 번 더 묻는다

코드가 지키는 것 (도구 실행·저장·세기·검증): 슬롯 순서, 같은 검색어 차단([34]), 한 번에 읽는 문서 수, 턴·시간 상한,
필수 슬롯이 비었을 때의 추가 대화([55]), 인용 대조와 카드 걸러내기.

슬롯 채우기 (README "체크리스트 충족까지")
- [33][58] 가이드 슬롯(SLOTS: 채용 요건 4·자격증 4·활동·공모전·교과목 8·추천 포트폴리오 3)마다 몇 건 채웠는지 센다.
  채용 요건·포트폴리오는 고유 문서(doc_id) 기준 — 한 문서에서 카드가 쏟아져도 슬롯마다 1건 ([31]).
  [57] 항목 슬롯(ITEM_SLOTS: 활동·공모전·교과목, 자격증)은 서로 다른 활동·과목·자격증 수로 센다 — 교육과정 PDF 1건(과목 39개)도,
  과목마다 1건인 인호 COURSE 행도 같은 기준. 끝에 apply_caps가 추천 상한(활동·공모전·교과목 8·자격증 4)으로 줄인다
- [58] 활동·공모전·교과목 = 교내 활동(교내프로그램 카드, 건양대) → 외부 활동·공모전(대외활동 카드) → 교과목 순으로 남긴다.
  교과목은 학생이 보고 고르는 것이고 해마다 바뀌어 따로 채우지 않는다(채우느라 바퀴를 쓰지 않음). 교과목 카드 규칙은 그대로:
  [54] 학생 학년보다 낮은 학년 과목은 뺀다(학년 모르면 남김), 직무 관련 과목만, 관련 과목 중 교양 먼저
- [58] 자격증 4개는 채용 공고(채용 요건 카드가 나온 문서)에서 많이 나온 것 → 전체 문서에서 많이 나온 것 → 찾은 순
- [56] 슬롯 순서(SLOT_PRIORITY): 채용 요건·자격증(필수) → 활동·공모전·교과목 → 포트폴리오
- [57] 건양대 데이터(RAG) → 검색 API 순으로 찾고, 선택 목록·추천에서 건양대 것을 먼저 둔다.
  건양대 문서가 아닌데 교내프로그램으로 분류된 카드는 대외활동으로 고친다. 교과목은 건양대 문서의 카드만 남긴다.
- [48] RAG(건양대 교육과정·로드맵·수업계획서·비교과)가
  1순위 — 슬롯마다 맞는 문서 종류(SLOT_DOC_KINDS, 조각 metadata doc_kind)만 RAG를 먼저 찾는다.
  선택 목록에서 RAG 문서("DB 자료")를 맨 앞에 두고 같은 조건이면 먼저 고르게 한다.
  검색 API는 RAG에 없거나 부족한(오래된) 정보를 보충하는 경로. DB가 꺼져 있으면 경고만 남기고 검색 API로만 진행
- 이미 찬 슬롯은 대화를 열지 않는다 (앞 슬롯을 위해 읽은 문서의 카드가 뒤 슬롯을 채우기도 함). 이미 읽은 문서는 목록에서 뺀다
- AGENT_MAX_TURNS 턴 또는 슬롯 몫의 시간이 지나면 그 슬롯을 끝낸다 (LLM 호출 전에 확인). 남은 시간은 뒤 슬롯 몫으로 넘어간다
"""
import json
import re
import time
from collections import Counter

from pydantic import BaseModel, Field

from engine.llm import LLMConfig, chat
from engine.pipeline.agent_loop import SearchGuard, run_tool_loop
from engine.prompts.stage2_evidence import (
    AGENT_NUDGE, AGENT_REQUEST, AGENT_SYSTEM, READ_REQUEST, READ_SYSTEM, SLOT_EXTRA, SLOT_TASKS,
)
from engine.schemas import CardDraft, CardDraftList, EvidenceCard, EvidenceDoc
from engine.schemas.job import TargetJob
from engine.tools import jobalio
from engine.tools.jobalio import TOOL_SPEC as JOB_TOOL_SPEC, search_public_jobs
from engine.tools.rag_search import TOOL_SPEC as RAG_TOOL_SPEC, RagSearchError, search_rag
from engine.tools.requirements import TOOL_SPEC as WEB_TOOL_SPEC, RequirementSearchResult, fetch_texts, search_requirements

# 역할별 LLM 설정
# [74] 도구를 고르는 에이전트. [43] 0.3·0.2는 실행마다 검색어·문서가 바뀌어 편차·시간만 늘고 품질은 그대로 (0.2 3회: 83~175초)
AGENT = LLMConfig(temperature=0, num_predict=1024)
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
SELECT_PER_SLOT = 2     # read_documents 한 번에 읽을 문서 상한 (채울 수가 1 남았으면 1)
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
ACTIVITY_WEB_SELECT = 4  # [61] 활동·공모전·교과목에서 read_documents 한 번에 읽을 웹 문서 상한 (다른 슬롯은 SELECT_PER_SLOT)
AGENT_MAX_TURNS = 12    # [74] 슬롯 대화 하나의 LLM 턴 상한
AGENT_IDLE_TURNS = 2    # [74] 새로 실행된 도구가 없는 턴(같은 검색어 되풀이 등)이 이만큼 이어지면 그 슬롯을 끝낸다 ([34])
AGENT_NUDGES = 2        # [78] 슬롯이 부족한 채 끝내려 할 때 되묻는 횟수. 두 번째는 첫 되묻기 뒤 카드가 늘었을 때만
AGENT_QUERY_MAX_WORDS = 6  # [78] 이보다 긴 검색어는 실행하지 않고 줄이게 한다 ([26] 2~5단어, 조금 넘는 것은 봐줌)
LIST_SNIPPET_CHARS = 80 # [78] 검색 결과 목록에 보여 줄 요약 앞부분
SEARCH_BEFORE_READ = 3  # [81] 읽지 않고 이어서 실행한 검색이 이만큼이면 안 읽은 문서가 있는 동안 검색 도구를 뺀다 (무엇을 읽을지는 LLM이 고름)
PROVIDER_RANK = {"rag": 0, "jobalio": 1}  # [84] 활동 외 슬롯 목록 순서. 나머지(웹)는 2
UNREAD_SHOWN = 10       # [81] 검색이 막히거나 새 문서가 없을 때 다시 보여 줄 안 읽은 문서 수
AGENT_LIST_MAX = 20     # [74] 검색 결과 한 번에 보여 줄 문서 수. [30] 목록이 길면 앞쪽만 보고 고른다
# [55] 학생에게 꼭 줄 슬롯. 시간·턴 상한에 닿아도 근거가 0건이면 이 시간만큼 그 슬롯 대화를 한 번 더 연다
REQUIRED_SLOTS = ("채용 요건", "자격증")
REQUIRED_EXTRA_SECONDS = 60
# 2단계 시간 상한. 슬롯마다 채울 수에 비례해 나눈다. 진행 중인 읽기는 끊지 않아 실제로는 더 걸릴 수 있음.
# [56] 시간 상한 없이 학생 3명으로 잼: 다 찬 시점 148초·261초(평균 204초), 1명은 6바퀴(158초)에도 교내 활동 1/3 → 평균을 올려 최소값으로
MAX_SECONDS = 205


# [74] 읽기 도구. 검색 도구 정의는 각 도구 모듈(TOOL_SPEC)에 있다
READ_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "read_documents",
        "description": "검색 결과 목록에서 고른 문서의 본문 전체를 읽어 근거 카드를 만든다. "
                       "결과로 문서마다 나온 근거 항목과 슬롯 현황을 돌려준다.",
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
SEARCH_NEXT = "이 슬롯의 근거가 있을 문서를 read_documents로 고르거나, 쓸 만한 문서가 없으면 다른 검색어를 써."


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
        if slot == ACTIVITY:  # [61] 외부 → 건양대 → 교육과정 (같은 단계 안에서는 웹 먼저)
            nums.sort(key=lambda n: (self._activity_tier(n), self.docs[n].provider == "rag"))
        else:  # [48] RAG 먼저, [84] 그다음 잡알리오 공고 원문, [57] 같은 단계 안에서는 건양대 자료 먼저
            nums.sort(key=lambda n: (PROVIDER_RANK.get(self.docs[n].provider, 2), not is_school_doc(self.docs[n], None)))
        readable = [n for n in nums if self.readable(n)]
        return readable if len(readable) >= MIN_READABLE else readable + [n for n in nums if n not in readable]

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
        # [89] 원문 표에서 못 읽으면 DB course_detail 값 (인호 교과목 행은 머리글 없는 한 줄: "필수 | 1 | 2 | 80245B | …")
        if grade is None:
            grade = doc.course_grade
        if course_type is None and doc.course_type:
            course_type = "교양" if "교양" in doc.course_type else "전공"
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
    selected: list[str] = Field(description="에이전트 LLM이 읽기로 고른 문서의 doc_id")
    cards: list[EvidenceCard] = Field(description="원문 대조를 통과한 카드")
    rejected: list[CardDraft] = Field(description="원문에서 인용을 찾지 못해 버린 카드")
    queries: list[str]
    query_slots: dict[str, str] = Field(default_factory=dict, description="[37] 검색어 → 그 검색어를 쓴 슬롯")
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
    rounds: list["RoundLog"] = Field(default_factory=list, description="[74] 슬롯 대화별 기록")
    timings: dict[str, float] = Field(description="역할별 소요 시간(초, 합계): agent·search·fetch·read")
    # 5단계 검증(마감일 확인)용 본문. 근거 풀 항목(EvidenceDoc)에는 본문을 넣지 않는 규칙이라 따로 둔다.
    # 카드가 나온 문서만 담고, JSON 출력(model_dump)에는 넣지 않는다
    texts: dict[str, str] = Field(default_factory=dict, exclude=True, description="doc_id → 본문 (카드가 나온 문서만)")


class RoundLog(BaseModel):
    """[74] 슬롯 대화 하나의 기록."""
    slot: str
    queries: list[str]
    selected: list[str] = Field(description="이 대화에서 읽은 문서의 doc_id")
    tool_calls: list[dict] = Field(default_factory=list, description="LLM이 부른 도구 순서: {turn, tool, args, result}")
    turns: int = Field(default=0, description="LLM 턴 수")
    stop: str = Field(default="", description="끝난 이유: filled(슬롯 참) / agent(LLM이 끝냄) / turns / time / idle / error")
    cards: int = Field(description="이 대화에서 새로 통과한 카드 수")
    slots: dict[str, int] = Field(description="[33] 대화가 끝났을 때 슬롯별 채운 수")
    seconds: float


ExtractionResult.model_rebuild()


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


class SlotAgent:
    """[74] 2단계 실행 하나의 상태와 도구 실행. LLM은 도구를 고르고, 이 클래스가 실행·저장·세기·검증을 맡는다."""

    def __init__(self, job: TargetJob, log=print, use_rag: bool = True):
        self.pool = EvidencePool()
        self.job, self.target, self.log = job, make_target(job), log
        self.rag = RagState(enabled=use_rag)
        self.timings = {"agent": 0.0, "search": 0.0, "fetch": 0.0, "read": 0.0}
        self.queries: list[str] = []
        self.used: dict[str, str] = {}             # 검색어 → 슬롯
        self.guard = SearchGuard()                 # [34] 같은 검색 차단, [78] 새 문서 없는 도구 빼기
        self.read: set[str] = set()                # 읽은 D번호
        self.selected_all: list[str] = []
        self.cards: list[EvidenceCard] = []
        self.rejected: list[CardDraft] = []
        self.keys: set[tuple[str, str]] = set()    # 카드 중복 확인 (doc_id, quote) — 3단계 dedup_cards와 같은 기준
        self.dropped = Counter()                   # 걸러낸 사유별 수
        self.rounds: list[RoundLog] = []
        self.filled = {slot: 0 for slot in SLOTS}
        self.slot = ""                             # 지금 대화의 슬롯
        self.searches_since_read = 0               # [81] 이 대화에서 마지막 읽기 뒤 실행한 검색 수
        self.jobs_ok = jobalio.available()         # [84] 잡알리오 키가 있을 때만 search_public_jobs를 준다

    def _unread(self) -> list[str]:
        """[81] 이 대화 목록에 나왔지만 아직 안 읽은 문서."""
        return self.pool.order([n for n in self.guard.listed if n not in self.read], self.slot)

    def _unread_block(self) -> str:
        unread = self._unread()
        return f"\n\n아직 안 읽은 문서:\n{self.pool.render_titles(unread[:UNREAD_SHOWN])}" if unread else ""

    def _must_read(self) -> bool:
        return self.searches_since_read >= SEARCH_BEFORE_READ and bool(self._unread())

    def tools(self) -> list[dict]:
        specs = [READ_TOOL_SPEC]
        if self._must_read():
            return specs
        if not self.guard.exhausted("search_requirements"):
            specs.insert(0, WEB_TOOL_SPEC)
        if self.jobs_ok and not self.guard.exhausted("search_public_jobs"):
            specs.insert(0, JOB_TOOL_SPEC)
        if self.rag.enabled and not self.guard.exhausted("search_rag"):
            specs.insert(0, RAG_TOOL_SPEC)
        return specs

    def status(self) -> str:
        return ", ".join(f"{s} {n}/{SLOTS[s][1]}" for s, n in self.filled.items())

    # ---------------------------------------------------------------- 도구 실행. 반환: (대화에 돌려줄 글, 새로 실행했는지)

    def _search(self, tool: str, query, slot: str) -> tuple[str, bool]:
        query = str(query or "").strip()
        if not query:
            return "query가 비어 있음. 검색어를 적어 다시 호출해.", False
        if self._must_read():
            return f"검색을 {self.searches_since_read}번 이어서 했다. 새로 검색하기 전에 아래 문서 중 근거가 있을 것을 read_documents로 읽어.{self._unread_block()}", False
        if len(query.split()) > AGENT_QUERY_MAX_WORDS:
            return f'"{query}"는 너무 길어서 실행하지 않았다. 대상 하나 + 찾는 영역 하나, 2~5단어로 줄여.', False
        if self.guard.exhausted(tool):
            return f"{tool}에서 새 문서가 더 나오지 않는다. 다른 도구를 쓰거나 목록의 문서를 읽거나 끝내.{self._unread_block()}", False
        if self.guard.repeated(tool, query):
            self.log(f"[{slot}] 검색어 중복(실행 안 함): {tool} {query}")
            return f'"{query}"는 이 도구로 이미 검색했다. 다른 방향의 검색어를 쓰거나, 목록의 문서를 읽거나, 끝내.{self._unread_block()}', False
        if query not in self.used:
            self.used[query] = slot
            self.queries.append(query)
        t = time.time()
        if tool == "search_rag":
            res = self.rag.search(query, slot, self.log)
            if res is None:
                return "DB 검색을 쓸 수 없다. search_requirements를 써.", False
        elif tool == "search_public_jobs":
            res = search_public_jobs(query)
        else:
            res = search_requirements(query, per_source=PER_SOURCE, max_fetch=0)
        nums = self.pool.add(res, slot)
        self.timings["search"] += time.time() - t
        self.searches_since_read += 1
        self.log(f"[{slot}] {tool}: {query} → 문서 {len(nums)}건" + (f" (실패: {', '.join(res.errors)})" if res.errors else ""))
        shown = self.pool.order([n for n in self.guard.fresh(tool, nums) if n not in self.read], slot)
        if not shown:
            return f'"{query}" 결과에 새 문서가 없다. {self.guard.hint(self._search_tools())}아직 안 읽은 문서를 읽거나 다른 검색어·도구를 써.{self._unread_block()}', True
        more = f"\n(외 {len(shown) - AGENT_LIST_MAX}건 생략)" if len(shown) > AGENT_LIST_MAX else ""
        return f'"{query}" 결과:\n{self.pool.render_titles(shown[:AGENT_LIST_MAX])}{more}\n\n{SEARCH_NEXT}', True

    def _pick(self, docs, slot: str) -> tuple[list[str], list[str]]:
        """read_documents 인자에서 읽을 D번호를 고른다. 반환: (읽을 것, 못 읽는 것과 이유).
        상한: 기본 SELECT_PER_SLOT(채울 수가 1 남았으면 1), 활동 슬롯은 웹 ACTIVITY_WEB_SELECT건·RAG SCHOOL_SELECT_PER_SLOT건까지."""
        nums = list(dict.fromkeys(re.findall(r"D[0-9]+", json.dumps(docs, ensure_ascii=False))))
        web_cap = total_cap = max(1, min(SELECT_PER_SLOT, SLOTS[slot][1] - self.filled[slot]))
        if slot == ACTIVITY:  # [61] 외부 활동을 먼저 찾으므로 웹 문서를 더 읽는다
            web_cap = total_cap = max(total_cap, ACTIVITY_WEB_SELECT)
        ok, skipped, web = [], [], 0
        for num in nums:
            if num not in self.pool.docs:
                skipped.append(f"{num}: 목록에 없는 번호")
            elif num in self.read:
                skipped.append(f"{num}: 이미 읽음")
            else:
                is_rag = self.pool.docs[num].provider == "rag"
                wide = is_rag and slot in RAG_WIDE_SLOTS  # [54] 짧은 RAG 문서는 한 번에 더
                if (not is_rag and web >= web_cap) or len(ok) >= (max(total_cap, SCHOOL_SELECT_PER_SLOT) if wide else total_cap):
                    skipped.append(f"{num}: 한 번에 읽는 수를 넘음 (다음 호출에)")
                else:
                    web += not is_rag
                    ok.append(num)
        return ok, skipped

    def _read(self, docs, slot: str) -> tuple[str, bool]:
        nums, skipped = self._pick(docs, slot)
        note = ("\n못 읽음: " + "; ".join(skipped)) if skipped else ""
        if not nums:
            return f"읽을 문서가 없다.{note}\n검색 결과 목록의 [D번호]를 고르거나 새로 검색해.", False
        self.searches_since_read = 0
        t = time.time()
        self.pool.fetch(nums)
        self.timings["fetch"] += time.time() - t
        self.log(f"[{slot}] 읽기: {nums} (본문 {sum(self.pool.docs[n].doc_id in self.pool.texts for n in nums)}/{len(nums)}건)")
        t = time.time()
        drafts = read_cards(self.target, self.pool, nums, self.log, {n: slot for n in nums})
        self.timings["read"] += time.time() - t
        by_doc, rejected = self._accept(drafts, nums)
        self.read.update(nums)
        self.selected_all += nums
        self.filled = fill_counts(slot_docs(self.cards))
        self.log(f"카드 {len(drafts)}장 중 통과 {sum(len(v) for v in by_doc.values())}장 → 슬롯 {self.status()}")
        lines = []
        for num in nums:
            items = by_doc.get(num, [])
            kind = "본문" if self.pool.docs[num].doc_id in self.pool.texts else "요약만"
            lines.append(f"[{num}] ({kind}) " + (f"카드 {len(items)}장: {', '.join(items)}" if items else "근거 없음"))
        if rejected:
            lines.append(f"원문에 없어 버린 카드 {rejected}장")
        left = SLOTS[slot][1] - self.filled[slot]
        tail = f"이 슬롯 {self.filled[slot]}/{SLOTS[slot][1]}. 아직 {left}개 부족하다. 이번 결과를 보고 다음에 무엇을 할지 정해." \
            if left > 0 else f"이 슬롯 {self.filled[slot]}/{SLOTS[slot][1]}."
        return "\n".join(lines) + note + f"\n\n{tail}", True

    def _accept(self, drafts: list[CardDraft], nums: list[str]) -> tuple[dict[str, list[str]], int]:
        """원문 대조와 카드 걸러내기. 통과한 카드를 저장하고 (D번호 → 통과한 item 목록, 버린 수)를 돌려준다."""
        pool, grade = self.pool, self.job.student.grade
        per_doc = Counter(c.doc_id for c in self.cards)
        by_doc: dict[str, list[str]] = {}
        rejected = 0
        for draft in drafts:
            card = verify(draft, pool, nums)
            if card is None:
                self.rejected.append(draft)
                rejected += 1
                continue
            num = draft.doc.strip("[] ")
            # [33] 교과목인데 건양대 문서가 아니면(다른 대학 교육과정 등) 버린다.
            # [57] 교내프로그램인데 건양대 문서가 아니면 외부 활동이므로 대외활동으로 고친다
            if card.category in SCHOOL_CATEGORIES and not is_school_doc(pool.docs[num], pool.texts.get(card.doc_id)):
                if card.category == "교과목":
                    self.dropped["not_school"] += 1
                    continue
                card.category = "대외활동"
                self.dropped["relabeled"] += 1
            # [59] 건양대 문서의 외부 활동 카드인데 건양대 행사면(예: "AI·SW중심대학 디지털 경진대회", 건양대 SW중심대학) 교내프로그램
            elif card.category == "대외활동" and (SCHOOL_EVENT_MARK in card.item or SCHOOL_EVENT_MARK in card.quote) \
                    and is_school_doc(pool.docs[num], pool.texts.get(card.doc_id)):
                card.category = "교내프로그램"
                self.dropped["to_school"] += 1
            # [60] 교과목은 학교 DB(RAG) 문서이거나 quote에 과목 코드가 있을 때만 (웹 뉴스의 "의료 AI"·"데이터 분석" 같은 문구가 과목으로 들어옴)
            if card.category == "교과목" and pool.docs[num].provider != "rag" and not COURSE_CODE.search(card.quote):
                self.dropped["not_course"] += 1
                continue
            # [60] 활동 카드인데 item에 활동을 나타내는 말(대회·공모전·캠프·프로그램·인턴 …)이 없으면 버린다
            if card.category in ("교내프로그램", "대외활동") and not ACTIVITY_WORD.search(card.item):
                self.dropped["not_activity"] += 1
                continue
            # [54] 학생 학년보다 낮은 학년 과목 (학년을 모르면 남김)
            if card.category == "교과목" and grade and card.grade and card.grade < grade:
                self.dropped["past_grade"] += 1
                continue
            # [43] 문서당 카드 상한. LLM이 중요한 것부터 쓰게 했으므로 앞에서부터 남긴다
            if per_doc[card.doc_id] >= MAX_CARDS_PER_DOC:
                self.dropped["over_cap"] += 1
                continue
            per_doc[card.doc_id] += 1
            self.cards.append(card)
            if (card.doc_id, _norm(card.quote)) not in self.keys:
                self.keys.add((card.doc_id, _norm(card.quote)))
                by_doc.setdefault(num, []).append(card.item)
        return by_doc, rejected

    def _search_tools(self) -> list[str]:
        return (["search_rag"] if self.rag.enabled else []) + ["search_requirements"] + (["search_public_jobs"] if self.jobs_ok else [])

    def _run_tool(self, name: str, args: dict, slot: str) -> tuple[str, bool]:
        if name in self._search_tools():
            return self._search(name, args.get("query"), slot)
        if name == "read_documents":
            return self._read(args.get("docs"), slot)
        return f"{name}이라는 도구는 없다. {', '.join(self._search_tools())}, read_documents 중에서 써.", False

    # ---------------------------------------------------------------- 슬롯 대화 (LLM ↔ 도구 루프)

    def run_slot(self, slot: str, deadline: float) -> None:
        """가이드 슬롯 하나를 새 대화에서 채운다. 다음에 무엇을 할지는 LLM이 도구 호출로 정한다."""
        started, cards_before, read_before = time.time(), len(self.keys), len(self.selected_all)
        queries_before = len(self.queries)
        task, directions = SLOT_TASKS[slot]
        used = [q for q, s in self.used.items() if s == slot]
        messages = [{"role": "system", "content": AGENT_SYSTEM},
                    {"role": "user", "content": AGENT_REQUEST.format(
                        target=self.target, slot=slot, filled=self.filled[slot], need=SLOTS[slot][1], task=task,
                        directions="\n".join(f"- {d}" for d in directions),
                        used=", ".join(f'"{q}"' for q in used) if used else "없음", extra=SLOT_EXTRA.get(slot, ""))}]
        self.guard.new_conversation()
        self.slot, self.searches_since_read = slot, 0
        nudges, cards_at_nudge = 0, 0
        self.log(f"=== [{slot}] 에이전트 시작 (슬롯 {self.status()})")

        def on_text(_text: str) -> str | None:
            """LLM이 끝내려 함. 슬롯이 부족하면 되묻는다. 두 번째는 앞 되묻기 뒤 카드가 늘었을 때만."""
            nonlocal nudges, cards_at_nudge
            if self.filled[slot] >= SLOTS[slot][1] or nudges >= AGENT_NUDGES or (nudges and len(self.keys) == cards_at_nudge):
                return None
            nudges, cards_at_nudge = nudges + 1, len(self.keys)
            mine = [q for q, s in self.used.items() if s == slot]
            return AGENT_NUDGE.format(slot=slot, filled=self.filled[slot], need=SLOTS[slot][1],
                                      hint=self.guard.hint(self._search_tools()),
                                      used=", ".join(f'"{q}"' for q in mine) or "없음")

        def add_seconds(seconds: float) -> None:
            self.timings["agent"] += seconds

        res = run_tool_loop(messages, AGENT, tools=self.tools, run_tool=lambda name, args: self._run_tool(name, args, slot),
                            on_text=on_text, max_turns=AGENT_MAX_TURNS, idle_turns=AGENT_IDLE_TURNS, deadline=deadline,
                            done=lambda: self.filled[slot] >= SLOTS[slot][1], on_chat=add_seconds, log=self.log)
        turns, calls_log = res.turns, res.calls
        stop = "filled" if res.stop == "done" else res.stop
        self.log(f"[{slot}] 에이전트 끝 ({stop}, {turns}턴, 도구 {len(calls_log)}번)")
        self.rounds.append(RoundLog(
            slot=slot, queries=self.queries[queries_before:], tool_calls=calls_log, turns=turns, stop=stop,
            selected=[self.pool.docs[n].doc_id for n in self.selected_all[read_before:]],
            cards=len(self.keys) - cards_before, slots=dict(self.filled), seconds=round(time.time() - started, 1)))


def extract_evidence(job: TargetJob, log=print, use_rag: bool = True) -> ExtractionResult:
    """슬롯마다 도구 호출 에이전트를 돌려 근거 카드를 모은다. use_rag=False면 RAG 없이 검색 API로만 (비교 시험용)."""
    agent = SlotAgent(job, log, use_rag)
    start = time.time()
    # [55] 필수 슬롯(채용 요건·자격증)부터. 슬롯 몫 시간은 채울 수에 비례하고, 남은 시간은 뒤 슬롯으로 넘어간다
    for i, slot in enumerate(SLOT_PRIORITY):
        if agent.filled[slot] >= SLOTS[slot][1]:
            continue
        left = MAX_SECONDS - (time.time() - start)
        if left <= 0 and not (slot in REQUIRED_SLOTS and agent.filled[slot] == 0):
            log(f"시간 상한 {MAX_SECONDS}초로 [{slot}] 건너뜀")
            continue
        weight = sum(SLOTS[s][1] for s in SLOT_PRIORITY[i:] if agent.filled[s] < SLOTS[s][1])
        agent.run_slot(slot, time.time() + max(0, left) * SLOTS[slot][1] / weight)
    # [55] 그래도 비어 있는 필수 슬롯은 대화를 한 번 더 연다 (이미 쓴 검색어를 보여 주므로 다른 방향으로 찾게 됨)
    for slot in REQUIRED_SLOTS:
        if agent.filled[slot] == 0:
            log(f"필수 슬롯이 비어 있어 한 번 더: {slot}")
            agent.run_slot(slot, time.time() + REQUIRED_EXTRA_SECONDS)

    pool = agent.pool
    cards, capped = apply_caps(agent.cards)
    if capped:
        log("추천 상한으로 뺀 항목: " + ", ".join(f"{s} {n}개" for s, n in capped.items()))
    d = agent.dropped
    return ExtractionResult(
        job=job, docs=list(pool.docs.values()), selected=[pool.docs[n].doc_id for n in agent.selected_all],
        past_grade=d["past_grade"], capped=capped, relabeled=d["relabeled"], to_school=d["to_school"],
        not_course=d["not_course"], not_activity=d["not_activity"],
        cards=cards, rejected=agent.rejected, queries=agent.queries, query_slots=agent.used, rounds=agent.rounds,
        not_school=d["not_school"], over_cap=d["over_cap"],
        rag_error=agent.rag.error,
        slots=fill_counts(slot_docs(cards)),
        timings={k: round(v, 1) for k, v in agent.timings.items()},
        texts={i: t for i, t in pool.texts.items() if i in {c.doc_id for c in cards}},
    )
