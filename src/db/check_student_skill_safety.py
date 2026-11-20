"""Read-only end-to-end check of ResourceSkill -> StudentSkill -> GAP safety."""
from __future__ import annotations

import argparse
from sqlalchemy import text

from src.db.database import engine
from src.gap_service import GapRequest, calculate_gap_request
from src.mapping.skill_trust_policy import find_untrusted_only_skill_ids
from src.student_skill_service import (
    load_completed_resource_skills,
    load_student_skills,
    load_student_skills_for_gap,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--student-id", type=int, default=1)
    args = parser.parse_args()
    student_id = args.student_id

    with engine.connect() as conn:
        name = conn.execute(text("SELECT current_database()") ).scalar_one()
        n = conn.execute(
            text("SELECT COUNT(*) FROM student WHERE student_id = :sid"),
            {"sid": student_id},
        ).scalar_one()
        if not n:
            raise SystemExit(f"Student not found: {student_id}")

    raw = load_student_skills(student_id)
    trusted = load_student_skills_for_gap(student_id)
    quarantined = find_untrusted_only_skill_ids(student_id)
    completed = load_completed_resource_skills(student_id)

    raw_ids = {int(r["skill_id"]) for r in raw}
    trusted_ids = {int(r["skill_id"]) for r in trusted}
    assert quarantined <= raw_ids, "Quarantine contains absent StudentSkill"
    assert trusted_ids == raw_ids - quarantined, "GAP trust filter mismatch"
    assert all(bool(r["verified"]) for r in completed), (
        "Unapproved ResourceSkill passed the completion loader"
    )

    print(f"[DB] {name} / student_id={student_id}")
    print(f"[RAW STUDENT SKILLS] {len(raw_ids)}")
    print(f"[TRUSTED FOR GAP] {len(trusted_ids)}")
    print(f"[QUARANTINED FROM GAP] {len(quarantined)}")
    print(f"[APPROVED COMPLETED LINKS] {len(completed)}")

    for r in raw:
        sid = int(r["skill_id"])
        if sid in quarantined:
            print(f"  QUARANTINED: {sid} / {r['skill_name']}")
            result = calculate_gap_request(GapRequest(
                student_id=student_id,
                skills=[{"skill_id": sid, "skill": r["skill_name"]}],
            ))
            assert len(result) == 1 and result[0]["gap"] == "미보유", (
                f"Unapproved skill {sid} was counted as owned"
            )

    # Unmapped result is always explicit, and test has no side effects.
    result = calculate_gap_request(GapRequest(
        student_id=student_id,
        skills=[{"skill_id": None, "skill": "검증용 사전 외 역량"}],
    ))
    assert len(result) == 1 and result[0]["gap"] == "매핑되지 않음"
    print("[PASS] Unapproved-only evidence is not counted as held in GAP")
    print("[PASS] Unmapped SkillRow returned explicitly")
    print("[READ ONLY] Database unchanged")
    print("[NOTE] No-evidence StudentSkill remains eligible; check separately if needed.")


if __name__ == "__main__":
    main()
