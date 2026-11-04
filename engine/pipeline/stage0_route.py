"""0단계 입력 판단 + 정보 질문·진로 고민 답변 ([66]).

입력 판단 (route_input)
  1. 코드: 위기 표현(CRISIS — 자살·자해 등)이 있으면 LLM 없이 진로 고민 + crisis. 위기 안내는 LLM 판단에 맡기지 않는다
  2. 코드: 질문 칸이 비어 있으면(폼 칸만) LLM 없이 취업 가이드
  3. 입력 판단 LLM(새 대화): 취업 가이드 / 정보 질문 / 진로 고민 (RouteDraft). 실패하면 취업 가이드
취업 가이드 → run.py가 1~5단계. 입력 판단 LLM이 has_goal=false(직무·관심 분야·전공·기업을 하나도 말하지 않음)이고 폼 칸도 비었으면
  1단계 전에 clarify_answer로 되묻는다 (1단계는 정보가 없어도 직무를 지어냄). 1단계 결과가 비어도(needs_more_info) 2단계 전에 되묻는다
진로 고민인데 위기 표현이면 안전을 먼저 묻는 COUNSEL_CRISIS_REQUEST (자퇴·진로 이야기로 받지 않게)
정보 질문 (answer_info)
  계획 LLM(검색어 1~3개) → 코드가 검색어마다 RAG(건양대 자료, 문서 종류 제한 없음) → 검색 API를 근거 풀(EvidencePool)에 → 선택 LLM(최대 INFO_SELECT건)
  → 본문 수집 → 답변 LLM(본문 전체, INFO_READ_BUDGET까지 문서째 — 본문은 자르지 않음) → 코드: 없는 [D번호]는 지우고, 근거 번호가 하나도 없으면
  한 번 다시 쓰게 함. 글의 [D번호]를 나온 순으로 [S1]…로 바꾸고 출처(sources)를 붙인다
진로 고민 (answer_counsel)
  상담 답변 LLM(검색 없음) → 코드: 상담센터 권유가 없으면 붙이고, crisis면 위기 상담 안내(CRISIS_NOTE)를 맨 앞에 반드시 붙인다
"""
import json
import re

from engine.llm import LLMConfig, chat
from engine.pipeline.stage1_job import render_student
from engine.pipeline.stage2_evidence import EvidencePool
from engine.pipeline.stage4_rank import today_text
from engine.pipeline.stage5_verify import tidy
from engine.prompts.stage0_route import (
    COUNSEL_CRISIS_REQUEST, COUNSEL_REQUEST, COUNSEL_SYSTEM, INFO_ANSWER_REQUEST, INFO_ANSWER_SYSTEM, INFO_PLAN_REQUEST, INFO_PLAN_SYSTEM,
    INFO_SELECT_REQUEST, INFO_SELECT_SYSTEM, ROUTE_REQUEST, ROUTE_SYSTEM,
)
from engine.schemas import DocSelection
from engine.schemas.job import StudentInput, TargetJob
from engine.schemas.rank import GuideEvidence, GuideResult
from engine.schemas.route import InfoPlan, RouteDecision, RouteDraft

ROUTER = LLMConfig(temperature=0)
INFO_WRITER = LLMConfig(temperature=0.3, num_predict=2048)
COUNSELOR = LLMConfig(temperature=0.5, num_predict=1024)

INFO_SLOT = "정보 질문"     # 근거 풀에서 이 경로 검색어로 나온 문서 표시
INFO_PER_SOURCE = 10
INFO_RAG_K = 3
INFO_SELECT = 3             # 읽을 문서 수
INFO_READ_BUDGET = 25_000   # 답변 LLM 1회 입력 분량(자). 2단계 READ_BUDGET_CHARS와 같음. 넘으면 뒤 문서를 뺀다(본문은 자르지 않음)
INFO_RETRIES = 1

# 위기 표현: LLM 판단과 상관없이 위기 상담 안내를 붙인다 (놓치는 것보다 잘못 붙는 것이 낫다)
CRISIS = re.compile(r"죽고\s*싶|죽어\s*버리|자살|자해|목숨|살기\s*싫|살고\s*싶지\s*않|사라지고\s*싶|극단적\s*선택|끝내고\s*싶|없어지고\s*싶")
CRISIS_NOTE = ("지금 많이 힘드시다면 혼자 견디지 말고 바로 이야기해 주세요. **자살예방 상담전화 109**(24시간), "
               "**정신건강 위기상담 1577-0199**에서 언제든 도움을 받을 수 있어요. 위급한 상황이면 112·119에 연락하세요.")
