"""Gap 판단: 근거 집계 표(SkillRow)의 줄마다 학생이 이미 가졌는지.

두 경로를 쓴다.
- db: student_id가 있으면 인호 src/gap_service.calculate_skill_gaps (student_skill 테이블과 skill_id 비교).
      인호 함수는 Gap이 있는 줄(미보유·수준부족·매핑되지 않음)만 돌려주므로, 돌아오지 않은 매핑된 줄 = 보유.
- profile: student_id가 없거나 DB 호출이 실패하면 1단계 TargetJob의 보유 항목(기술·자격증·참여 활동)으로 판단.
      보유 기술·자격증을 3단계와 같은 방법(stage3_aggregate.match_skills: 표기 대조 → 임베딩 후보 → 매칭·확인 LLM)으로
      Skill 사전 skill_id에 붙여 같은 skill_id면 보유. 자격증은 이름·별칭이 같을 때만 (3단계 EXACT_ONLY와 같은 규칙).
      사전에 없는 줄은 Skill 이름이 학생 입력과 표기만 다를 때 보유, 아니면 "매핑되지 않음".
      [63] 자격증 줄은 근거 이름의 첫 단어가 학생 자격증과 같아도 보유 ("AICE" → "AICE Associate"). 첫 단어만 보므로
      "정보처리기사" ↔ "정보처리산업기사"는 다르게 본다.
db 결과에도 profile 대조를 한 번 더 한다 — 학생이 폼에 적었지만 student_skill에 아직 없는 것(예: 방금 입력한 자격증)을 보유로 올린다.
"""
import logging

from engine.pipeline.stage3_aggregate import _key, match_skills
from engine.schemas.aggregate import SkillEntry, SkillRow
from engine.schemas.job import TargetJob
from engine.schemas.rank import RowGap

log = logging.getLogger(__name__)


def row_key(row: SkillRow) -> int | str:
    """집계 표 줄 키: skill_id, 없으면 표기 무시한 Skill 이름 (3단계 build_table과 같은 기준)."""
    return row.skill_id if row.skill_id is not None else _key(row.skill)


def held_names(job: TargetJob) -> dict[str, list[str]]:
    """학생 보유 항목: 표준 이름 → [표준 이름, 원문]. 분류별(기술·자격증·활동)."""
    out = {"skill": {}, "cert": {}, "activity": {}}
    for h in job.skills:
        out["skill"].setdefault(h.name, list(dict.fromkeys([h.name, h.original])))
    for h in job.certificates:
        out["cert"].setdefault(h.name, list(dict.fromkeys([h.name, h.original])))
    for a in job.student.activities:  # 참여한 비교과 활동은 1단계가 정리하지 않아 폼 원문 그대로
        if a.strip():
            out["activity"].setdefault(a.strip(), [a.strip()])
    return out


def _profile_ids(target: str, job: TargetJob, skill_dict: list[SkillEntry] | None, log_fn) -> dict[int, str]:
    """보유 기술·자격증 → skill_id. 반환: skill_id → 학생 입력 이름."""
    if not skill_dict:
        return {}
    held = held_names(job)
    members = {**held["skill"], **held["cert"]}
    if not members:
        return {}
    found = match_skills(target, members, skill_dict, exact_only=set(held["cert"]), log=log_fn)
    return {e.skill_id: name for name, e in found.items()}


def gap_from_profile(rows: list[SkillRow], job: TargetJob, target: str,
                     skill_dict: list[SkillEntry] | None, log_fn=print) -> list[RowGap]:
    held_ids = _profile_ids(target, job, skill_dict, log_fn)
    held = held_names(job)
    by_key = {_key(n): name for group in held.values() for name, ns in group.items() for n in ns}
    certs = {_key(n): name for name, ns in held["cert"].items() for n in ns}
    out = []
    for r in rows:
        hit = held_ids.get(r.skill_id) if r.skill_id is not None else None
        if hit is None:  # 사전 매칭이 안 됐어도 이름이 같으면 보유 (사전에 없는 Skill, 활동 이름 등)
            hit = next((by_key[_key(n)] for n in (r.skill, *r.names) if _key(n) in by_key), None)
        if hit is None and r.slot == "자격증":  # [63] 학생 "AICE" ↔ 근거 "AICE Associate": 근거 이름의 첫 단어가 학생 자격증 이름
            hit = next((certs[_key(n.split()[0])] for n in (r.skill, *r.names)
                        if len(n.split()) > 1 and _key(n.split()[0]) in certs), None)
        if hit is not None:
            gap = "보유"
        else:
            gap = "미보유" if r.skill_id is not None else "매핑되지 않음"
        out.append(RowGap(skill_id=r.skill_id, skill=r.skill, gap=gap, source="profile", matched=hit))
    return out


def gap_from_db(rows: list[SkillRow], student_id: int) -> list[RowGap]:
    """인호 Gap API. 실패하면 예외 그대로 (호출한 쪽이 profile로 바꾼다)."""
    from src.gap_service import calculate_skill_gaps  # DB 패키지는 쓸 때만

    result = calculate_skill_gaps(student_id, [r.model_dump(include={"skill_id", "skill"}) for r in rows])
    by_id = {g["skill_id"]: g for g in result if g.get("skill_id") is not None}
    out = []
    for r in rows:
        if r.skill_id is None:
            out.append(RowGap(skill_id=None, skill=r.skill, gap="매핑되지 않음", source="db"))
        elif r.skill_id in by_id:
            g = by_id[r.skill_id]
            out.append(RowGap(skill_id=r.skill_id, skill=r.skill, gap=g["gap"], source="db",
                              student_level=g.get("student_level")))
        else:
            out.append(RowGap(skill_id=r.skill_id, skill=r.skill, gap="보유", source="db"))
    return out


def compute_gaps(rows: list[SkillRow], job: TargetJob, target: str, skill_dict: list[SkillEntry] | None,
                 student_id: int | None = None, log_fn=print) -> tuple[list[RowGap], str, str | None]:
    """반환: (줄마다 RowGap — rows와 같은 순서, 출처 "db"/"profile", db 오류 메시지)."""
    profile = gap_from_profile(rows, job, target, skill_dict, log_fn)
    if student_id is None:
        return profile, "profile", None
    try:
        db = gap_from_db(rows, student_id)
    except Exception as e:  # 학생이 DB에 없음, DB 꺼짐 등
        log_fn(f"인호 Gap API 실패 → 1단계 보유 항목으로 판단: {type(e).__name__}: {e}")
        return profile, "profile", f"{type(e).__name__}: {e}"
    # 학생이 폼에 적은 보유 항목은 DB에 없어도 보유로 올린다
    for d, p in zip(db, profile):
        if d.gap != "보유" and p.gap == "보유":
            d.gap, d.matched = "보유", p.matched
    return db, "db", None
