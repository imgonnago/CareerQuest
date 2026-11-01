from __future__ import annotations

import argparse

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from src.db.database import engine


# ============================================================
# 설정
# ============================================================

MIN_ROLE_DEMAND = Decimal("0.00001")

# StudentResource -> StudentSkill 변환 시 허용하는 관계
RESOURCE_SKILL_RELATIONS = {
    "COURSE": {
        "TAUGHT",
    },

    "CERTIFICATE": {
        "VERIFIED",
    },

    "PROGRAM": {
        "TAUGHT",
    },
}


# ============================================================
# 데이터 구조
# ============================================================

@dataclass
class GapSkill:
    skill_id: int
    skill_name: str

    demand_score: Decimal

    required_count: int
    preferred_count: int
    related_count: int

    sample_count: int

    owned: bool


@dataclass
class Recommendation:
    resource_id: int
    resource_type: str

    name: str
    organization: str | None
    canonical_url: str | None

    covered_skills: list[str]

    weighted_coverage: Decimal
    coverage_ratio: Decimal

    recommendation_score: Decimal

    year_level: int | None
    semester: str | None

    current_status: str | None


# ============================================================
# 공통
# ============================================================

def decimal_value(
    value: Any,
) -> Decimal:

    if value is None:
        return Decimal("0")

    return Decimal(
        str(
            value
        )
    )


# ============================================================
# STUDENT 조회
# ============================================================

def load_student(
    student_id: int,
) -> dict:

    sql = text(
        """
        SELECT
            st.student_id,
            st.external_user_id,

            st.university_id,
            u.name AS university_name,

            st.department_id,
            d.name AS department_name,

            st.school_year,
            st.gpa,
            st.gpa_scale,

            st.target_job_role_id,
            jr.name AS target_role_name,

            st.interest_text

        FROM student st

        LEFT JOIN university u
            ON u.university_id =
               st.university_id

        LEFT JOIN department d
            ON d.department_id =
               st.department_id

        LEFT JOIN job_role jr
            ON jr.job_role_id =
               st.target_job_role_id

        WHERE
            st.student_id =
                :student_id
        """
    )


    with engine.connect() as conn:

        row = (
            conn.execute(
                sql,

                {
                    "student_id":
                        student_id,
                },
            ).mappings().first()
        )


    if row is None:

        raise RuntimeError(
            f"student_id={student_id} 학생이 없습니다."
        )


    return dict(
        row
    )


# ============================================================
# 학생 목록
# ============================================================