COUNSEL_NOTE = "학교 학생상담센터에서 상담 선생님과 이야기를 나눠 보는 것도 권해 드려요. 혼자 정하지 않아도 괜찮아요."
DOC_REF = re.compile(r"\[(D\d+)\]")
GENERIC_TITLES = {"", "신입", "인턴", "경력", "미정", "없음"}


def route_input(student: StudentInput, log=print) -> RouteDecision:
    text = " ".join(filter(None, [student.question, student.desired_job]))
    if CRISIS.search(text):
        return RouteDecision(kind="진로 고민", reason="위기 표현이 있음", crisis=True, by_code=True)
    if not (student.question or "").strip():
        return RouteDecision(kind="취업 가이드", reason="질문 없이 폼 칸만 입력", by_code=True)
    messages = [{"role": "system", "content": ROUTE_SYSTEM},
                {"role": "user", "content": ROUTE_REQUEST.format(
                    student=render_student(student.model_copy(update={"documents": []})))}]
    try:
        d = RouteDraft.model_validate(json.loads(chat(messages, ROUTER, format=RouteDraft.model_json_schema())["content"]))
    except Exception as e:
        log(f"입력 판단 실패 → 취업 가이드로: {type(e).__name__}")
        return RouteDecision(kind="취업 가이드", reason="입력 판단 실패", by_code=True)
    question = d.question.strip() or (student.question or "").strip()
    has_goal = d.has_goal or bool(student.desired_job or student.major)  # 폼 칸에 적었으면 목표가 있는 것
    return RouteDecision(kind=d.kind, reason=d.reason, question=question if d.kind == "정보 질문" else "", has_goal=has_goal)


def needs_more_info(job: TargetJob) -> bool:
    """1단계 결과에 2단계 검색을 시작할 목표가 없는지: 희망 직무·전공·관심 분야·기업·보유 항목이 모두 없고 직무명도 일반적."""
    s = job.student
    has = s.desired_job or s.major or job.interests or job.companies or job.skills or job.certificates or job.projects
    return not has and job.job_title.strip() in GENERIC_TITLES


def clarify_answer() -> GuideResult:
    text = ("어떤 방향으로 준비하면 좋을지 같이 정리해 드릴게요. 그러려면 몇 가지만 알려 주세요.\n\n"
            "- 전공과 학년\n- 관심 있는 분야나 하고 싶은 일 (예: 백엔드 개발, 로봇, 데이터 분석)\n"
            "- 가 보고 싶은 기업이 있다면 기업 이름\n- 지금 가진 기술·자격증·해 본 프로젝트\n\n"
            "하나만 알려 주셔도 거기서부터 시작할 수 있어요.")
    return GuideResult(kind="취업 가이드", job_title="", answer=text, summary=text.split("\n\n")[0], sections=[],
                       held=[], lacking=[], answer_by_code=True)


# ---------------------------------------------------------------- 정보 질문

def _search(question: str, pool: EvidencePool, log) -> list[str]:
    from engine.tools.rag_search import RagSearchError, search_rag
    from engine.tools.requirements import search_requirements

    today = today_text()
    try:
        msg = chat([{"role": "system", "content": INFO_PLAN_SYSTEM},
                    {"role": "user", "content": INFO_PLAN_REQUEST.format(question=question, today=today)}],
                   ROUTER, format=InfoPlan.model_json_schema())
        queries = [q.strip() for q in InfoPlan.model_validate(json.loads(msg["content"])).queries if q.strip()][:3]
    except Exception:
        queries = []
    queries = queries or [question]
    rag_ok = True
    for q in queries:
        if rag_ok:  # 데이터 우선순위: RAG(건양대 자료) 먼저
            try:
                pool.add(search_rag(q, k=INFO_RAG_K), INFO_SLOT)
            except RagSearchError as e:
                rag_ok = False
                log(f"RAG 검색 실패 — 검색 API로만: {e}")
        pool.add(search_requirements(q, per_source=INFO_PER_SOURCE, max_fetch=0), INFO_SLOT)
    log(f"정보 질문 검색어: {queries} → 문서 {len(pool.docs)}건")
    return queries


def _select(question: str, pool: EvidencePool) -> list[str]:
    nums, titles = pool.render_slot(INFO_SLOT, set())
    if not nums:
        return []
    msg = chat([{"role": "system", "content": INFO_SELECT_SYSTEM},
                {"role": "user", "content": INFO_SELECT_REQUEST.format(question=question, titles=titles, n=INFO_SELECT)}],
               ROUTER, format=DocSelection.model_json_schema())
    picked = [n for n in DocSelection.model_validate(json.loads(msg["content"])).selected if n in nums]
    return list(dict.fromkeys(picked))[:INFO_SELECT]


