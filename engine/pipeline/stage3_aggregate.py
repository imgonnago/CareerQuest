"""3단계 근거 집계: 코드(중복 제거) → 역할 LLM(문서 역할) → 이름 LLM+임베딩+합치기 LLM(Skill) → 코드(세기).

1. 코드가 (doc_id, quote)가 같은 카드를 지우고 근거 번호(C1, C2 …)를 붙인다 (같은 인용이 분류만 바꿔 두 번 나옴)
2. 역할 LLM이 카드가 나온 문서마다 역할(채용공고·자격증정보·프로젝트추천 등)을 고른다 (DocRoleList)
3. 이름 통일 (Skill 사전(README: 인호 담당)이 생기면 사전 매칭으로 바꿀 자리)
   - 이름 뽑기 LLM이 카드마다 item과 인용을 보고 짧은 Skill 이름을 붙인다 (SkillNameList). 2단계 item은 긴 문장이라 그대로는 못 묶음
   - 코드가 표기만 다른 이름(띄어쓰기·대소문자)을 합치고, bge-m3 임베딩이 비슷한 이름끼리 후보 묶음을 만든다
   - 합치기 LLM이 2개 이상인 후보 묶음만 보고 같은 것을 합친다 (SkillGroupList)
   ※ 긴 item 전체를 한 번에 묶게 하면 ADsP·빅데이터분석기사를 AICE로 합치는 등 과하게 묶음 (2026-10-30)
4. 코드가 Skill마다 고유 문서 수·분류별·역할별 수·기업·근거 번호를 센다 (SkillRow)
   [33] Skill마다 가이드 슬롯(교과목·채용 요건·자격증 …)을 붙이고 슬롯 순으로 정렬 → 가이드 목차가 된다

기업정보 카드는 Skill이 아니라서 집계 표에서 뺀다 (카드 목록에는 남김).
Gap(학생 보유 여부)은 DB가 없어 아직 계산하지 않는다.
"""
import json
import re
import time
from collections import Counter, defaultdict

from engine.llm import LLMConfig, chat, embed
from engine.pipeline.stage2_evidence import SLOT_OF, SLOTS, ExtractionResult, _norm, make_target
from engine.prompts.stage3_aggregate import (
    MERGE_REQUEST, MERGE_SYSTEM, NAME_REQUEST, NAME_SYSTEM, ROLE_REQUEST, ROLE_SYSTEM,
)
from engine.schemas import EvidenceCard, EvidenceDoc
from engine.schemas.aggregate import (
    AggregationResult, DocRole, DocRoleList, NumberedCard, SkillGroupList, SkillNameList, SkillRow,
)

# 역할별 LLM 설정
ROLE_CLASSIFIER = LLMConfig(temperature=0)
SKILL_NAMER = LLMConfig(temperature=0)

SIM_THRESHOLD = 0.80        # 후보 묶음 기준 코사인 유사도 (bge-m3, 실험값)
SKIP_CATEGORIES = {"기업정보"}  # Skill 집계에서 빼는 카드 분류
NO_COMPANY = {"null", "none", "없음", ""}


def dedup_cards(cards: list[EvidenceCard]) -> tuple[list[NumberedCard], int]:
    """(doc_id, quote)가 같은 카드는 하나만 남긴다. 기업정보가 아닌 분류를 우선한다."""
    kept: dict[tuple[str, str], EvidenceCard] = {}
    for card in cards:
        key = (card.doc_id, _norm(card.quote))
        if key not in kept or (kept[key].category in SKIP_CATEGORIES and card.category not in SKIP_CATEGORIES):
            kept[key] = card
    numbered = [NumberedCard(**c.model_dump(), card_id=f"C{i}") for i, c in enumerate(kept.values(), 1)]
    for c in numbered:  # LLM이 null 대신 문자열 "null"을 쓰는 경우가 있음
        if c.company and c.company.strip().lower() in NO_COMPANY:
            c.company = None
    return numbered, len(cards) - len(numbered)


def _doc_nums(docs: list[EvidenceDoc]) -> dict[str, str]:
    """doc_id → D번호. 2단계 근거 풀과 같은 번호 (docs가 D번호 순)."""
    return {d.doc_id: f"D{i}" for i, d in enumerate(docs, 1)}


def render_role_docs(docs: list[EvidenceDoc], cards: list[NumberedCard]) -> str:
    """역할 LLM용: 카드가 나온 문서마다 [D번호] 제목·출처·요약 + 그 문서 카드의 item."""
    nums = _doc_nums(docs)
    items = defaultdict(list)
    for c in cards:
        items[c.doc_id].append(c.item)
    blocks = []
    for doc in docs:
        if doc.doc_id not in items:
            continue
        source = f" · {doc.source_name}" if doc.source_name else ""
        blocks.append(f"[{nums[doc.doc_id]}] {doc.title}\n출처: {doc.domain}{source}\n요약: {doc.snippet}\n"
                      f"근거 항목: {'; '.join(items[doc.doc_id])}")
    return "\n\n".join(blocks)


