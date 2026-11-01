from __future__ import annotations

import argparse
import contextlib
import io
import json
import re

from dataclasses import replace
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from src.db.database import engine

from src.recommendation_engine import (
    GapSkill,
    calculate_skill_gap,
    load_student,
    load_student_skills,
    recommend_resources,
    sync_student_skills,
)


# ============================================================
# 설정
# ============================================================

# 이 값 이상만 실제 추천 행동(Actionable Gap)에 사용
ACTIONABLE_DEMAND_THRESHOLD = Decimal("0.10")

# Priority 표시는 분석용
HIGH_PRIORITY_THRESHOLD = Decimal("0.40")
MEDIUM_PRIORITY_THRESHOLD = Decimal("0.20")


# ============================================================
# 공통
# ============================================================

def decimal_value(
    value: Any,
) -> Decimal:

    if value is None:
        return Decimal("0")

    return Decimal(
        str(value)
    )


def normalize_skill_key(
    value: str,
) -> str:

    value = (
        value
        or ""
    ).lower()

    return re.sub(
        r"[^0-9a-z가-힣+#]+",
        "",
        value,
    )


def priority_of(
    demand_score: Decimal,
) -> str:

    if (
        demand_score
        >= HIGH_PRIORITY_THRESHOLD
    ):

        return "HIGH"


    if (
        demand_score
        >= MEDIUM_PRIORITY_THRESHOLD
    ):

        return "MEDIUM"


    return "LOW"


# ============================================================
# Skill 사전 자체 감사
# ============================================================