def _render_docs(pool: EvidencePool, nums: list[str]) -> tuple[list[str], str]:
    """본문 전체를 문서째 넣는다. 분량을 넘으면 뒤 문서를 뺀다 (첫 문서는 길어도 넣음)."""
    kept, parts, size = [], [], 0
    for n in nums:
        part = pool.render_doc(n)
        if kept and size + len(part) > INFO_READ_BUDGET:
            break
        kept.append(n)
        parts.append(part)
        size += len(part)
    return kept, "\n\n".join(parts)


def answer_info(question: str, log=print) -> GuideResult:
    pool = EvidencePool()
    _search(question, pool, log)
    nums = _select(question, pool)
    if nums:
        pool.fetch(nums)
    nums = [n for n in nums if pool.docs[n].doc_id in pool.texts or pool.docs[n].snippet]
    if not nums:
        text = ("찾아본 자료에서는 이 질문에 답할 내용을 찾지 못했어요. 질문을 조금 더 구체적으로(자격증·대회·과목 이름, 연도 등) "
                "알려 주시면 다시 찾아볼게요.")
        return GuideResult(kind="정보 질문", job_title="", answer=text, summary=text, sections=[], held=[], lacking=[],
                           answer_by_code=True)
    nums, docs = _render_docs(pool, nums)
    issues, all_issues, text = [], [], ""
    for _ in range(INFO_RETRIES + 1):
        retry = ("\n\n## 지난 답변의 문제 (이번에는 고칠 것)\n" + "\n".join(f"- {i}" for i in issues)) if issues else ""
        msg = chat([{"role": "system", "content": INFO_ANSWER_SYSTEM},
                    {"role": "user", "content": INFO_ANSWER_REQUEST.format(question=question, today=today_text(),
                                                                           docs=docs, retry=retry)}], INFO_WRITER)
        text = "\n".join(tidy(ln) if ln.strip() else "" for ln in msg["content"].strip().splitlines())
        text = DOC_REF.sub(lambda m: m.group(0) if m.group(1) in nums else "", text)
        issues = [] if DOC_REF.search(text) else ["근거 문서 번호([D번호])가 하나도 없음 — 문서에서 가져온 문장 끝에 붙일 것"]
        all_issues += issues
        if not issues:
            break
    # [D번호] → 나온 순 [S1]…, 출처
    order = list(dict.fromkeys(DOC_REF.findall(text)))
    label = {d: f"S{i}" for i, d in enumerate(order, 1)}
    text = DOC_REF.sub(lambda m: f"[{label[m.group(1)]}]", text)
    from engine.pipeline.answer import source_label
    sources = []
    for d in order:
        doc = pool.docs[d]
        sources.append(GuideEvidence(card_id=label[d], quote=doc.snippet or "", title=doc.title, url=doc.url,
                                     source=source_label(doc), published_at=doc.published_at))
    for i in all_issues:
        log(f"정보 답변 검사: {i}")
    return GuideResult(kind="정보 질문", job_title="", answer=text, sources=sources, answer_issues=all_issues,
                       summary=text.split("\n\n")[0], sections=[], held=[], lacking=[])


# ---------------------------------------------------------------- 진로 고민

def answer_counsel(student: StudentInput, crisis: bool, log=print) -> GuideResult:
    request = COUNSEL_CRISIS_REQUEST if crisis else COUNSEL_REQUEST
    messages = [{"role": "system", "content": COUNSEL_SYSTEM},
                {"role": "user", "content": request.format(
                    student=render_student(student.model_copy(update={"documents": []})))}]
    try:
        text = "\n".join(tidy(ln) if ln.strip() else "" for ln in chat(messages, COUNSELOR)["content"].strip().splitlines())
        by_code = False
    except Exception as e:
        log(f"상담 답변 실패: {type(e).__name__}")
        text, by_code = "많이 지치고 힘드셨겠어요. 지금 마음이 어떤지, 무엇이 가장 힘든지 조금 더 이야기해 주실 수 있을까요?", True
    # 지어낸 전화번호는 지운다 (번호는 코드가 붙이는 위기 안내에만)
    text = re.sub(r"\b\d{2,4}-\d{3,4}(-\d{4})?\b|\b1[0-9]{2}\b(?=\s*(번|으로|에|\)))", "", text)
    if "상담" not in text and not crisis:
        text += "\n\n" + COUNSEL_NOTE
    if crisis:
        text = CRISIS_NOTE + "\n\n" + text
    return GuideResult(kind="진로 고민", job_title="", answer=text, summary=text.split("\n\n")[0], sections=[],
                       held=[], lacking=[], answer_by_code=by_code, crisis=crisis)
