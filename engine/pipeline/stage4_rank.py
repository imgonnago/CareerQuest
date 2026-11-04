"""4단계 우선순위 판단: 코드(후보·Gap) → 우선순위 LLM(슬롯마다) → 5단계 검증(판단 오류면 그 슬롯만 다시) → 근거 순위 카드.

1. 코드가 근거 집계 표(3단계 SkillRow)를 가이드 슬롯별로 나누고, Gap(engine/tools/gap.py)으로 학생이 이미 가진 줄은 뺀다
   (뺀 줄은 RankingResult.held — 최종 답변의 "이미 갖춘 것")
2. 우선순위 LLM이 슬롯마다 새 대화에서 후보(R번호) 중 추천할 것을 중요한 순으로 고르고 이유·할 일을 쓴다 (RankDraftList)
   한 번에 모든 슬롯을 시키지 않는다 — 2단계 [37]과 같은 이유 (9B 모델이 앞 슬롯만 하거나 슬롯을 섞음)
3. 5단계 검증(stage5_verify.verify_slot)이 R번호·중복·빈 칸·지어낸 숫자·후보에 없는 기업 이름을 본다.
   판단 오류가 있으면 문제를 적어 그 슬롯만 RANK_RETRIES번 다시 부르고, 그래도 하나도 못 고르면 코드가 빈도 순으로 채운다(by_code)
4. 5단계 확인(stage5_verify.check_ranks): 인용 재대조·교과목 학년·활동 마감일
슬롯 순서·상한은 2단계와 같다 (SLOTS·ITEM_CAPS, 채용 요건은 RANK_CAPS).
"""
import json
import time
from datetime import date
from collections import defaultdict

from engine.llm import LLMConfig, chat
from engine.pipeline.stage2_evidence import ITEM_CAPS, SLOT_PRIORITY, make_target
from engine.pipeline.stage5_verify import check_ranks, term_of, verify_slot
from engine.prompts.stage4_rank import RANK_REQUEST, RANK_SYSTEM, RETRY_NOTE, SLOT_RANK_GUIDE
from engine.schemas.aggregate import AggregationResult, NumberedCard, SkillEntry, SkillRow
from engine.schemas.rank import RankCard, RankDraft, RankDraftList, RankingResult, RankIssue, RowGap
from engine.tools.gap import compute_gaps

RANKER = LLMConfig(temperature=0)

# 슬롯별 추천 상한. 항목 슬롯은 2단계 apply_caps와 같은 수, 채용 요건은 준비할 기술·조건 5개
RANK_CAPS = {"채용 요건": 5, **ITEM_CAPS}
RANK_RETRIES = 1        # 판단 오류일 때 그 슬롯을 다시 부르는 횟수 (README: 판단 오류 → (4))
MAX_ITEMS_SHOWN = 3     # 후보 줄에 보여 줄 근거 항목(카드 item) 수. 항목은 짧은 이름이라 본문 자르기와 다름
CATEGORY_LABEL = {"자격요건": "필수", "우대사항": "우대"}


def slot_candidates(rows: list[SkillRow], gaps: list[RowGap], slot: str) -> tuple[list[tuple[SkillRow, RowGap]], list[RowGap]]:
    """슬롯의 후보(보유 안 한 줄)와 보유한 줄. rows는 3단계가 슬롯 안에서 문서 수 많은 순으로 정렬해 둠 = 빈도 순위."""
    cands, held = [], []
    for r, g in zip(rows, gaps):
        if r.slot != slot:
            continue
        if g.gap == "보유":
            held.append(g)
        else:
            cands.append((r, g))
    return cands, held


def _course_info(row: SkillRow, cards: dict[str, NumberedCard]) -> str:
    cs = [cards[c] for c in row.card_ids if c in cards and cards[c].category == "교과목"]
    grades = sorted({c.grade for c in cs if c.grade})
    types = sorted({c.course_type for c in cs if c.course_type})
    parts = ([f"개설 {'·'.join(map(str, grades))}학년"] if grades else []) + types
    return f" | {' · '.join(parts)}" if parts else ""