def classify_roles(target: str, docs: list[EvidenceDoc], cards: list[NumberedCard]) -> dict[str, DocRole]:
    """역할 LLM: 카드가 나온 문서마다 역할을 고른다. 반환: doc_id → 역할 (LLM이 빠뜨린 문서는 기타)."""
    by_num = {num: doc_id for doc_id, num in _doc_nums(docs).items()}
    wanted = {c.doc_id for c in cards}
    if not wanted:
        return {}
    messages = [{"role": "system", "content": ROLE_SYSTEM},
                {"role": "user", "content": ROLE_REQUEST.format(target=target, docs=render_role_docs(docs, cards))}]
    msg = chat(messages, ROLE_CLASSIFIER, format=DocRoleList.model_json_schema())
    roles = {}
    for d in DocRoleList.model_validate(json.loads(msg["content"])).docs:
        doc_id = by_num.get(d.doc)
        if doc_id in wanted and doc_id not in roles:
            roles[doc_id] = d.role
    return {doc_id: roles.get(doc_id, "기타") for doc_id in wanted}


def _key(name: str) -> str:
    """표기 차이 판정용: 공백 제거, 소문자."""
    return re.sub(r"\s+", "", name).lower()


def _clean(name: str) -> str:
    """LLM이 이름 앞에 번호를 붙여 쓰는 경우("N3 AICE Associate")를 뗀다."""
    return re.sub(r"^\s*\[?[NIG][0-9]+\]?[\s:.)-]*", "", name).strip()


def render_items(cards: list[NumberedCard]) -> str:
    """이름 뽑기 LLM용: I번호(= 카드 순서)·분류·item + 인용.

    [27] item에 요구사항 대신 공고 직무명이 적히는 경우가 있어 인용(quote)을 같이 보여 주고 인용 기준으로 이름을 붙이게 한다.
    인용은 줄바꿈만 공백으로 바꾸고 자르지 않는다.
    """
    return "\n".join(f"I{i} [{c.category}] {c.item}\n   인용: {' '.join(c.quote.split())}"
                     for i, c in enumerate(cards, 1))


def name_items(target: str, cards: list[NumberedCard]) -> list[str]:
    """이름 뽑기 LLM: 카드 item마다 짧은 Skill 이름. LLM이 빠뜨리거나 비운 항목은 item 그대로."""
    messages = [{"role": "system", "content": NAME_SYSTEM},
                {"role": "user", "content": NAME_REQUEST.format(target=target, items=render_items(cards))}]
    msg = chat(messages, SKILL_NAMER, format=SkillNameList.model_json_schema())
    names = {}
    for d in SkillNameList.model_validate(json.loads(msg["content"])).names:
        i = int(d.item[1:]) - 1
        if 0 <= i < len(cards) and i not in names and _clean(d.name):
            names[i] = _clean(d.name)
    return [names.get(i, c.item) for i, c in enumerate(cards)]