def show_students(
) -> None:

    sql = text(
        """
        SELECT
            st.student_id,
            st.external_user_id,

            d.name AS department_name,

            st.school_year,

            jr.name AS target_role_name

        FROM student st

        LEFT JOIN department d
            ON d.department_id =
               st.department_id

        LEFT JOIN job_role jr
            ON jr.job_role_id =
               st.target_job_role_id

        ORDER BY
            st.student_id
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql
            ).mappings().all()
        )


    print(
        "=" * 80
    )

    print(
        "현재 STUDENT"
    )

    print(
        "=" * 80
    )


    if not rows:

        print(
            "등록된 학생 없음"
        )

        return


    for row in rows:

        print(
            (
                f"[{row['student_id']}] "
                f"{row['external_user_id'] or '-'} "
                f"| {row['department_name'] or '-'} "
                f"| {row['school_year'] or '-'}학년 "
                f"| 목표={row['target_role_name'] or '-'}"
            )
        )


# ============================================================
# DEMO STUDENT 생성
#
# 실제 학생 데이터가 아직 없을 때
# 전체 추천 파이프라인 검증용.
# ============================================================

def seed_demo_student(
) -> int:

    EXTERNAL_USER_ID = (
        "careerquest-demo-ai"
    )

    TARGET_ROLE = (
        "Machine Learning Engineer"
    )


    # 일부만 이수시켜서 Skill Gap이 실제로 나오도록 함
    COMPLETED_COURSE_CODES = (
        "00052M",   # 파이썬 프로그래밍
        "61952A",   # 기계학습
        "61911A",   # AI를 위한 기초수학 및 통계
        "61913A",   # 데이터베이스프로그래밍
    )


    with engine.begin() as conn:

        # ----------------------------------------------------
        # 건양대학교 인공지능학과
        # ----------------------------------------------------

        department = (
            conn.execute(
                text(
                    """
                    SELECT
                        d.department_id,
                        d.university_id

                    FROM department d

                    JOIN university u
                        ON u.university_id =
                           d.university_id

                    WHERE
                        u.name =
                            '건양대학교'

                      AND d.name =
                            '인공지능학과'

                      AND d.is_active =
                            TRUE

                    ORDER BY
                        d.survey_year DESC
                            NULLS LAST,
                        d.department_id DESC

                    LIMIT 1
                    """
                )
            ).mappings().first()
        )


        if department is None:

            raise RuntimeError(
                "건양대학교 인공지능학과가 DB에 없습니다."
            )


        # ----------------------------------------------------
        # 목표 JobRole
        # ----------------------------------------------------

        role_id = (
            conn.execute(
                text(
                    """
                    SELECT
                        job_role_id

                    FROM job_role

                    WHERE
                        name =
                            :name

                      AND is_active =
                            TRUE

                    LIMIT 1
                    """
                ),

                {
                    "name":
                        TARGET_ROLE,
                },
            ).scalar()
        )


        if role_id is None:

            raise RuntimeError(
                f"JobRole '{TARGET_ROLE}'이 없습니다."
            )


        # ----------------------------------------------------
        # STUDENT
        # ----------------------------------------------------

        student_id = (
            conn.execute(
                text(
                    """
                    SELECT
                        student_id

                    FROM student

                    WHERE
                        external_user_id =
                            :external_user_id
                    """
                ),

                {
                    "external_user_id":
                        EXTERNAL_USER_ID,
                },
            ).scalar()
        )


        if student_id is None:

            student_id = (
                conn.execute(
                    text(
                        """
                        INSERT INTO student (
                            external_user_id,

                            university_id,
                            department_id,

                            school_year,

                            target_job_role_id,

                            interest_text
                        )

                        VALUES (
                            :external_user_id,

                            :university_id,
                            :department_id,

                            2,

                            :target_job_role_id,

                            'AI / 머신러닝 분야 취업 희망'
                        )

                        RETURNING
                            student_id
                        """
                    ),

                    {
                        "external_user_id":
                            EXTERNAL_USER_ID,

                        "university_id":
                            int(
                                department[
                                    "university_id"
                                ]
                            ),

                        "department_id":
                            int(
                                department[
                                    "department_id"
                                ]
                            ),

                        "target_job_role_id":
                            int(
                                role_id
                            ),
                    },
                ).scalar_one()
            )


        else:

            conn.execute(
                text(
                    """
                    UPDATE student

                    SET
                        university_id =
                            :university_id,

                        department_id =
                            :department_id,

                        school_year =
                            2,

                        target_job_role_id =
                            :target_job_role_id,

                        updated_at =
                            NOW()

                    WHERE
                        student_id =
                            :student_id
                    """
                ),

                {
                    "university_id":
                        int(
                            department[
                                "university_id"
                            ]
                        ),

                    "department_id":
                        int(
                            department[
                                "department_id"
                            ]
                        ),

                    "target_job_role_id":
                        int(
                            role_id
                        ),

                    "student_id":
                        int(
                            student_id
                        ),
                },
            )


        # ----------------------------------------------------
        # DEMO 이수 과목
        # ----------------------------------------------------

        courses = (
            conn.execute(
                text(
                    """
                    SELECT
                        r.resource_id,
                        r.name,

                        cd.course_code

                    FROM course_detail cd

                    JOIN resource r
                        ON r.resource_id =
                           cd.resource_id

                    WHERE
                        cd.department_id =
                            :department_id

                      AND cd.curriculum_year =
                            2026

                      AND cd.course_code =
                            ANY(:course_codes)

                      AND r.resource_type =
                            'COURSE'

                    ORDER BY
                        cd.course_code
                    """
                ),

                {
                    "department_id":
                        int(
                            department[
                                "department_id"
                            ]
                        ),

                    "course_codes":
                        list(
                            COMPLETED_COURSE_CODES
                        ),
                },
            ).mappings().all()
        )


        for course in courses:

            conn.execute(
                text(
                    """
                    INSERT INTO student_resource (
                        student_id,
                        resource_id,

                        status,
                        completed_at,

                        evidence_text
                    )

                    VALUES (
                        :student_id,
                        :resource_id,

                        'COMPLETED',
                        NOW(),

                        'CareerQuest DEMO 이수 과목'
                    )

                    ON CONFLICT (
                        student_id,
                        resource_id
                    )

                    DO UPDATE SET

                        status =
                            'COMPLETED',

                        completed_at =
                            COALESCE(
                                student_resource.completed_at,
                                NOW()
                            ),

                        updated_at =
                            NOW()
                    """
                ),

                {
                    "student_id":
                        int(
                            student_id
                        ),

                    "resource_id":
                        int(
                            course[
                                "resource_id"
                            ]
                        ),
                },
            )


    print(
        "=" * 80
    )

    print(
        "DEMO STUDENT 생성/갱신"
    )

    print(
        "=" * 80
    )

    print(
        f"student_id : {student_id}"
    )

    print(
        f"목표 직무  : {TARGET_ROLE}"
    )

    print(
        "이수 과목:"
    )


    for course in courses:

        print(
            (
                f"  {course['course_code']} "
                f"| {course['name']}"
            )
        )


    return int(
        student_id
    )


# ============================================================
# 완료 RESOURCE -> STUDENT_SKILL
# ============================================================

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
            ON r.resource_id =
               sr.resource_id

        JOIN resource_skill rs
            ON rs.resource_id =
               r.resource_id

        JOIN skill s
            ON s.skill_id =
               rs.skill_id

        WHERE
            sr.student_id =
                :student_id

          AND sr.status =
                'COMPLETED'

          AND r.resource_type IN (
                'COURSE',
                'CERTIFICATE',
                'PROGRAM'
          )

          AND s.is_active =
                TRUE

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
            ).mappings().all()
        )


    result = []


    for row in rows:

        resource_type = (
            row[
                "resource_type"
            ]
        )


        allowed_relations = (
            RESOURCE_SKILL_RELATIONS.get(
                resource_type,
                set(),
            )
        )


        if (
            row[
                "relation_type"
            ]
            not in allowed_relations
        ):

            continue


        result.append(
            dict(
                row
            )
        )


    return result


# ============================================================
# STUDENT_SKILL UPSERT
# ============================================================

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

    ON CONFLICT (
        student_id,
        skill_id
    )

    DO UPDATE SET

        confidence =
            CASE

                WHEN student_skill.confidence
                     IS NULL
                THEN EXCLUDED.confidence

                WHEN EXCLUDED.confidence
                     IS NULL
                THEN student_skill.confidence

                ELSE GREATEST(
                    student_skill.confidence,
                    EXCLUDED.confidence
                )

            END,

        verified =
            (
                student_skill.verified
                OR EXCLUDED.verified
            ),

        acquired_at =
            COALESCE(
                student_skill.acquired_at,
                EXCLUDED.acquired_at
            ),

        updated_at =
            NOW()

    RETURNING
        student_skill_id
    """
)


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
                    Decimal("0.80")
                )


            verified = bool(
                row[
                    "verified"
                ]
            )


            # CERTIFICATE의 VERIFIED 관계는
            # 학생이 실제 취득 완료했다면
            # 강한 Skill 근거로 봄.
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
                            f"→ {row['skill_name']}"
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


