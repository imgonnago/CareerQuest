"""5단계 검증 (코드): 근거 순위 카드 ↔ 집계 표·원문 대조, 교과목 개설 학년, 활동 마감일.

verify_slot — 우선순위 LLM 출력 하나를 근거 순위 카드로 바꾸며 판단 오류를 찾는다. 판단 오류가 있으면 4단계가 그 슬롯만 다시 부른다.
  - 판단 오류: 후보에 없는 R번호, 빈 이유·할 일, 이유의 숫자가 근거 문서·카드 수와 다름(숫자는 쓰지 말라고 함),
               이유에 그 후보 근거에 없는 기업(관심 기업 포함 — "루닛의 핵심 기술" 같은 지어낸 주장), 할 일에 관심 기업도 아닌 다른 기업,
               이미 지난 시기("2026년 1학기"를 2026년 11월에)
    → 문제가 된 순위 카드는 빼고 나머지는 남긴다. 4단계는 처음·다시 부른 결과 중 통과한 카드가 많은 쪽을 쓴다
  - 주의: 같은 번호 두 번(뒤엣것 버림), 상한 초과(뒤를 자름)
check_ranks — 검증을 통과한 카드 전체를 한 번 더 본다 (다시 판단하지 않음)
  - doc_id·인용: 근거 번호의 카드가 있는지, 본문이 있으면 인용이 본문에 그대로 있는지 (2단계 verify와 같은 공백 무시 대조)
    → 근거가 하나도 안 남으면 그 순위 카드를 뺀다
  - 교과목 개설 학년: 학생 학년보다 낮으면 뺀다 (2단계도 거르지만 학년 모르는 카드가 섞인 줄 대비). 교양/전공·학년을 카드에 붙인다
  - [63] 활동 참가 대상: 인용에 초·중·고등학생·청소년만 있고 대학생·일반·"고등학생 이상"이 없으면 뺀다
  - 활동·공모전 마감일: 인용 → 본문(없으면 요약) 순으로 그 활동 이름 근처의 접수·마감 날짜를 찾아
    지났으면 "마감"(지난 회차 — 다음 모집 확인), 안 지났으면 "모집 중", 못 찾으면 "확인 필요".
    확인 필요는 notes에 적고 추천에 남긴다. [65] "마감"은 추천에서 빼고 지난 회차(past)로 — 다시 열릴 수 있어
    "이런 활동이 있다" 참고로는 보여 주되 할 일로 권하지 않는다.
    대상·마감 검사는 활동 카드이거나, 채용 요건·자격증 슬롯이 아니면서 이름이 행사·모집(EVENT_WORD)인 순위 카드에 한다
"""
import re
from datetime import date

from engine.pipeline.stage2_evidence import ACTIVITY, _norm
from engine.schemas.aggregate import AggregationResult, SkillRow
from engine.schemas.rank import RankCard, RankDraft, RankIssue, RowGap

MIN_TEXT = 4                 # 이유·할 일 최소 글자 수
COUNT_WORDS = re.compile(r"(\d+)\s*(?:건|개|곳|군데|번|회|개사)")
# 마감일 찾기
DEADLINE_WORD = re.compile(r"마감|접수|모집|신청|지원\s*기간|기간|까지|~|∼|-\s*\d")
FULL_DATE = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
SHORT_DATE = re.compile(r"(?<![\d.])(\d{1,2})\s*[./월]\s*(\d{1,2})\s*일?(?![\d.])")
WINDOW_BEFORE, WINDOW_AFTER = 2, 8   # 활동 이름이 나온 줄 앞뒤로 날짜를 찾을 줄 수
OLD_DOC_DAYS = 365           # 마감일을 못 찾았을 때 이보다 오래된 문서면 notes에 적음
# [63] 끝난 행사 기사("10월 10일 … 성료", "개최했다") — 인용의 날짜가 모두 지났으면 지난 회차
PAST_EVENT = re.compile(r"성료|마쳤|폐막|시상식|수상|열렸|개최했|진행했|모집했")
# [67] 날짜가 없을 때는 확실히 끝났다는 말만 (일반 조언 글의 "수상·진행했"에 걸려 "데이터 분석 공모전 참여"가 지난 회차로 빠짐)
ENDED_FOR_SURE = re.compile(r"성료|폐막|마쳤|마무리|시상식|지난\s*\d{1,2}\s*일[^.]{0,80}(개최|열렸|열린|진행)")
# [63] 참가 대상이 초·중·고등학생뿐인 활동 (대학생이 못 나감). "고등학생 이상"·"대학생"·"일반"이 있으면 괜찮음
SCHOOL_ONLY = re.compile(r"(초등학생|중학생|고등학생|청소년)")
OPEN_TO_ALL = re.compile(r"대학생|대학\s*\(?원\)?생|일반인?|누구나|전\s*국민|성인|고등학생\s*이상|제한\s*없")