def candidate_groups(names: list[str]) -> list[list[int]]:
    """임베딩 코사인 유사도가 SIM_THRESHOLD 이상인 이름끼리 이어 후보 묶음을 만든다 (union-find)."""
    vecs = embed(names)  # L2 정규화돼 있어 내적 = 코사인
    parent = list(range(len(names)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if sum(a * b for a, b in zip(vecs[i], vecs[j])) >= SIM_THRESHOLD:
                parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i in range(len(names)):
        groups[find(i)].append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def render_groups(names: list[str], groups: list[list[int]]) -> str:
    """합치기 LLM용: 후보 묶음(G번호)별 N번호·이름."""
    lines = []
    for g, members in enumerate(groups, 1):
        lines.append(f"G{g}")
        lines += [f"  N{i + 1} {names[i]}" for i in members]
    return "\n".join(lines)


def merge_names(target: str, names: list[str]) -> tuple[dict[str, str], list[list[int]]]:
    """이름 → 대표 이름. 표기만 다른 이름은 코드가 합치고, 임베딩이 비슷한 후보만 합치기 LLM이 판단한다.

    반환: (이름 → 대표 이름, LLM에 보인 후보 묶음). LLM이 빠뜨린 이름은 그대로 둔다.
    """
    canon = {}
    for n in names:
        canon.setdefault(_key(n), n)
    uniq = list(canon.values())
    rep = {n: canon[_key(n)] for n in names}
    groups = [g for g in candidate_groups(uniq) if len(g) > 1] if len(uniq) > 1 else []
    if not groups:
        return rep, []
    messages = [{"role": "system", "content": MERGE_SYSTEM},
                {"role": "user", "content": MERGE_REQUEST.format(target=target, groups=render_groups(uniq, groups))}]
    msg = chat(messages, SKILL_NAMER, format=SkillGroupList.model_json_schema())
    allowed = {i for g in groups for i in g}
    merged = {}
    for s in SkillGroupList.model_validate(json.loads(msg["content"])).skills:
        for ref in s.names:
            i = int(ref[1:]) - 1
            if i in allowed and i not in merged:
                merged[i] = _clean(s.name) or uniq[i]
    final = {uniq[i]: merged.get(i, uniq[i]) for i in range(len(uniq))}
    # 대표 이름끼리 표기만 다르면 다시 합친다
    canon2 = {}
    for u in uniq:
        final[u] = canon2.setdefault(_key(final[u]), final[u])
    return {n: final[r] for n, r in rep.items()}, groups


def build_table(cards: list[NumberedCard], roles: dict[str, DocRole]) -> list[SkillRow]:
    """Skill이 붙은 카드로 근거 집계 표를 센다. 수는 고유 doc_id 기준."""
    by_skill = defaultdict(list)
    for c in cards:
        if c.skill:
            by_skill[c.skill].append(c)
    rows = []
    for skill, cs in by_skill.items():
        doc_ids = list(dict.fromkeys(c.doc_id for c in cs))
        by_category = defaultdict(set)
        for c in cs:
            by_category[c.category].add(c.doc_id)
        by_role = defaultdict(int)
        for d in doc_ids:
            by_role[roles.get(d, "기타")] += 1
        by_slot = Counter()  # [33] 분류 → 슬롯별 문서 수. 가장 많은 슬롯을 이 Skill의 슬롯으로
        for k, v in by_category.items():
            if k in SLOT_OF:
                by_slot[SLOT_OF[k]] += len(v)
        rows.append(SkillRow(
            skill=skill, slot=by_slot.most_common(1)[0][0] if by_slot else None,
            doc_count=len(doc_ids), card_count=len(cs),
            fulltext_docs=len({c.doc_id for c in cs if c.evidence_level == "fulltext"}),
            by_category={k: len(v) for k, v in by_category.items()}, by_role=dict(by_role),
            companies=list(dict.fromkeys(c.company for c in cs if c.company)),
            aliases=list(dict.fromkeys(c.item for c in cs)),
            card_ids=[c.card_id for c in cs], doc_ids=doc_ids,
        ))
    order = {slot: i for i, slot in enumerate(SLOTS)}
    return sorted(rows, key=lambda r: (order.get(r.slot, len(order)), -r.doc_count, -r.fulltext_docs, -r.card_count))


def render_table(result: AggregationResult) -> str:
    """사람·4단계 LLM용 근거 집계 표 텍스트 (원문 없이 숫자·근거 번호)."""
    nums = _doc_nums(result.docs)
    lines, slot = [], ""
    for r in result.skills:
        if r.slot != slot or not lines:  # [33] 슬롯마다 제목 줄
            slot = r.slot
            lines.append(f"## {slot or '기타'}")
        cat = ", ".join(f"{k} {v}" for k, v in r.by_category.items())
        role = ", ".join(f"{k} {v}" for k, v in r.by_role.items())
        company = f" | 기업: {', '.join(r.companies)}" if r.companies else ""
        docs = ",".join(nums[d] for d in r.doc_ids)
        lines.append(f"{r.skill} | 문서 {r.doc_count}(본문 {r.fulltext_docs}) | 분류: {cat} | 역할: {role}"
                     f"{company} | 근거: {','.join(r.card_ids)} ({docs})")
    return "\n".join(lines)


def aggregate_evidence(ext: ExtractionResult, log=print) -> AggregationResult:
    timings = {}
    target = make_target(ext.job)
    cards, duplicates = dedup_cards(ext.cards)
    log(f"카드 {len(ext.cards)}장 중 중복 {duplicates}장 제거")

    t = time.time()
    roles = classify_roles(target, ext.docs, cards)
    timings["role"] = time.time() - t
    log(f"문서 역할: {dict(Counter(roles.values()))}")

    t = time.time()
    targets = [c for c in cards if c.category not in SKIP_CATEGORIES]
    names = name_items(target, targets) if targets else []
    rep, groups = merge_names(target, names) if names else ({}, [])
    for c, n in zip(targets, names):
        c.skill = rep[n]
    timings["skill"] = time.time() - t
    log(f"항목 {len(targets)}개 → 이름 {len(set(names))}개 (후보 묶음 {len(groups)}개) → Skill {len(set(rep.values()))}개")

    skills = build_table(cards, roles)
    return AggregationResult(
        job=ext.job, docs=ext.docs, cards=cards, doc_roles=roles, skills=skills,
        duplicates=duplicates, timings={k: round(v, 1) for k, v in timings.items()},
    )
