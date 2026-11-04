"""3단계 근거 집계: 코드(중복 제거) → 역할 LLM(문서 역할) → 이름 LLM+임베딩+합치기 LLM(Skill) → 사전 매칭(skill_id) → 코드(세기).

1. 코드가 (doc_id, quote)가 같은 카드를 지우고 근거 번호(C1, C2 …)를 붙인다 (같은 인용이 분류만 바꿔 두 번 나옴)
2. 역할 LLM이 카드가 나온 문서마다 역할(채용공고·자격증정보·프로젝트추천 등)을 고른다 (DocRoleList)
3. 이름 통일
   - 이름 뽑기 LLM이 카드마다 item과 인용을 보고 짧은 Skill 이름 목록을 붙인다 (SkillNameList). 2단계 item은 긴 문장이라 그대로는 못 묶음
     [21] 한 카드에 여러 개("Git, CLI, Linux")면 나눠 받고, 카드 하나가 집계 표 여러 줄에 들어간다
   - 코드가 표기만 다른 이름(띄어쓰기·대소문자)을 합치고, bge-m3 임베딩이 비슷한 이름끼리 후보 묶음을 만든다
   - 합치기 LLM이 2개 이상인 후보 묶음만 보고 같은 것을 합친다 (SkillGroupList)
   ※ 긴 item 전체를 한 번에 묶게 하면 ADsP·빅데이터분석기사를 AICE로 합치는 등 과하게 묶음 (2026-10-30)
4. [41] DB Skill 사전(skill·skill_alias 테이블, tools/skill_dict)에서 skill_id를 찾는다
   - 코드가 대표 이름·묶인 이름을 사전 이름·별칭과 표기 무시하고 대조 → 같으면 그 skill_id
   - 나머지는 bge-m3 임베딩으로 사전 후보(MATCH_MIN_SIM 이상, MATCH_TOP_K개)를 골라 사전 매칭 LLM이 하나 또는 "없음"을 고른다
     (SkillMatchList). 후보가 없으면 묻지 않고 null
     ※ 유사도 하한 없이 물으면 9B 모델이 "없음" 대신 억지로 고름 (ADsP→AWS 0.46, 학사 이상→LLM 0.54, CUDA→CI/CD 0.56)
   - 찾으면 Skill 이름을 사전 이름으로 바꾼다 (다른 대표 이름이 같은 skill_id면 한 줄로 합쳐짐). 없으면 skill_id null
   사전에 없는 skill_id는 만들지 않는다 (인호 쪽 규칙: 모델은 기존 Skill ID만 고름). DB를 못 읽으면 모두 null
5. 코드가 Skill마다 고유 문서 수·분류별·역할별 수·기업·근거 번호를 센다 (SkillRow)
   [33] Skill마다 가이드 슬롯(교과목·채용 요건·자격증 …)을 붙이고 슬롯 순으로 정렬 → 가이드 목차가 된다

기업정보 카드는 Skill이 아니라서 집계 표에서 뺀다 (카드 목록에는 남김).
Gap(학생 보유 여부)은 이 표의 skill_id로 인호 쪽 코드가 판단한다.
"""
import json
import re
import time
from collections import Counter, defaultdict

from engine.llm import LLMConfig, chat, embed
from engine.pipeline.stage2_evidence import SLOT_OF, SLOTS, ExtractionResult, _norm, make_target
from engine.prompts.stage3_aggregate import (
    CHECK_REQUEST, CHECK_SYSTEM, MATCH_REQUEST, MATCH_SYSTEM, MERGE_REQUEST, MERGE_SYSTEM, NAME_REQUEST, NAME_SYSTEM, ROLE_REQUEST, ROLE_SYSTEM,
)
from engine.schemas import EvidenceCard, EvidenceDoc
from engine.schemas.aggregate import (
    AggregationResult, DocRole, DocRoleList, MatchCheckList, NumberedCard, SkillEntry, SkillGroupList, SkillMatchList,
    SkillNameList, SkillRow,
)
from engine.tools.skill_dict import load_skill_dict

# 역할별 LLM 설정
ROLE_CLASSIFIER = LLMConfig(temperature=0)
SKILL_NAMER = LLMConfig(temperature=0)