# qwen이 숫자·영문 뒤에 띄어 쓰는 것("3 학년 2 학기", "Python 과")을 붙인다
UNIT_SPACE = re.compile(r"(\d) (학년|학기|년|월|일|개월|주|시간|분|개|건|회|차|기|대)")
# 할 일 시기: "2026년 1학기", "2026년 상반기" — 오늘보다 앞이면 판단 오류
# [63] 후보 번호가 할 일·이유에 새어 나옴("R1 미니 인턴십"), 학기와 방학이 안 맞음("1학기 겨울방학")
ROW_REF = re.compile(r"\bR\d+\b")
TERM_MISMATCH = re.compile(r"1\s*학기\s*(중\s*)?겨울\s*방학|2\s*학기\s*(중\s*)?여름\s*방학")
TERM_TEXT = re.compile(r"(20\d{2})\s*년\s*(?:([12])\s*학기|(상반기|하반기))")
MONTH_TEXT = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월")  # [64] "2026년 1월 겨울방학" (오늘 2026년 11월)
PARTICLE_SPACE = re.compile(r"([A-Za-z0-9)]) (과|와|를|을|이|가|은|는|에|의|로|으로|에서|도|만)(?=[\s.,]|$)")


def tidy(text: str) -> str:
    return PARTICLE_SPACE.sub(r"\1\2", UNIT_SPACE.sub(r"\1\2", " ".join(text.split())))


def _today(today: int | None) -> int:
    return today or int(date.today().strftime("%Y%m%d"))


def term_of(day: int) -> tuple[int, int]:
    """YYYYMMDD → (학년도, 학기). 1학기 3~8월, 2학기 9~2월 (1·2월은 앞 해 2학기)."""
    y, m = day // 10000, day // 100 % 100
    return (y, 1) if 3 <= m <= 8 else (y, 2) if m >= 9 else (y - 1, 2)