def audit_skill_dictionary(
) -> list[dict]:

    sql = text(
        """
        SELECT
            skill_id,
            name,
            normalized_name

        FROM skill

        WHERE
            is_active = TRUE

        ORDER BY
            skill_id
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql
            ).mappings().all()
        )


    grouped: dict[
        str,
        list[dict],
    ] = {}


    for row in rows:

        key = (
            normalize_skill_key(
                row["name"]
            )
        )


        grouped.setdefault(
            key,
            [],
        ).append(
            {
                "skill_id":
                    int(
                        row[
                            "skill_id"
                        ]
                    ),

                "name":
                    row[
                        "name"
                    ],

                "normalized_name":
                    row[
                        "normalized_name"
                    ],
            }
        )


    warnings = []


    for key, items in (
        grouped.items()
    ):

        if (
            not key
            or len(items)
            <= 1
        ):

            continue


        warnings.append(
            {
                "type":
                    "DUPLICATE_SKILL_DISPLAY_NAME",

                "message":
                    (
                        "표시 이름 기준 동일 Skill이 "
                        "여러 ID로 존재합니다."
                    ),

                "skill_key":
                    key,

                "skills":
                    items,
            }
        )


    return warnings


# ============================================================
# StudentSkill / RoleSkill Identity 감사 및 보정
# ============================================================

def normalize_gap_identity(
    student_skills: list[dict],
    gaps: list[
        GapSkill
    ],
) -> tuple[
    list[
        GapSkill
    ],
    list[
        dict
    ],
]:

    owned_by_name: dict[
        str,
        list[dict],
    ] = {}


    for skill in student_skills:

        key = (
            normalize_skill_key(
                skill[
                    "skill_name"
                ]
            )
        )


        owned_by_name.setdefault(
            key,
            [],
        ).append(
            skill
        )


    corrected: list[
        GapSkill
    ] = []

    warnings: list[
        dict
    ] = []


    for gap in gaps:

        key = (
            normalize_skill_key(
                gap.skill_name
            )
        )


        effective_owned = (
            gap.owned
        )


        # ----------------------------------------------------
        # ID는 다르지만 동일 이름의 Skill을
        # 학생이 이미 보유한 경우
        # ----------------------------------------------------

        if (
            not gap.owned

            and key
            in owned_by_name
        ):

            same_name_skills = (
                owned_by_name[
                    key
                ]
            )


            student_ids = [
                int(
                    x[
                        "skill_id"
                    ]
                )

                for x
                in same_name_skills
            ]


            if (
                gap.skill_id
                not in student_ids
            ):

                warnings.append(
                    {
                        "type":
                            "SKILL_IDENTITY_MISMATCH",

                        "message":
                            (
                                f"학생은 '{gap.skill_name}'을 "
                                "보유하고 있으나 RoleSkill의 "
                                "skill_id와 다릅니다."
                            ),

                        "skill_name":
                            gap.skill_name,

                        "role_skill_id":
                            gap.skill_id,

                        "student_skill_ids":
                            student_ids,
                    }
                )


                # 추천 결과가 잘못되는 것을 막기 위한
                # 임시 논리 보정
                effective_owned = True


        corrected.append(
            replace(
                gap,
                owned=effective_owned,
            )
        )


    return (
        corrected,
        warnings,
    )


# ============================================================
# Readiness
# ============================================================

def calculate_readiness(
    gaps: list[
        GapSkill
    ],
    *,
    actionable_only: bool,
) -> Decimal:

    selected = []


    for gap in gaps:

        if (
            actionable_only

            and gap.demand_score
            < ACTIONABLE_DEMAND_THRESHOLD
        ):

            continue


        selected.append(
            gap
        )


    total = sum(
        (
            gap.demand_score

            for gap
            in selected
        ),
        Decimal("0"),
    )


    covered = sum(
        (
            gap.demand_score

            for gap
            in selected

            if gap.owned
        ),
        Decimal("0"),
    )


    if (
        total <= 0
    ):

        return Decimal(
            "0.0000"
        )


    return (
        covered
        / total
    ).quantize(
        Decimal(
            "0.0001"
        )
    )


# ============================================================
# Actionable Gap
# ============================================================

def actionable_gaps(
    gaps: list[
        GapSkill
    ],
) -> list[
    GapSkill
]:

    return [
        gap

        for gap
        in gaps

        if (
            gap.demand_score
            >= ACTIONABLE_DEMAND_THRESHOLD
        )
    ]


# ============================================================
# Recommendation JSON 변환
# ============================================================

def recommendation_to_dict(
    rec,
) -> dict:

    return {
        "resource_id":
            rec.resource_id,

        "resource_type":
            rec.resource_type,

        "name":
            rec.name,

        "organization":
            rec.organization,

        "canonical_url":
            rec.canonical_url,

        "covered_skills":
            list(
                rec.covered_skills
            ),

        "weighted_coverage":
            float(
                rec.weighted_coverage
            ),

        "coverage_ratio":
            float(
                rec.coverage_ratio
            ),

        "recommendation_score":
            float(
                rec.recommendation_score
            ),

        "year_level":
            rec.year_level,

        "semester":
            rec.semester,

        "current_status":
            rec.current_status,
    }


# ============================================================
# 최종 Payload 생성
# ============================================================

def build_recommendation_payload(
    student_id: int,
    *,
    sync_skills: bool,
    top_n: int,
    quiet: bool = False,
) -> dict:

    # --------------------------------------------------------
    # Completed Resource -> StudentSkill
    # --------------------------------------------------------

    if sync_skills:

        if quiet:

            buffer = io.StringIO()

            with contextlib.redirect_stdout(
                buffer
            ):

                sync_student_skills(
                    student_id
                )


        else:

            sync_student_skills(
                student_id
            )


    # --------------------------------------------------------
    # 학생
    # --------------------------------------------------------

    student = (
        load_student(
            student_id
        )
    )


    student_skills = (
        load_student_skills(
            student_id
        )
    )


    # --------------------------------------------------------
    # 기본 Skill Gap
    # --------------------------------------------------------

    (
        raw_gaps,
        _,
    ) = calculate_skill_gap(
        student,
        student_skills,
    )


    # --------------------------------------------------------
    # Skill Identity 검사
    # --------------------------------------------------------

    (
        gaps,
        identity_warnings,
    ) = normalize_gap_identity(
        student_skills,
        raw_gaps,
    )


    dictionary_warnings = (
        audit_skill_dictionary()
    )


    warnings = (
        dictionary_warnings
        + identity_warnings
    )


    # --------------------------------------------------------
    # Readiness
    # --------------------------------------------------------

    overall_readiness = (
        calculate_readiness(
            gaps,
            actionable_only=False,
        )
    )


    actionable_readiness = (
        calculate_readiness(
            gaps,
            actionable_only=True,
        )
    )


    # --------------------------------------------------------
    # 핵심 Gap만 추천에 사용
    # --------------------------------------------------------

    recommendation_gaps = (
        actionable_gaps(
            gaps
        )
    )


    recommendations = (
        recommend_resources(
            student,
            recommendation_gaps,
            top_n=top_n,
        )
    )


    # --------------------------------------------------------
    # Student Skill JSON
    # --------------------------------------------------------

    owned_skills = []


    for skill in student_skills:

        confidence = (
            float(
                skill[
                    "confidence"
                ]
            )

            if skill[
                "confidence"
            ]
            is not None

            else None
        )


        owned_skills.append(
            {
                "skill_id":
                    int(
                        skill[
                            "skill_id"
                        ]
                    ),

                "name":
                    skill[
                        "skill_name"
                    ],

                "category":
                    skill[
                        "category"
                    ],

                "level":
                    skill[
                        "level"
                    ],

                "confidence":
                    confidence,

                "verified":
                    bool(
                        skill[
                            "verified"
                        ]
                    ),

                "evidence_count":
                    int(
                        skill[
                            "evidence_count"
                        ]
                    ),
            }
        )


    # --------------------------------------------------------
    # RoleSkill JSON
    # --------------------------------------------------------

    role_skills = []


    for gap in gaps:

        actionable = (
            gap.demand_score
            >= ACTIONABLE_DEMAND_THRESHOLD
        )


        role_skills.append(
            {
                "skill_id":
                    gap.skill_id,

                "name":
                    gap.skill_name,

                "demand_score":
                    float(
                        gap.demand_score
                    ),

                "required_count":
                    gap.required_count,

                "preferred_count":
                    gap.preferred_count,

                "related_count":
                    gap.related_count,

                "sample_count":
                    gap.sample_count,

                "owned":
                    gap.owned,

                "actionable":
                    actionable,

                "priority":
                    priority_of(
                        gap.demand_score
                    ),
            }
        )


    missing_skills = [
        item

        for item
        in role_skills

        if not item[
            "owned"
        ]
    ]


    actionable_missing_skills = [
        item

        for item
        in missing_skills

        if item[
            "actionable"
        ]
    ]


    # --------------------------------------------------------
    # Payload
    # --------------------------------------------------------

    return {
        "student": {
            "student_id":
                int(
                    student[
                        "student_id"
                    ]
                ),

            "external_user_id":
                student[
                    "external_user_id"
                ],

            "university_id":
                student[
                    "university_id"
                ],

            "university_name":
                student[
                    "university_name"
                ],

            "department_id":
                student[
                    "department_id"
                ],

            "department_name":
                student[
                    "department_name"
                ],

            "school_year":
                student[
                    "school_year"
                ],

            "target_job_role_id":
                student[
                    "target_job_role_id"
                ],

            "target_role_name":
                student[
                    "target_role_name"
                ],
        },


        "readiness": {
            # 모든 RoleSkill 포함
            "overall":
                float(
                    overall_readiness
                ),

            # demand >= 0.10만 포함
            "actionable":
                float(
                    actionable_readiness
                ),

            "actionable_threshold":
                float(
                    ACTIONABLE_DEMAND_THRESHOLD
                ),
        },


        "owned_skills":
            owned_skills,


        "role_skills":
            role_skills,


        "missing_skills":
            missing_skills,


        "actionable_missing_skills":
            actionable_missing_skills,


        "recommendations": [
            recommendation_to_dict(
                rec
            )

            for rec
            in recommendations
        ],


        "warnings":
            warnings,


        "metadata": {
            "role_skill_count":
                len(
                    role_skills
                ),

            "owned_skill_count":
                len(
                    owned_skills
                ),

            "missing_skill_count":
                len(
                    missing_skills
                ),

            "actionable_missing_skill_count":
                len(
                    actionable_missing_skills
                ),

            "recommendation_count":
                len(
                    recommendations
                ),
        },
    }


# ============================================================
# 사람이 보는 출력
# ============================================================

def print_payload(
    payload: dict,
) -> None:

    student = (
        payload[
            "student"
        ]
    )


    readiness = (
        payload[
            "readiness"
        ]
    )


    print(
        "=" * 90
    )

    print(
        "CareerQuest Recommendation Service"
    )

    print(
        "=" * 90
    )


    print()


    print(
        (
            f"학생      : "
            f"{student['student_id']}"
        )
    )


    print(
        (
            f"학교/학과 : "
            f"{student['university_name']} / "
            f"{student['department_name']}"
        )
    )


    print(
        (
            f"목표 직무 : "
            f"{student['target_role_name']}"
        )
    )


    print(
        (
            "전체 Readiness     : "
            f"{readiness['overall'] * 100:.1f}%"
        )
    )


    print(
        (
            "핵심 Skill Readiness: "
            f"{readiness['actionable'] * 100:.1f}%"
        )
    )


    # --------------------------------------------------------
    # 경고
    # --------------------------------------------------------

    warnings = (
        payload[
            "warnings"
        ]
    )


    if warnings:

        print(
            "\n"
            + "=" * 90
        )

        print(
            "DATA QUALITY WARNING"
        )

        print(
            "=" * 90
        )


        for warning in warnings:

            print(
                (
                    f"[{warning['type']}] "
                    f"{warning['message']}"
                )
            )


            if (
                warning[
                    "type"
                ]
                == "SKILL_IDENTITY_MISMATCH"
            ):

                print(
                    (
                        f"  skill       : "
                        f"{warning['skill_name']}"
                    )
                )

                print(
                    (
                        f"  role ID     : "
                        f"{warning['role_skill_id']}"
                    )
                )

                print(
                    (
                        f"  student IDs : "
                        f"{warning['student_skill_ids']}"
                    )
                )


    # --------------------------------------------------------
    # 핵심 부족 Skill
    # --------------------------------------------------------

    print(
        "\n"
        + "=" * 90
    )

    print(
        "ACTIONABLE SKILL GAP"
    )

    print(
        "=" * 90
    )


    for skill in (
        payload[
            "actionable_missing_skills"
        ]
    ):

        print(
            (
                f"[{skill['priority']:<6}] "
                f"{skill['name']:<28} "
                f"demand="
                f"{skill['demand_score']:.5f} "
                f"| required="
                f"{skill['required_count']} "
                f"| preferred="
                f"{skill['preferred_count']} "
                f"| sample="
                f"{skill['sample_count']}"
            )
        )


    # --------------------------------------------------------
    # 추천
    # --------------------------------------------------------

    print(
        "\n"
        + "=" * 90
    )

    print(
        "RECOMMENDATIONS"
    )

    print(
        "=" * 90
    )


    recommendations = (
        payload[
            "recommendations"
        ]
    )


    if not recommendations:

        print(
            "현재 Gap을 직접 채우는 Resource 없음"
        )


    for index, rec in enumerate(
        recommendations,
        start=1,
    ):

        print()


        print(
            (
                f"{index:02d}. "
                f"[{rec['resource_type']}] "
                f"{rec['name']}"
            )
        )


        print(
            (
                f"    score  : "
                f"{rec['recommendation_score']:.4f}"
            )
        )


        print(
            (
                "    skills : "
                + ", ".join(
                    rec[
                        "covered_skills"
                    ]
                )
            )
        )


        if (
            rec[
                "resource_type"
            ]
            == "COURSE"
        ):

            print(
                (
                    f"    course : "
                    f"{rec['year_level'] or '-'}학년 "
                    f"| "
                    f"{rec['semester'] or '-'}"
                )
            )


    print(
        "\n"
        + "=" * 90
    )

    print(
        "INTEGRATION PAYLOAD READY"
    )

    print(
        "=" * 90
    )


# ============================================================
# CLI
# ============================================================

def parse_args(
) -> argparse.Namespace:

    parser = (
        argparse.ArgumentParser()
    )


    parser.add_argument(
        "--student-id",

        type=int,

        required=True,
    )


    parser.add_argument(
        "--sync-skills",

        action="store_true",
    )


    parser.add_argument(
        "--top",

        type=int,

        default=10,
    )


    parser.add_argument(
        "--json",

        action="store_true",

        help=(
            "팀원/프론트 연동용 JSON 출력"
        ),
    )


    return parser.parse_args()


# ============================================================
# main
# ============================================================

def main(
) -> None:

    args = (
        parse_args()
    )


    payload = (
        build_recommendation_payload(
            args.student_id,

            sync_skills=(
                args.sync_skills
            ),

            top_n=(
                args.top
            ),

            quiet=(
                args.json
            ),
        )
    )


    if args.json:

        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )
        )

        return


    print_payload(
        payload
    )


if __name__ == "__main__":

    main()