SIM_THRESHOLD = 0.80        # 후보 묶음 기준 코사인 유사도 (bge-m3, 실험값)
MAX_NAMES_PER_ITEM = 5      # [21] 카드 하나에 붙이는 Skill 상한 (긴 도구 나열이 표를 덮지 않게)
MATCH_TOP_K = 5             # [41] 사전 매칭 LLM에 보여 줄 Skill당 사전 후보 수
MATCH_MIN_SIM = 0.65        # [41] 이보다 덜 비슷한 사전 항목은 후보로 안 보여 줌 (bge-m3, 실험값)
# [44] 이 분류의 카드에서만 나온 이름은 기술이 아니라 자격증·과목·프로그램·대회 이름이라 사전과 이름·별칭이 같을 때만 붙인다
#      (AWS AI Practitioner → AWS, AWS 기반 AI 창업캠프 → AWS, 고성능 GPU 서버 → Backend Development)
EXACT_ONLY_CATEGORIES = {"자격증", "교과목", "교내프로그램", "대외활동"}
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
        domain = "DB 자료" if doc.provider == "rag" else doc.domain  # [48] RAG 문서는 도메인이 없음 (2단계 표시와 같게)
        blocks.append(f"[{nums[doc.doc_id]}] {doc.title}\n출처: {domain}{source}\n요약: {doc.snippet}\n"
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


def _split_names(names: list[str]) -> list[str]:
    """[21] 이름 목록 정리: 번호 떼기, 띄운 "/"로 이어 쓴 것("Git / CLI / Linux") 나누기, 표기만 다른 것 빼기, 상한.

    붙여 쓴 "/"(CI/CD, TCP/IP)는 한 이름이라 나누지 않는다.
    """
    out = {}
    for name in names:
        for part in re.split(r"\s+/\s+", _clean(name)):
            if part.strip():
                out.setdefault(_key(part), part.strip())
    return list(out.values())[:MAX_NAMES_PER_ITEM]


def name_items(target: str, cards: list[NumberedCard]) -> list[list[str]]:
    """이름 뽑기 LLM: 카드 item마다 짧은 Skill 이름 목록. LLM이 빠뜨리거나 비운 항목은 [item]."""
    messages = [{"role": "system", "content": NAME_SYSTEM},
                {"role": "user", "content": NAME_REQUEST.format(target=target, items=render_items(cards))}]
    msg = chat(messages, SKILL_NAMER, format=SkillNameList.model_json_schema())
    names = {}
    for d in SkillNameList.model_validate(json.loads(msg["content"])).items:
        i = int(d.item[1:]) - 1
        if 0 <= i < len(cards) and i not in names and (ns := _split_names(d.names)):
            names[i] = ns
    return [names.get(i, [c.item]) for i, c in enumerate(cards)]


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


def render_matches(names: list[str], cands: list[list[int]], by_id: dict[int, SkillEntry]) -> str:
    """사전 매칭 LLM용: Skill(N번호)마다 사전 후보(S번호 = skill_id)·이름·별칭."""
    lines = []
    for i, (name, ids) in enumerate(zip(names, cands), 1):
        lines.append(f"N{i} {name}")
        for sid in ids:
            e = by_id[sid]
            alias = f" (별칭: {', '.join(e.aliases)})" if e.aliases else ""
            lines.append(f"   S{sid} {e.name}{alias}")
    return "\n".join(lines)


def render_pairs(pairs: list[tuple[str, SkillEntry]]) -> str:
    """[44] 사전 매칭 확인 LLM용: P번호 · 근거 Skill → 사전 Skill (별칭)."""
    lines = []
    for i, (name, e) in enumerate(pairs, 1):
        alias = f" (별칭: {', '.join(e.aliases)})" if e.aliases else ""
        lines.append(f"P{i} {name} → {e.name}{alias}")
    return "\n".join(lines)


def check_matches(target: str, pairs: list[tuple[str, SkillEntry]]) -> list[bool]:
    """[44] 사전 매칭 확인 LLM: 짝마다 Gap 기준으로 맞는지. LLM이 빠뜨린 짝은 버린다(아니오)."""
    messages = [{"role": "system", "content": CHECK_SYSTEM},
                {"role": "user", "content": CHECK_REQUEST.format(target=target, pairs=render_pairs(pairs))}]
    msg = chat(messages, SKILL_NAMER, format=MatchCheckList.model_json_schema())
    ok = {}
    for p in MatchCheckList.model_validate(json.loads(msg["content"])).pairs:
        ok.setdefault(int(p.pair[1:]) - 1, p.ok == "예")
    return [ok.get(i, False) for i in range(len(pairs))]


def match_skills(target: str, members: dict[str, list[str]], skill_dict: list[SkillEntry],
                 exact_only: set[str] = frozenset(), log=print) -> dict[str, SkillEntry]:
    """[41] 대표 Skill 이름 → 사전 항목. members: 대표 이름 → 그 대표로 묶인 이름(이름 뽑기 LLM 결과).

    대표 이름이나 묶인 이름이 사전 이름·별칭과 표기만 다르면 코드가 바로 정하고,
    나머지만 임베딩 후보(MATCH_MIN_SIM 이상)를 붙여 사전 매칭 LLM에 묻는다. 사전에 없는 것은 반환에 넣지 않는다.
    [44] exact_only(자격증·과목·프로그램·대회 이름)는 LLM에 묻지 않고, LLM이 고른 짝은 확인 LLM이 한 번 더 본다.
    """
    by_key = {}
    for e in skill_dict:
        for t in (e.name, *e.aliases):
            by_key.setdefault(_key(t), e)
    found = {}
    for rep, names in members.items():
        hit = next((by_key[_key(n)] for n in (rep, *names) if _key(n) in by_key), None)
        if hit:
            found[rep] = hit
    rest = [rep for rep in members if rep not in found and rep not in exact_only]
    if not rest:
        return found
    # 사전 이름·별칭을 각각 임베딩하고 skill_id마다 가장 가까운 것으로 순위를 매긴다
    texts = [(e.skill_id, t) for e in skill_dict for t in (e.name, *e.aliases)]
    vecs = embed([t for _, t in texts] + rest)
    dict_vecs, rest_vecs = vecs[:len(texts)], vecs[len(texts):]
    asked, cands = [], []
    for rep, v in zip(rest, rest_vecs):
        best = {}
        for (sid, _), u in zip(texts, dict_vecs):
            best[sid] = max(best.get(sid, -1.0), sum(a * b for a, b in zip(v, u)))
        ids = [sid for sid in sorted(best, key=best.get, reverse=True)[:MATCH_TOP_K] if best[sid] >= MATCH_MIN_SIM]
        if ids:  # 비슷한 사전 항목이 없으면 묻지 않고 null
            asked.append(rep)
            cands.append(ids)
    if not asked:
        return found
    rest = asked
    by_id = {e.skill_id: e for e in skill_dict}
    messages = [{"role": "system", "content": MATCH_SYSTEM},
                {"role": "user", "content": MATCH_REQUEST.format(target=target, skills=render_matches(rest, cands, by_id))}]
    msg = chat(messages, SKILL_NAMER, format=SkillMatchList.model_json_schema())
    picked = {}
    for m in SkillMatchList.model_validate(json.loads(msg["content"])).matches:
        i = int(m.skill[1:]) - 1
        if not 0 <= i < len(rest) or rest[i] in picked or m.choice == "없음":
            continue
        sid = int(m.choice[1:])
        if sid in cands[i]:  # 그 Skill의 후보 밖 번호는 버린다
            picked[rest[i]] = by_id[sid]
    if picked:
        pairs = list(picked.items())
        for (rep, e), ok in zip(pairs, check_matches(target, pairs)):
            if ok:
                found[rep] = e
            else:
                log(f"사전 매칭 확인에서 뺌: {rep} → {e.name}")
    return found


def build_table(cards: list[NumberedCard], roles: dict[str, DocRole]) -> list[SkillRow]:
    """Skill이 붙은 카드로 근거 집계 표를 센다. 수는 고유 doc_id 기준.

    [41] skill_id가 있으면 skill_id로, 없으면 이름으로 묶는다. [21] Skill이 여러 개인 카드는 여러 줄에 들어간다.
    """
    by_skill = defaultdict(list)   # 키 → 카드
    head = {}                      # 키 → (skill_id, Skill 이름)
    before = defaultdict(list)     # 키 → 사전 매칭 전 이름
    for c in cards:
        for skill, sid, name in zip(c.skills, c.skill_ids, c.skill_names):
            key = sid if sid is not None else skill
            head.setdefault(key, (sid, skill))
            by_skill[key].append(c)
            before[key].append(name)
    rows = []
    for key, cs in by_skill.items():
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
            skill_id=head[key][0], skill=head[key][1], slot=by_slot.most_common(1)[0][0] if by_slot else None,
            doc_count=len(doc_ids), card_count=len(cs),
            fulltext_docs=len({c.doc_id for c in cs if c.evidence_level == "fulltext"}),
            by_category={k: len(v) for k, v in by_category.items()}, by_role=dict(by_role),
            companies=list(dict.fromkeys(c.company for c in cs if c.company)),
            aliases=list(dict.fromkeys(c.item for c in cs)),
            names=list(dict.fromkeys(before[key])),
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
        sid = f"#{r.skill_id}" if r.skill_id is not None else r.mapping  # [49] 사전에 없으면 "매핑되지 않음"
        lines.append(f"{r.skill} ({sid}) | 문서 {r.doc_count}(본문 {r.fulltext_docs}) | 분류: {cat} | 역할: {role}"
                     f"{company} | 근거: {','.join(r.card_ids)} ({docs})")
    return "\n".join(lines)


def aggregate_evidence(ext: ExtractionResult, skill_dict: list[SkillEntry] | None = None,
                       log=print) -> AggregationResult:
    """skill_dict를 안 주면 DB에서 읽는다 (tools/skill_dict). 못 읽으면 skill_id 없이 집계."""
    timings = {}
    if skill_dict is None:
        skill_dict = load_skill_dict()
    target = make_target(ext.job)
    cards, duplicates = dedup_cards(ext.cards)
    log(f"카드 {len(ext.cards)}장 중 중복 {duplicates}장 제거")

    t = time.time()
    roles = classify_roles(target, ext.docs, cards)
    timings["role"] = time.time() - t
    log(f"문서 역할: {dict(Counter(roles.values()))}")

    t = time.time()
    targets = [c for c in cards if c.category not in SKIP_CATEGORIES]
    per_card = name_items(target, targets) if targets else []
    names = [n for ns in per_card for n in ns]
    rep, groups = merge_names(target, names) if names else ({}, [])
    timings["skill"] = time.time() - t
    log(f"항목 {len(targets)}개 → 이름 {len(set(names))}개 (후보 묶음 {len(groups)}개) → Skill {len(set(rep.values()))}개")

    # [41] 대표 Skill 이름 → DB 사전 skill_id. 찾으면 이름도 사전 이름으로 바꾼다
    t = time.time()
    members = defaultdict(list)
    for n in names:
        members[rep[n]].append(n)
    # [44] 자격증·과목·프로그램·대회 카드에서만 나온 이름은 사전과 이름이 같을 때만 붙인다
    cats = defaultdict(set)
    for c, ns in zip(targets, per_card):
        for n in ns:
            cats[rep[n]].add(c.category)
    exact_only = {r for r, cs in cats.items() if cs <= EXACT_ONLY_CATEGORIES}
    entries = match_skills(target, members, skill_dict, exact_only, log) if skill_dict and members else {}
    for c, ns in zip(targets, per_card):
        seen = set()
        for r in dict.fromkeys(rep[n] for n in ns):
            e = entries.get(r)
            skill, sid = (e.name, e.skill_id) if e else (r, None)
            key = sid if sid is not None else _key(skill)
            if key in seen:  # 다른 이름이 같은 사전 Skill로 붙으면 한 번만
                continue
            seen.add(key)
            c.skills.append(skill)
            c.skill_ids.append(sid)
            c.skill_names.append(r)
    timings["match"] = time.time() - t
    if skill_dict:
        log(f"사전 매칭: Skill {len(members)}개 중 {len(entries)}개 → skill_id {len({e.skill_id for e in entries.values()})}개")
    else:
        log("Skill 사전 없음 — skill_id 없이 집계")

    skills = build_table(cards, roles)
    return AggregationResult(
        job=ext.job, docs=ext.docs, cards=cards, doc_roles=roles, skills=skills, skill_dict_loaded=bool(skill_dict),
        duplicates=duplicates, timings={k: round(v, 1) for k, v in timings.items()}, texts=ext.texts,
    )
