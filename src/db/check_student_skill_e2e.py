
from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import text

from src.db.database import engine
from src.student_skill_service import (
    load_completed_resource_skills,
    sync_student_skills,
)


def inspect(student_id: int):

    rows = load_completed_resource_skills(
        student_id
    )

    expected = {
        (
            int(row["skill_id"]),
            int(row["resource_id"]),
            str(row["resource_type"]),
        )
        for row in rows
    }

    with engine.connect() as conn:

        student_exists = conn.execute(
            text(
                "SELECT 1 FROM student "
                "WHERE student_id = :id"
            ),
            {"id": student_id},
        ).scalar()

        if student_exists is None:
            raise RuntimeError(
                f"학생이 없습니다: {student_id}"
            )

        completed = conn.execute(
            text("""
                SELECT
                    r.resource_type,
                    COUNT(*) AS total
                FROM student_resource sr
                JOIN resource r
                    ON r.resource_id = sr.resource_id
                WHERE sr.student_id = :id
                  AND sr.status = 'COMPLETED'
                  AND r.resource_type IN (
                      'COURSE',
                      'CERTIFICATE',
                      'PROGRAM'
                  )
                GROUP BY r.resource_type
                ORDER BY r.resource_type
            """),
            {"id": student_id},
        ).all()

        skills = conn.execute(
            text("""
                SELECT skill_id, verified
                FROM student_skill
                WHERE student_id = :id
            """),
            {"id": student_id},
        ).all()

        evidence = conn.execute(
            text("""
                SELECT
                    ss.skill_id,
                    se.resource_id,
                    se.evidence_type
                FROM student_skill ss
                JOIN student_skill_evidence se
                    ON se.student_skill_id =
                       ss.student_skill_id
                WHERE ss.student_id = :id
                  AND se.resource_id IS NOT NULL
            """),
            {"id": student_id},
        ).all()

    mapped_by_type = Counter(
        resource_type
        for _, _, resource_type in expected
    )

    actual_edges = Counter(
        (
            int(skill_id),
            int(resource_id),
            str(evidence_type),
        )
        for skill_id, resource_id, evidence_type
        in evidence
    )

    skill_map = {
        int(skill_id): bool(verified)
        for skill_id, verified in skills
    }

    missing_edges = (
        expected - set(actual_edges)
    )

    missing_skills = {
        skill_id
        for skill_id, _, _ in expected
    } - set(skill_map)

    unverified_certs = {
        skill_id
        for skill_id, _, resource_type in expected
        if resource_type == "CERTIFICATE"
        and not skill_map.get(skill_id, False)
    }

    duplicated = {
        key: count
        for key, count in actual_edges.items()
        if count > 1
    }

    print(
        "\n[완료 Resource]",
        {
            str(name): count
            for name, count in completed
        },
    )

    print(
        "[Skill 연결이 있는 완료 근거]",
        dict(mapped_by_type),
    )

    print(
        f"[현재 StudentSkill] "
        f"{len(skills)}개"
    )

    print(
        f"[현재 Resource Evidence] "
        f"{sum(actual_edges.values())}개"
    )

    print(
        f"[누락 Skill] {len(missing_skills)}개 "
        f"/ [누락 Evidence] "
        f"{len(missing_edges)}개"
    )

    print(
        f"[자격증 검증 플래그 누락] "
        f"{len(unverified_certs)}개"
    )

    print(
        f"[중복 Resource Evidence] "
        f"{len(duplicated)}종류"
    )

    for edge in sorted(missing_edges)[:8]:
        print("  누락:", edge)

    for edge, count in list(
        duplicated.items()
    )[:8]:
        print("  중복:", edge, "x", count)

    errors = (
        len(missing_skills)
        + len(missing_edges)
        + len(unverified_certs)
        + len(duplicated)
    )

    return expected, actual_edges, errors


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--student-id",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args = parser.parse_args()

    if args.student_id <= 0:
        parser.error(
            "student-id는 양수여야 합니다."
        )

    mode = (
        "APPLY"
        if args.apply
        else "READ-ONLY"
    )

    print(
        f"[{mode}] "
        f"student_id={args.student_id}"
    )

    expected, _, errors = inspect(
        args.student_id
    )

    if not expected:

        print(
            "\n[보류] 완료한 Resource 중 "
            "Skill 연결이 있는 자료가 없습니다."
        )

        print(
            "StudentSkill E2E 검증에는 "
            "완료 Resource가 필요합니다."
        )

        return

    if not args.apply:

        print(
            "\n[DRY RUN] DB 변경 없음"
        )

        if errors == 0:
            print(
                "[이미 일치] 필요한 Skill과 "
                "Evidence가 존재합니다."
            )

        else:
            print(
                "[동기화 필요] --apply로 "
                "기존 동기화 함수를 실행할 수 있습니다."
            )

        return

    print("\n[동기화 1회]")

    sync_student_skills(
        args.student_id
    )

    expected, first_edges, first_errors = (
        inspect(args.student_id)
    )

    if first_errors:

        raise SystemExit(
            "[FAIL] 동기화 후 "
            "누락 또는 중복이 남았습니다."
        )

    print(
        "\n[동기화 2회: 중복 방지 확인]"
    )

    sync_student_skills(
        args.student_id
    )

    _, second_edges, second_errors = (
        inspect(args.student_id)
    )

    if (
        second_errors
        or first_edges != second_edges
    ):

        raise SystemExit(
            "[FAIL] 재동기화 후 "
            "Evidence가 중복되거나 "
            "상태가 변했습니다."
        )

    print(
        "\n[PASS] 완료 Resource "
        "→ ResourceSkill "
        "→ StudentSkill "
        "→ StudentSkillEvidence"
    )

    print(
        "[PASS] 두 번째 동기화에서도 "
        "Evidence 중복 생성 없음"
    )

    for resource_type in (
        "COURSE",
        "CERTIFICATE",
        "PROGRAM",
    ):

        if not any(
            item_type == resource_type
            for _, _, item_type in expected
        ):

            print(
                f"[미검증] {resource_type}: "
                "완료·매핑 근거가 없음"
            )


if __name__ == "__main__":
    main()