def render_candidates(cands: list[tuple[SkillRow, RowGap]], cards: dict[str, NumberedCard]) -> str:
    """우선순위 LLM용 후보 목록: R번호 · 이름 · 보유 여부 · 근거 문서 수 · 분류 · 역할 · 기업 · 근거 항목."""
    lines = []
    for i, (r, g) in enumerate(cands, 1):
        cat = ", ".join(f"{CATEGORY_LABEL.get(k, k)} {v}" for k, v in r.by_category.items())
        role = ", ".join(f"{k} {v}" for k, v in r.by_role.items())
        company = f" | 기업: {', '.join(r.companies)}" if r.companies else ""
        level = f" (현재 {g.student_level})" if g.student_level else ""
        items = "; ".join(r.aliases[:MAX_ITEMS_SHOWN])
        lines.append(f"R{i} {r.skill} | {g.gap}{level} | 근거 문서 {r.doc_count} ({role}) | 분류: {cat}{company}"
                     f"{_course_info(r, cards)} | 근거 항목: {items}")
    return "\n".join(lines)


def render_held(agg: AggregationResult, held: list[RowGap]) -> str:
    job = agg.job
    lines = []
    if job.skills:
        lines.append("보유 기술: " + ", ".join(h.name for h in job.skills))
    if job.certificates:
        lines.append("자격증: " + ", ".join(h.name for h in job.certificates))
    if job.projects:
        lines.append("해 본 프로젝트: " + ", ".join(h.name for h in job.projects))
    if job.student.activities:
        lines.append("참여한 활동: " + ", ".join(job.student.activities))
    if held:
        lines.append("근거에 나왔지만 이미 가진 것: " + ", ".join(dict.fromkeys(g.skill for g in held)))
    return "\n".join(lines) or "없음"


def today_text(today: int | None = None) -> str:
    """오늘 날짜와 지금·다음 학기. 예: 2026년 11월 4일 — 지금 2026년 2학기, 다음 학기 2027년 1학기"""
    day = today or int(date.today().strftime("%Y%m%d"))
    y, t = term_of(day)
    ny, nt = (y, 2) if t == 1 else (y + 1, 1)
    # [65] 방학을 학기에 붙여 "2027년 1학기 겨울방학"처럼 쓰는 일이 잦아 이번 방학을 날짜로 적어 준다
    vacation = (f"이번 여름방학 {y}년 7~8월" if t == 1 else f"이번 겨울방학 {y}년 12월~{y + 1}년 2월")
    return (f"{day // 10000}년 {day // 100 % 100}월 {day % 100}일 — 지금 {y}년 {t}학기, {vacation}, 다음 학기 {ny}년 {nt}학기 "
            f"(그 전 학기·연도는 이미 지남. 방학은 \"{y}년 겨울방학\"처럼 쓰고 \"1학기 겨울방학\"처럼 학기에 붙이지 않음)")


def judge_slot(target: str, held_text: str, slot: str, cands: list[tuple[SkillRow, RowGap]],
               cards: dict[str, NumberedCard], issues: list[RankIssue] | None = None,
               today: int | None = None) -> list[RankDraft]:
    """우선순위 LLM 1회 (새 대화). issues가 있으면 지난 출력의 문제를 요청 끝에 붙인다."""
    retry = RETRY_NOTE.format(issues="\n".join(f"- {i.message}" for i in issues)) if issues else ""
    content = RANK_REQUEST.format(target=target, today=today_text(today), held=held_text, slot=slot, guide=SLOT_RANK_GUIDE[slot],
                                  candidates=render_candidates(cands, cards), cap=min(RANK_CAPS[slot], len(cands)),
                                  retry=retry)
    messages = [{"role": "system", "content": RANK_SYSTEM}, {"role": "user", "content": content}]
    msg = chat(messages, RANKER, format=RankDraftList.model_json_schema())
    return RankDraftList.model_validate(json.loads(msg["content"])).ranks


