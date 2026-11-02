from __future__ import annotations

import argparse
import json
from decimal import Decimal

from sqlalchemy import text

from src.db.database import engine


RESOURCE_SKILL_RELATIONS = {
    "COURSE": {"TAUGHT"},
    "CERTIFICATE": {"VERIFIED"},
    "PROGRAM": {"TAUGHT"},
}


UPSERT_STUDENT_SKILL_SQL = text(
    """
    INSERT INTO student_skill (
        student_id,
        skill_id,
        confidence,
        verified,
        acquired_at
    )
    VALUES (
        :student_id,
        :skill_id,
        :confidence,
        :verified,
        :acquired_at
    )
    ON CONFLICT (student_id, skill_id)
    DO UPDATE SET
        confidence = CASE
            WHEN student_skill.confidence IS NULL THEN EXCLUDED.confidence
            WHEN EXCLUDED.confidence IS NULL THEN student_skill.confidence
            ELSE GREATEST(student_skill.confidence, EXCLUDED.confidence)
        END,
        verified = student_skill.verified OR EXCLUDED.verified,
        acquired_at = COALESCE(
            student_skill.acquired_at,
            EXCLUDED.acquired_at
        ),
        updated_at = NOW()
    RETURNING student_skill_id
    """
)


def load_completed_resource_skills(
    student_id: int,
) -> list[dict]:

    sql = text(
        """
        SELECT
            sr.resource_id,
            sr.completed_at,

            r.resource_type,
            r.name AS resource_name,

            rs.skill_id,
            s.name AS skill_name,

            rs.relation_type,
            rs.confidence,
            rs.verified

        FROM student_resource sr

        JOIN resource r
            ON r.resource_id = sr.resource_id

        JOIN resource_skill rs
            ON rs.resource_id = r.resource_id

        JOIN skill s
            ON s.skill_id = rs.skill_id

        WHERE
            sr.student_id = :student_id

            AND sr.status = 'COMPLETED'

            AND r.resource_type IN (
                'COURSE',
                'CERTIFICATE',
                'PROGRAM'
            )

            AND s.is_active = TRUE

        ORDER BY
            r.resource_id,
            rs.skill_id
        """
    )

    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql,
                {
                    "student_id":
                        student_id,
                },
            )
            .mappings()
            .all()
        )

    result: list[dict] = []

    for row in rows:

        allowed = (
            RESOURCE_SKILL_RELATIONS.get(
                row[
                    "resource_type"
                ],
                set(),
            )
        )

        if (
            row[
                "relation_type"
            ]
            not in allowed
        ):
            continue

        result.append(
            dict(
                row
            )
        )

    return result


def sync_student_skills(
    student_id: int,
) -> int:

    rows = (
        load_completed_resource_skills(
            student_id
        )
    )

    created_evidence = 0

    with engine.begin() as conn:

        for row in rows:

            confidence = (
                row[
                    "confidence"
                ]
            )

            if confidence is None:

                confidence = (
                    Decimal(
                        "0.80"
                    )
                )

            verified = bool(
                row[
                    "verified"
                ]
            )

            if (
                row[
                    "resource_type"
                ]
                == "CERTIFICATE"

                and row[
                    "relation_type"
                ]
                == "VERIFIED"
            ):

                verified = True

            student_skill_id = int(
                conn.execute(
                    UPSERT_STUDENT_SKILL_SQL,
                    {
                        "student_id":
                            student_id,

                        "skill_id":
                            int(
                                row[
                                    "skill_id"
                                ]
                            ),

                        "confidence":
                            confidence,

                        "verified":
                            verified,

                        "acquired_at":
                            row[
                                "completed_at"
                            ],
                    },
                ).scalar_one()
            )

            evidence_type = (
                row[
                    "resource_type"
                ]
            )

            exists = (
                conn.execute(
                    text(
                        """
                        SELECT
                            evidence_id

                        FROM student_skill_evidence

                        WHERE
                            student_skill_id =
                                :student_skill_id

                            AND evidence_type =
                                :evidence_type

                            AND resource_id =
                                :resource_id

                        LIMIT 1
                        """
                    ),
                    {
                        "student_skill_id":
                            student_skill_id,

                        "evidence_type":
                            evidence_type,

                        "resource_id":
                            int(
                                row[
                                    "resource_id"
                                ]
                            ),
                    },
                ).scalar()
            )

            if exists is not None:
                continue

            conn.execute(
                text(
                    """
                    INSERT INTO student_skill_evidence (
                        student_skill_id,
                        evidence_type,
                        resource_id,
                        description,
                        weight
                    )
                    VALUES (
                        :student_skill_id,
                        :evidence_type,
                        :resource_id,
                        :description,
                        :weight
                    )
                    """
                ),
                {
                    "student_skill_id":
                        student_skill_id,

                    "evidence_type":
                        evidence_type,

                    "resource_id":
                        int(
                            row[
                                "resource_id"
                            ]
                        ),

                    "description":
                        (
                            f"{row['resource_type']} 완료: "
                            f"{row['resource_name']} "
                            f"-> {row['skill_name']}"
                        ),

                    "weight":
                        confidence,
                },
            )

            created_evidence += 1

    print(
        (
            "StudentSkill 동기화 대상: "
            f"{len(rows)}개 근거"
        )
    )

    print(
        (
            "신규 SkillEvidence: "
            f"{created_evidence}개"
        )
    )

    return len(
        rows
    )


def load_student_skills(
    student_id: int,
) -> list[dict]:

    sql = text(
        """
        SELECT
            ss.student_skill_id,

            ss.skill_id,
            s.name AS skill_name,
            s.category,

            ss.level,
            ss.confidence,
            ss.verified,

            COUNT(
                sse.evidence_id
            ) AS evidence_count

        FROM student_skill ss

        JOIN skill s
            ON s.skill_id =
               ss.skill_id

        LEFT JOIN student_skill_evidence sse
            ON sse.student_skill_id =
               ss.student_skill_id

        WHERE
            ss.student_id =
                :student_id

            AND s.is_active =
                TRUE

        GROUP BY
            ss.student_skill_id,
            ss.skill_id,
            s.name,
            s.category,
            ss.level,
            ss.confidence,
            ss.verified

        ORDER BY
            s.category,
            s.name
        """
    )

    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql,
                {
                    "student_id":
                        student_id,
                },
            )
            .mappings()
            .all()
        )

    return [
        dict(
            row
        )

        for row in rows
    ]


def parse_args() -> argparse.Namespace:

    parser = (
        argparse.ArgumentParser(
            description=(
                "완료 Resource를 StudentSkill로 "
                "동기화하고 현재 Skill을 조회합니다."
            )
        )
    )

    parser.add_argument(
        "--student-id",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--sync",
        action="store_true",
    )

    parser.add_argument(
        "--json",
        action="store_true",
    )

    return parser.parse_args()


def main() -> None:

    args = (
        parse_args()
    )

    if args.sync:

        sync_student_skills(
            args.student_id
        )

    skills = (
        load_student_skills(
            args.student_id
        )
    )

    if args.json:

        print(
            json.dumps(
                skills,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )

        return

    print(
        (
            f"student_id={args.student_id} "
            f"| skills={len(skills)}"
        )
    )

    for skill in skills:

        print(
            (
                f"- {skill['skill_id']} "
                f"{skill['skill_name']} "
                f"| level={skill['level']} "
                f"| evidence="
                f"{skill['evidence_count']}"
            )
        )


if __name__ == "__main__":
    main()