def past_terms(text: str, today: int) -> list[str]:
    """할 일·이유에 적힌 시기 중 이미 지난 것 ("2026년 1학기"·"2026년 상반기")."""
    now = term_of(today)
    out = []
    for m in TERM_TEXT.finditer(text):
        y = int(m.group(1))
        term = int(m.group(2)) if m.group(2) else (1 if m.group(3) == "상반기" else 2)
        if (y, term) < now:
            out.append(m.group(0))
    for m in MONTH_TEXT.finditer(text):
        if (int(m.group(1)), int(m.group(2))) < (today // 10000, today // 100 % 100):
            out.append(m.group(0))
    return out


def _companies(agg: AggregationResult) -> set[str]:
    """집계 표 전체에 나온 기업 이름 (지어낸 기업 확인용)."""
    return {c for r in agg.skills for c in r.companies if len(c) >= 2}


def verify_slot(slot: str, drafts: list[RankDraft], cands: list[tuple[SkillRow, RowGap]],
                agg: AggregationResult, cap: int, today: int | None = None) -> tuple[list[RankCard], list[RankIssue]]:
    """우선순위 LLM 출력 → 근거 순위 카드 + 문제 목록."""
    today = _today(today)
    issues, out, seen = [], [], set()
    all_companies = _companies(agg)
    job_companies = {n for c in agg.job.companies for n in (c.name, c.original, *c.keywords)}
    for d in drafts:
        i = int(d.row[1:]) - 1
        if not 0 <= i < len(cands):
            issues.append(RankIssue(slot=slot, kind="판단 오류", row=d.row, message=f"{d.row}는 후보 목록에 없는 번호"))
            continue
        if i in seen:
            issues.append(RankIssue(slot=slot, kind="주의", row=d.row, message=f"{d.row}를 두 번 적음 (뒤엣것 버림)"))
            continue
        row, gap = cands[i]
        reason, action = tidy(d.reason), tidy(d.action)
        bad = []
        if len(reason) < MIN_TEXT or len(action) < MIN_TEXT:
            bad.append(f"{d.row}의 이유나 할 일이 비어 있음")
        nums = {int(m.group(1)) for m in COUNT_WORDS.finditer(reason)}
        if nums - {row.doc_count, row.card_count, row.fulltext_docs}:
            bad.append(f"{d.row}의 이유에 근거와 다른 숫자가 있음 (숫자는 쓰지 말 것): {reason}")
        # 이유는 그 후보 근거의 기업만 (관심 기업이라도 근거에 없으면 "루닛의 핵심 기술" 같은 지어낸 주장이 됨).
        # 할 일에는 관심 기업(지원 목표)을 써도 된다
        # 후보 이름·근거 항목에 든 기업은 괜찮다 ("카카오 10K 해커톤"의 카카오)
        own = set(row.companies) | {c for c in all_companies | job_companies
                                     if any(c in t for t in (row.skill, *row.aliases))}
        foreign = [c for c in (all_companies | job_companies) - own if c in reason]
        foreign += [c for c in all_companies - own - job_companies if c in action and c not in foreign]
        if foreign:
            bad.append(f"{d.row}({row.skill})의 이유·할 일에 그 후보 근거에 없는 기업이 있음: {', '.join(foreign)}"
                       " (기업은 후보 줄의 '기업:'에 있을 때만)")
        if ROW_REF.search(text := action + " " + reason):
            bad.append(f"{d.row}의 이유·할 일에 후보 번호(R번호)를 씀 — 이름으로 쓸 것")
        if m := TERM_MISMATCH.search(text):
            bad.append(f"{d.row}의 시기가 맞지 않음: {m.group(0)} (겨울방학은 2학기 뒤, 여름방학은 1학기 뒤)")
        if past := past_terms(action + " " + reason, today):
            y, t = term_of(today)
            bad.append(f"{d.row}의 할 일 시기가 이미 지남: {', '.join(past)} (지금은 {y}년 {t}학기)")
        if bad:
            issues += [RankIssue(slot=slot, kind="판단 오류", row=d.row, message=m) for m in bad]
            continue
        seen.add(i)
        out.append(RankCard(
            slot=slot, rank=len(out) + 1, skill_id=row.skill_id, skill=row.skill, gap=gap.gap,
            action=action, reason=reason, card_ids=row.card_ids, doc_ids=row.doc_ids,
            categories=list(row.by_category), doc_count=row.doc_count, freq_rank=i + 1,
        ))
    if len(out) > cap:
        issues.append(RankIssue(slot=slot, kind="주의", message=f"상한 {cap}개를 넘어 {len(out) - cap}개를 뺌"))
        out = out[:cap]
    if not out and cands and not any(x.kind == "판단 오류" for x in issues):
        issues.append(RankIssue(slot=slot, kind="판단 오류", message="후보 중 하나도 고르지 않음. 목표 직무와 관련 있는 후보를 고를 것"))
    return out, issues


def _dates(line: str, year_hint: int) -> list[int]:
    """줄에서 날짜(YYYYMMDD) 찾기. 연도 없는 "11.30"·"11월 30일"은 같은 줄의 연도, 없으면 문서 작성 연도."""
    out = []
    years = []
    for m in FULL_DATE.finditer(line):
        y, mo, d = map(int, m.groups())
        years.append(y)
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append(y * 10000 + mo * 100 + d)
    rest = FULL_DATE.sub(" ", line)
    y = years[-1] if years else year_hint
    if y:
        for m in SHORT_DATE.finditer(rest):
            mo, d = map(int, m.groups())
            if 1 <= mo <= 12 and 1 <= d <= 31:
                out.append(y * 10000 + mo * 100 + d)
    return [v for v in out if 20150101 <= v <= 20351231]


def find_deadline(names: list[str], text: str, published_at: int | None, single: bool) -> int | None:
    """활동 이름이 나온 줄 근처(앞 WINDOW_BEFORE·뒤 WINDOW_AFTER줄)에서 접수·마감 줄의 가장 늦은 날짜.

    문서가 이 활동 하나만 다루면(single) 이름 위치와 상관없이 문서 전체의 접수·마감 줄에서 찾는다 ([67])."""
    lines = text.splitlines()
    keys = [_norm(n).lower() for n in names if len(_norm(n)) >= 2]
    hits = [i for i, ln in enumerate(lines) if any(k in _norm(ln).lower() for k in keys)]
    if single:  # [67] 활동 하나만 다루는 글은 문서 전체 (이름은 1~13줄, 마감일은 24줄이라 창 밖이던 공모전 공고)
        idx = range(len(lines))
    elif hits:
        idx = sorted({j for i in hits for j in range(max(0, i - WINDOW_BEFORE), min(len(lines), i + WINDOW_AFTER + 1))})
    else:
        return None
    year = published_at // 10000 if published_at else None
    found = [d for j in idx if DEADLINE_WORD.search(lines[j]) for d in _dates(lines[j], year)]
    return max(found) if found else None


def _activity_deadline(rank: RankCard, agg: AggregationResult, cards: dict, today: int) -> None:
    """마감일: 인용 → 본문 순. [63] 모음 글은 본문에서 찾으면 다음 활동의 날짜를 읽으므로(한국코드페어 8/18 → 다음 대회 12월)
    인용에 접수·마감 날짜가 있으면 그것만 쓴다. 날짜를 못 찾았는데 인용이 끝난 행사 기사이고 인용 날짜가 모두 지났으면 마감."""
    docs = {d.doc_id: d for d in agg.docs}
    names = [rank.skill, *[cards[c].item for c in rank.card_ids if c in cards]]
    best, newest, ended = None, None, False
    for doc_id in rank.doc_ids:
        doc = docs.get(doc_id)
        if doc is None:
            continue
        # [64] 제목도 같이 본다 ("… 미니 인턴십 모집 (~10/26)")
        quotes = "\n".join([doc.title] + [cards[c].quote for c in rank.card_ids if c in cards and cards[c].doc_id == doc_id])
        year = doc.published_at // 10000 if doc.published_at else None
        d = find_deadline(names, quotes, doc.published_at, single=True)
        if d is None:
            text = agg.texts.get(doc_id) or f"{doc.title}\n{doc.snippet}"
            n_acts = len({cards[c].item for c in cards if cards[c].doc_id == doc_id
                          and cards[c].category in ("대외활동", "교내프로그램")})
            d = find_deadline(names, text, doc.published_at, single=n_acts <= 1)
        if d is None and PAST_EVENT.search(quotes + doc.title):
            dates = _dates(quotes + " " + doc.title, year)
            # [67] 날짜가 없는 결과 기사("…논문경진대회 성료", "지난 3일 … 개최했다")는 작성일이 오늘보다 앞이면 끝난 행사
            ended = ended or (max(dates) < today if dates else bool(
                ENDED_FOR_SURE.search(quotes + " " + doc.title) and doc.published_at and doc.published_at < today))
        if d and (best is None or d > best):
            best = d
        if doc.published_at and (newest is None or doc.published_at > newest):
            newest = doc.published_at
    rank.deadline = best
    if best is None and ended:
        rank.deadline_status = "마감"
        rank.notes.append("이미 끝난 행사 기사 — 다음 회차 모집 일정 확인")
        return
    if best is None:
        rank.deadline_status = "확인 필요"
        note = "모집 기간을 원문에서 찾지 못함 — 링크에서 확인"
        if newest:
            age = (date(today // 10000, today // 100 % 100, today % 100)
                   - date(newest // 10000, max(1, newest // 100 % 100), max(1, newest % 100))).days
            if age > OLD_DOC_DAYS:
                note += f" (자료 작성 {newest // 10000}년)"
        rank.notes.append(note)
    elif best < today:
        rank.deadline_status = "마감"
        rank.notes.append(f"{best // 10000}.{best // 100 % 100}.{best % 100} 마감된 회차 — 다음 모집 일정 확인")
    else:
        rank.deadline_status = "모집 중"


# [65] 활동 카드가 아니어도 이름이 모집·행사를 가리키면 대상·마감 검사를 한다 (포트폴리오 슬롯의 "AI 엔지니어 미니 인턴십(~10/26)")
EVENT_WORD = re.compile(r"대회|공모|해커톤|데이터?톤|챌린지|challenge|contest|캠프|camp|인턴|서포터즈|기자단|경진|올림피아드|"
                        r"부트캠프|아카데미|academy|모집|프로그램", re.I)
REQUIREMENT_SLOTS = {"채용 요건", "자격증"}  # 기술·조건·자격증 이름 — 공고 마감일을 붙이면 안 됨


def is_event(r: RankCard, cards: dict) -> bool:
    """모집 기간이 있는 활동인지: 활동 카드(대외활동·교내프로그램)이거나, 채용 요건·자격증 슬롯이 아니면서 이름·item이 행사·모집."""
    if any(c in ("대외활동", "교내프로그램") for c in r.categories):
        return True
    if r.slot in REQUIREMENT_SLOTS:
        return False
    return any(EVENT_WORD.search(t) for t in (r.skill, *[cards[c].item for c in r.card_ids if c in cards]))


SCHEDULE_MAX = 3          # [67] 순위 카드마다 붙일 일정 수
SCHEDULE_CONTEXT = 60     # 일정 줄 길이 (날짜 앞뒤 글자 수)


SCHEDULE_WORD = re.compile(r"시험|접수|원서|필기|실기|회차|제\s*\d+\s*회|일정|마감|기간|발표|모집|신청|개최|본선|예선")


def _clip(line: str) -> str:
    line = " ".join(line.split())
    return line if len(line) <= SCHEDULE_CONTEXT * 2 else line[:SCHEDULE_CONTEXT * 2] + "…"


def upcoming_dates(r: RankCard, agg: AggregationResult, cards: dict, today: int) -> list[str]:
    """[67] 시험·접수·대회 일정: 오늘 이후 날짜와 그 줄 (답변 LLM은 이 목록의 날짜만 씀).

    인용·출처 제목의 날짜 → 본문에서 이름이 나온 줄 근처(앞 WINDOW_BEFORE·뒤 WINDOW_AFTER줄, 제목에 이름이 있으면 문서 전체)의
    일정 말(시험·접수·원서·마감 …)이 있는 줄. 인용에는 설명 문장만 들어가는 일이 많아 본문도 본다 (코드가 날짜만 찾음 — LLM 입력을 자르는 것이 아님).
    활동은 앞에서 찾은 마감일(deadline)도 넣는다."""
    docs = {d.doc_id: d for d in agg.docs}
    names = [n for n in dict.fromkeys([r.skill, *[cards[c].item for c in r.card_ids if c in cards]]) if len(_norm(n)) >= 2]
    keys = [_norm(n).lower() for n in names]
    out: dict[int, str] = {}
    if r.deadline and r.deadline >= today:
        out[r.deadline] = "접수 마감"

    def take(line: str, year: int | None, need_word: bool) -> None:
        if need_word and not SCHEDULE_WORD.search(line):
            return
        for d in _dates(line, year):
            if d >= today and d not in out:
                out[d] = _clip(line)

    for doc_id in dict.fromkeys(cards[c].doc_id for c in r.card_ids if c in cards):
        doc = docs.get(doc_id)
        if doc is None:
            continue
        year = doc.published_at // 10000 if doc.published_at else None
        quotes = [cards[c].quote for c in r.card_ids if c in cards and cards[c].doc_id == doc_id]
        for line in re.split(r"\n|(?<=[.!?])\s+", "\n".join([doc.title, *quotes])):
            take(line, year, need_word=False)
        text = agg.texts.get(doc_id)
        if not text:
            continue
        lines = text.splitlines()
        whole = any(k in _norm(doc.title).lower() for k in keys)
        hits = range(len(lines)) if whole else sorted({j for i, ln in enumerate(lines) if any(k in _norm(ln).lower() for k in keys)
                                                     for j in range(max(0, i - WINDOW_BEFORE), min(len(lines), i + WINDOW_AFTER + 1))})
        for j in hits:
            take(lines[j], year, need_word=True)
    return [f"{d // 10000}.{d // 100 % 100}.{d % 100} — {ctx}" for d, ctx in sorted(out.items())[:SCHEDULE_MAX]]


def check_ranks(ranks: list[RankCard], agg: AggregationResult,
                today: int | None = None) -> tuple[list[RankCard], list[RankIssue], list[RankCard]]:
    """검증을 통과한 순위 카드 확인: 인용 재대조 → 교과목 학년 → 활동 대상·마감일. 슬롯 안 순위를 다시 매긴다.

    반환: (추천, 문제, 지난 회차). [65] 마감된 활동은 다시 열릴 수 있어 "이런 활동이 있다"는 참고로 남기되 추천에서는 뺀다."""
    today = _today(today)
    cards = {c.card_id: c for c in agg.cards}
    grade = agg.job.student.grade
    issues, out, past = [], [], []
    for r in ranks:
        # doc_id·인용: 본문이 있는 문서는 인용이 본문에 있는지 다시 본다
        kept = []
        for cid in r.card_ids:
            c = cards.get(cid)
            if c is None:
                issues.append(RankIssue(slot=r.slot, kind="주의", message=f"{r.skill}: 근거 번호 {cid}의 카드가 없음"))
                continue
            text = agg.texts.get(c.doc_id)
            if text is not None and _norm(c.quote) not in _norm(text):
                issues.append(RankIssue(slot=r.slot, kind="주의", message=f"{r.skill}: {cid} 인용이 본문에 없음 (뺌)"))
                continue
            kept.append(cid)
        if not kept:
            issues.append(RankIssue(slot=r.slot, kind="주의", message=f"{r.skill}: 남은 근거가 없어 순위에서 뺌"))
            continue
        r.card_ids = kept
        r.doc_ids = list(dict.fromkeys(cards[c].doc_id for c in kept))
        r.doc_count = len(r.doc_ids)
        # 교과목 확인은 활동·공모전·교과목 슬롯만 (채용 요건 Skill 줄에 교과목 카드가 섞여도 학년을 붙이지 않음)
        course_cards = [cards[c] for c in kept if cards[c].category == "교과목"] if r.slot == ACTIVITY else []
        if course_cards:
            grades = [c.grade for c in course_cards if c.grade]
            r.grade = max(grades) if grades else None
            r.course_type = next((c.course_type for c in course_cards if c.course_type), None)
            if grade and r.grade and r.grade < grade:
                issues.append(RankIssue(slot=r.slot, kind="주의", message=f"{r.skill}: {r.grade}학년 과목이라 {grade}학년 학생에게서 뺌"))
                continue
            if r.grade is None:
                r.notes.append("개설 학년을 원문 표에서 읽지 못함 — 교육과정에서 확인")
        if is_event(r, cards):
            # [63] 참가 대상이 초·중·고등학생뿐이면 뺀다 (대학생 추천에 "중학생·고등학생" 해커톤이 나옴)
            # [64] 출처 제목도 본다 ("국내 최대 청소년 로봇 축제 '2026 월드 로봇 올림피아드 한국대회'")
            titles = " ".join(d.title for d in agg.docs if d.doc_id in {cards[c].doc_id for c in kept})
            quotes = " ".join(cards[c].quote for c in kept) + " " + titles
            if SCHOOL_ONLY.search(quotes) and not OPEN_TO_ALL.search(quotes):
                issues.append(RankIssue(slot=r.slot, kind="주의", message=f"{r.skill}: 참가 대상이 초·중·고등학생이라 뺌"))
                continue
            _activity_deadline(r, agg, cards, today)
            if r.deadline_status == "마감":
                issues.append(RankIssue(slot=r.slot, kind="주의", message=f"{r.skill}: 마감된 회차라 추천에서 빼고 참고로 남김"))
                past.append(r)
                continue
        if r.slot == "자격증" or is_event(r, cards):  # [67] 시험·접수·대회 일정
            r.schedule = upcoming_dates(r, agg, cards, today)
        out.append(r)
    # 슬롯 순서는 그대로, 순위 다시 매김
    by_slot: dict[str, list[RankCard]] = {}
    for r in out:
        by_slot.setdefault(r.slot, []).append(r)
    final = []
    for rs in by_slot.values():
        for i, r in enumerate(rs, 1):
            r.rank = i
            final.append(r)
    return final, issues, past