# ============================================================
# 현재 학생 Skill
# ============================================================

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
            ).mappings().all()
        )


    return [
        dict(
            row
        )

        for row
        in rows
    ]


# ============================================================
# 목표 직무 Skill Gap
# ============================================================

def calculate_skill_gap(
    student: dict,
    student_skills: list[dict],
) -> tuple[
    list[GapSkill],
    Decimal,
]:

    target_role_id = (
        student[
            "target_job_role_id"
        ]
    )


    if target_role_id is None:

        raise RuntimeError(
            "학생의 target_job_role_id가 설정되지 않았습니다."
        )


    sql = text(
        """
        SELECT
            rs.skill_id,
            s.name AS skill_name,

            rs.required_count,
            rs.preferred_count,
            rs.related_count,

            rs.total_posting_count,

            rs.demand_score

        FROM role_skill rs

        JOIN skill s
            ON s.skill_id =
               rs.skill_id

        WHERE
            rs.job_role_id =
                :job_role_id

          AND s.is_active =
                TRUE

          AND COALESCE(
                rs.demand_score,
                0
              ) >= :min_demand

        ORDER BY
            rs.demand_score DESC,
            rs.required_count DESC,
            s.name
        """
    )


    with engine.connect() as conn:

        role_skills = (
            conn.execute(
                sql,

                {
                    "job_role_id":
                        int(
                            target_role_id
                        ),

                    "min_demand":
                        MIN_ROLE_DEMAND,
                },
            ).mappings().all()
        )


    if not role_skills:

        raise RuntimeError(
            (
                "목표 JobRole에 RoleSkill 데이터가 없습니다: "
                f"{student['target_role_name']}"
            )
        )


    owned_skill_ids = {
        int(
            row[
                "skill_id"
            ]
        )

        for row
        in student_skills
    }


    gaps: list[
        GapSkill
    ] = []


    total_demand = (
        Decimal("0")
    )


    covered_demand = (
        Decimal("0")
    )


    for row in role_skills:

        demand = (
            decimal_value(
                row[
                    "demand_score"
                ]
            )
        )


        owned = (
            int(
                row[
                    "skill_id"
                ]
            )
            in owned_skill_ids
        )


        total_demand += (
            demand
        )


        if owned:

            covered_demand += (
                demand
            )


        gaps.append(
            GapSkill(
                skill_id=int(
                    row[
                        "skill_id"
                    ]
                ),

                skill_name=row[
                    "skill_name"
                ],

                demand_score=(
                    demand
                ),

                required_count=int(
                    row[
                        "required_count"
                    ]
                ),

                preferred_count=int(
                    row[
                        "preferred_count"
                    ]
                ),

                related_count=int(
                    row[
                        "related_count"
                    ]
                ),

                sample_count=int(
                    row[
                        "total_posting_count"
                    ]
                ),

                owned=(
                    owned
                ),
            )
        )


    if (
        total_demand
        > 0
    ):

        readiness = (
            covered_demand
            / total_demand
        )


    else:

        readiness = (
            Decimal("0")
        )


    readiness = (
        readiness.quantize(
            Decimal(
                "0.0001"
            )
        )
    )


    return (
        gaps,
        readiness,
    )