def by_frequency(slot: str, cands: list[tuple[SkillRow, RowGap]]) -> list[RankCard]:
    """우선순위 LLM이 실패했을 때: 빈도 순(3단계 정렬 그대로) 상위 RANK_CAPS개. 이유·할 일은 근거에서 코드가 쓴다."""
    out = []
    for i, (r, g) in enumerate(cands[:RANK_CAPS[slot]], 1):
        cats = ", ".join(CATEGORY_LABEL.get(k, k) for k in r.by_category)
        roles = ", ".join(r.by_role)
        out.append(RankCard(
            slot=slot, rank=i, skill_id=r.skill_id, skill=r.skill, gap=g.gap,
            reason=f"{roles} 문서에서 {cats}(으)로 나옴" + (f" ({', '.join(r.companies)})" if r.companies else ""),
            action=f"{r.skill} 준비", card_ids=r.card_ids, doc_ids=r.doc_ids, categories=list(r.by_category),
            doc_count=r.doc_count, freq_rank=i, by_code=True,
        ))
    return out


def prioritize(agg: AggregationResult, student_id: int | None = None, skill_dict: list[SkillEntry] | None = None,
               log=print, today: int | None = None) -> RankingResult:
    """4·5단계. student_id가 있으면 인호 Gap API, 없으면(또는 실패하면) 1단계 보유 항목으로 Gap을 판단한다.

    skill_dict를 안 주면 DB에서 읽는다 (profile Gap의 사전 매칭용). today(YYYYMMDD)는 마감일 판정 기준 (시험용)."""
    from engine.tools.skill_dict import load_skill_dict

    timings = {}
    target = make_target(agg.job)
    t = time.time()
    if skill_dict is None and agg.skill_dict_loaded:
        skill_dict = load_skill_dict()
    gaps, gap_source, gap_error = compute_gaps(agg.skills, agg.job, target, skill_dict, student_id, log)
    timings["gap"] = time.time() - t
    counts = defaultdict(int)
    for g in gaps:
        counts[g.gap] += 1
    log(f"Gap({gap_source}): " + ", ".join(f"{k} {v}" for k, v in counts.items()))

    cards = {c.card_id: c for c in agg.cards}
    ranks, held_all, issues, retried, lacking = [], [], [], [], []
    t = time.time()
    for slot in SLOT_PRIORITY:
        cands, held = slot_candidates(agg.skills, gaps, slot)
        held_all += held
        if not cands:
            lacking.append(slot)
            issues.append(RankIssue(slot=slot, kind="근거 부족",
                                    message="추천할 후보가 없음" + (" (모두 보유)" if held else "")))
            continue
        held_text = render_held(agg, held)
        found, slot_issues = [], None
        for attempt in range(RANK_RETRIES + 1):
            try:
                drafts = judge_slot(target, held_text, slot, cands, cards, slot_issues, today)
            except Exception as e:  # JSON 깨짐·Ollama 오류
                slot_issues = [RankIssue(slot=slot, kind="판단 오류", message=f"출력을 읽지 못함 ({type(e).__name__})")]
                drafts = []
            else:
                got, slot_issues = verify_slot(slot, drafts, cands, agg, RANK_CAPS[slot], today)
                if len(got) >= len(found):  # 다시 부른 결과가 더 적으면 앞 결과를 쓴다
                    found = got
            issues += slot_issues
            errors = [i for i in slot_issues if i.kind == "판단 오류"]
            if not errors:
                break
            if attempt < RANK_RETRIES:
                retried.append(slot)
                log(f"{slot}: 판단 오류 {len(errors)}건 → 다시 판단 ({'; '.join(i.message for i in errors)})")
                slot_issues = errors
        if not found:
            log(f"{slot}: 우선순위 LLM이 고른 것이 없어 빈도 순으로 채움")
            issues.append(RankIssue(slot=slot, kind="주의", message="우선순위 LLM 실패 → 빈도 순"))
            found = by_frequency(slot, cands)
        ranks += found
        log(f"{slot}: 후보 {len(cands)}개 중 {len(found)}개 → " + ", ".join(r.skill for r in found))
    timings["rank"] = time.time() - t

    ranks, check_issues, past = check_ranks(ranks, agg, today)
    issues = list({(i.slot, i.kind, i.message): i for i in issues + check_issues}.values())
    return RankingResult(ranks=ranks, past=past, held=held_all, gaps=gaps, gap_source=gap_source, gap_error=gap_error,
                         issues=issues, retried=retried, lacking=lacking,
                         timings={k: round(v, 1) for k, v in timings.items()})