# ============================================================
# 추천 Resource
# ============================================================

def recommend_resources(
    student: dict,
    gaps: list[
        GapSkill
    ],
    *,
    top_n: int,
) -> list[
    Recommendation
]:

    missing = [
        gap

        for gap
        in gaps

        if not gap.owned
    ]


    if not missing:

        return []


    missing_ids = [
        gap.skill_id

        for gap
        in missing
    ]


    demand_map = {
        gap.skill_id:
            gap.demand_score

        for gap
        in missing
    }


    total_missing_demand = sum(
        (
            gap.demand_score

            for gap
            in missing
        ),
        Decimal("0"),
    )


    sql = text(
        """
        SELECT
            r.resource_id,

            r.resource_type,
            r.name,
            r.organization,
            r.canonical_url,

            rs.skill_id,
            s.name AS skill_name,

            rs.relation_type,

            cd.department_id,
            cd.year_level,
            cd.semester,

            sr.status AS student_resource_status

        FROM resource r

        JOIN resource_skill rs
            ON rs.resource_id =
               r.resource_id

        JOIN skill s
            ON s.skill_id =
               rs.skill_id

        LEFT JOIN course_detail cd
            ON cd.resource_id =
               r.resource_id

        LEFT JOIN student_resource sr
            ON sr.resource_id =
               r.resource_id

           AND sr.student_id =
               :student_id

        WHERE
            r.resource_type IN (
                'COURSE',
                'CERTIFICATE',
                'PROGRAM'
            )

          AND r.is_active =
                TRUE

          AND (
                r.expires_at
                    IS NULL

                OR

                r.expires_at >=
                    NOW()
          )

          AND rs.skill_id =
                ANY(:skill_ids)

          AND (
                (
                    r.resource_type =
                        'COURSE'

                    AND rs.relation_type =
                        'TAUGHT'
                )

                OR

                (
                    r.resource_type =
                        'CERTIFICATE'

                    AND rs.relation_type =
                        'VERIFIED'
                )

                OR

                (
                    r.resource_type =
                        'PROGRAM'

                    AND rs.relation_type =
                        'TAUGHT'
                )
          )

          AND (
                r.resource_type
                    <> 'COURSE'

                OR

                cd.department_id =
                    :department_id
          )

          AND NOT EXISTS (
                SELECT
                    1

                FROM student_resource completed

                WHERE
                    completed.student_id =
                        :student_id

                  AND completed.resource_id =
                        r.resource_id

                  AND completed.status =
                        'COMPLETED'
          )

        ORDER BY
            r.resource_type,
            r.resource_id
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql,

                {
                    "student_id":
                        int(
                            student[
                                "student_id"
                            ]
                        ),

                    "department_id":
                        student[
                            "department_id"
                        ],

                    "skill_ids":
                        missing_ids,
                },
            ).mappings().all()
        )


    grouped: dict[
        int,
        dict,
    ] = {}


    for row in rows:

        resource_id = int(
            row[
                "resource_id"
            ]
        )


        if resource_id not in grouped:

            grouped[
                resource_id
            ] = {
                "resource_id":
                    resource_id,

                "resource_type":
                    row[
                        "resource_type"
                    ],

                "name":
                    row[
                        "name"
                    ],

                "organization":
                    row[
                        "organization"
                    ],

                "canonical_url":
                    row[
                        "canonical_url"
                    ],

                "year_level":
                    (
                        int(
                            row[
                                "year_level"
                            ]
                        )

                        if row[
                            "year_level"
                        ]
                        is not None

                        else None
                    ),

                "semester":
                    row[
                        "semester"
                    ],

                "current_status":
                    row[
                        "student_resource_status"
                    ],

                "skills":
                    {},
            }


        skill_id = int(
            row[
                "skill_id"
            ]
        )


        grouped[
            resource_id
        ][
            "skills"
        ][
            skill_id
        ] = row[
            "skill_name"
        ]


    recommendations: list[
        Recommendation
    ] = []


    missing_count = len(
        missing
    )


    for resource in (
        grouped.values()
    ):

        covered_skill_ids = list(
            resource[
                "skills"
            ].keys()
        )


        weighted_coverage = sum(
            (
                demand_map.get(
                    skill_id,
                    Decimal("0"),
                )

                for skill_id
                in covered_skill_ids
            ),
            Decimal("0"),
        )


        if (
            total_missing_demand
            > 0
        ):

            weighted_ratio = (
                weighted_coverage
                / total_missing_demand
            )


        else:

            weighted_ratio = (
                Decimal("0")
            )


        skill_count_ratio = (
            Decimal(
                len(
                    covered_skill_ids
                )
            )
            / Decimal(
                missing_count
            )
        )


        # ----------------------------------------------------
        # 추천 점수
        #
        # 80% : 직무 수요 가중 Skill Coverage
        # 20% : 부족 Skill 개수 Coverage
        #
        # 임의의 학교/자격증 가산점은 넣지 않음.
        # ----------------------------------------------------

        score = (
            weighted_ratio
            * Decimal("0.80")

            + skill_count_ratio
            * Decimal("0.20")
        )


        score = min(
            score,
            Decimal("1"),
        )


        recommendations.append(
            Recommendation(
                resource_id=(
                    resource[
                        "resource_id"
                    ]
                ),

                resource_type=(
                    resource[
                        "resource_type"
                    ]
                ),

                name=(
                    resource[
                        "name"
                    ]
                ),

                organization=(
                    resource[
                        "organization"
                    ]
                ),

                canonical_url=(
                    resource[
                        "canonical_url"
                    ]
                ),

                covered_skills=[
                    resource[
                        "skills"
                    ][
                        skill_id
                    ]

                    for skill_id
                    in covered_skill_ids
                ],

                weighted_coverage=(
                    weighted_coverage.quantize(
                        Decimal(
                            "0.0001"
                        )
                    )
                ),

                coverage_ratio=(
                    weighted_ratio.quantize(
                        Decimal(
                            "0.0001"
                        )
                    )
                ),

                recommendation_score=(
                    score.quantize(
                        Decimal(
                            "0.0001"
                        )
                    )
                ),

                year_level=(
                    resource[
                        "year_level"
                    ]
                ),

                semester=(
                    resource[
                        "semester"
                    ]
                ),

                current_status=(
                    resource[
                        "current_status"
                    ]
                ),
            )
        )


    recommendations.sort(
        key=lambda x: (
            -x.recommendation_score,
            -x.weighted_coverage,
            x.resource_type,
            x.name,
        )
    )


    return recommendations[
        :top_n
    ]


# ============================================================
# 결과 출력
# ============================================================

def print_student_profile(
    student: dict,
    student_skills: list[
        dict
    ],
) -> None:

    print(
        "\n"
        + "=" * 90
    )

    print(
        "1. STUDENT PROFILE"
    )

    print(
        "=" * 90
    )


    print(
        (
            f"student_id : "
            f"{student['student_id']}"
        )
    )


    print(
        (
            f"학교       : "
            f"{student['university_name'] or '-'}"
        )
    )


    print(
        (
            f"학과       : "
            f"{student['department_name'] or '-'}"
        )
    )


    print(
        (
            f"학년       : "
            f"{student['school_year'] or '-'}"
        )
    )


    print(
        (
            f"목표 직무  : "
            f"{student['target_role_name'] or '-'}"
        )
    )


    print(
        (
            f"보유 Skill : "
            f"{len(student_skills)}개"
        )
    )


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

            else 0.0
        )


        print(
            (
                f"  - {skill['skill_name']:<28} "
                f"confidence="
                f"{confidence:.2f} "
                f"| verified="
                f"{skill['verified']} "
                f"| evidence="
                f"{skill['evidence_count']}"
            )
        )


def print_skill_gap(
    student: dict,
    gaps: list[
        GapSkill
    ],
    readiness: Decimal,
) -> None:

    print(
        "\n"
        + "=" * 90
    )

    print(
        "2. TARGET ROLE SKILL GAP"
    )

    print(
        "=" * 90
    )


    print(
        (
            f"목표 직무 : "
            f"{student['target_role_name']}"
        )
    )


    print(
        (
            "현재 Skill Readiness : "
            f"{float(readiness) * 100:.1f}%"
        )
    )


    print()


    for gap in gaps:

        status = (
            "보유"
            if gap.owned
            else "부족"
        )


        if (
            gap.demand_score
            >= Decimal("0.60")
        ):

            priority = (
                "HIGH"
            )


        elif (
            gap.demand_score
            >= Decimal("0.30")
        ):

            priority = (
                "MEDIUM"
            )


        else:

            priority = (
                "LOW"
            )


        print(
            (
                f"[{status:<2}] "
                f"{gap.skill_name:<28} "
                f"DEMAND="
                f"{gap.demand_score} "
                f"| PRIORITY="
                f"{priority:<6} "
                f"| REQ="
                f"{gap.required_count} "
                f"PREF="
                f"{gap.preferred_count} "
                f"| SAMPLE="
                f"{gap.sample_count}"
            )
        )


def print_recommendations(
    recommendations: list[
        Recommendation
    ],
) -> None:

    print(
        "\n"
        + "=" * 90
    )

    print(
        "3. GAP-BASED RESOURCE RECOMMENDATION"
    )

    print(
        "=" * 90
    )


    if not recommendations:

        print(
            "추천할 Resource가 없습니다."
        )

        return


    for index, rec in enumerate(
        recommendations,
        start=1,
    ):

        print()


        print(
            (
                f"{index:02d}. "
                f"[{rec.resource_type}] "
                f"{rec.name}"
            )
        )


        print(
            (
                f"    score       : "
                f"{rec.recommendation_score}"
            )
        )


        print(
            (
                f"    gap coverage: "
                f"{rec.coverage_ratio}"
            )
        )


        print(
            (
                "    skills      : "
                + ", ".join(
                    rec.covered_skills
                )
            )
        )


        if (
            rec.resource_type
            == "COURSE"
        ):

            print(
                (
                    f"    course      : "
                    f"{rec.year_level or '-'}학년 "
                    f"| "
                    f"{rec.semester or '-'}"
                )
            )


        if rec.organization:

            print(
                (
                    f"    organization: "
                    f"{rec.organization}"
                )
            )


        if rec.current_status:

            print(
                (
                    f"    status      : "
                    f"{rec.current_status}"
                )
            )


# ============================================================
# 전체 분석
# ============================================================

def analyze_student(
    student_id: int,
    *,
    sync_skills: bool,
    top_n: int,
) -> None:

    student = (
        load_student(
            student_id
        )
    )


    if sync_skills:

        print(
            "\n[StudentSkill 동기화]"
        )

        sync_student_skills(
            student_id
        )


    student_skills = (
        load_student_skills(
            student_id
        )
    )


    (
        gaps,
        readiness,
    ) = calculate_skill_gap(
        student,
        student_skills,
    )


    recommendations = (
        recommend_resources(
            student,
            gaps,
            top_n=top_n,
        )
    )


    print_student_profile(
        student,
        student_skills,
    )


    print_skill_gap(
        student,
        gaps,
        readiness,
    )


    print_recommendations(
        recommendations
    )


    print(
        "\n"
        + "=" * 90
    )

    print(
        "CAREERQUEST CORE FLOW COMPLETE"
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

        default=None,
    )


    parser.add_argument(
        "--sync-skills",

        action="store_true",

        help=(
            "COMPLETED COURSE/CERTIFICATE/PROGRAM의 "
            "Skill을 StudentSkill로 동기화"
        ),
    )


    parser.add_argument(
        "--demo-seed",

        action="store_true",

        help=(
            "전체 파이프라인 검증용 "
            "건양대 AI학과 DEMO 학생 생성"
        ),
    )


    parser.add_argument(
        "--list-students",

        action="store_true",
    )


    parser.add_argument(
        "--top",

        type=int,

        default=10,
    )


    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================

def main(
) -> None:

    args = (
        parse_args()
    )


    print(
        "=" * 90
    )

    print(
        "CareerQuest - Student Skill Gap & Recommendation Engine"
    )

    print(
        "=" * 90
    )


    if args.list_students:

        show_students()

        return


    student_id = (
        args.student_id
    )


    if args.demo_seed:

        student_id = (
            seed_demo_student()
        )


    if student_id is None:

        print(
            "student_id가 필요합니다."
        )

        print()

        show_students()

        print()

        print(
            (
                "학생이 아직 없으면 "
                "--demo-seed를 사용하세요."
            )
        )

        return


    analyze_student(
        student_id,
        sync_skills=(
            args.sync_skills
        ),
        top_n=(
            args.top
        ),
    )


if __name__ == "__main__":

    main()