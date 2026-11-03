from __future__ import annotations

from typing import Any

import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
)
from fastapi.responses import (
    HTMLResponse,
)
from sqlalchemy import text
from sqlalchemy.exc import (
    SQLAlchemyError,
)

from src.db.database import engine

from src.gap_service import (
    GapInputError,
    GapRequest,
    GapResultItem,
    calculate_gap_request,
)

from src.mapping.student_experience_skill_bridge import (
    build_mapping_input,
)


app = FastAPI(
    title="CareerQuest",

    description=(
        "CareerQuest DB 조회 및 "
        "AI 모델 연동 API"
    ),

    version="0.2.0",
)


# ============================================================
# DB 조회 공통
# ============================================================

def fetch_all(
    sql: str,
    params: dict[
        str,
        Any,
    ] | None = None,
) -> list[
    dict[
        str,
        Any,
    ]
]:

    try:

        with engine.connect() as conn:

            rows = (
                conn.execute(
                    text(
                        sql
                    ),
                    params
                    or {},
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

    except SQLAlchemyError as error:

        raise HTTPException(
            status_code=500,

            detail=(
                f"DB 조회 실패: "
                f"{error}"
            ),
        ) from error


def fetch_one(
    sql: str,

    params: dict[
        str,
        Any,
    ] | None = None,
) -> dict[
    str,
    Any,
] | None:

    rows = (
        fetch_all(
            sql,
            params,
        )
    )

    if not rows:
        return None

    return rows[
        0
    ]


def fetch_scalar(
    sql: str,

    params: dict[
        str,
        Any,
    ] | None = None,
):

    try:

        with engine.connect() as conn:

            return (
                conn.execute(
                    text(
                        sql
                    ),
                    params
                    or {},
                )
                .scalar()
            )

    except SQLAlchemyError as error:

        raise HTTPException(
            status_code=500,

            detail=(
                f"DB 조회 실패: "
                f"{error}"
            ),
        ) from error


# ============================================================
# Home
# ============================================================

@app.get(
    "/",

    response_class=(
        HTMLResponse
    ),

    tags=[
        "System"
    ],
)
def home() -> str:

    return """
    <!doctype html>

    <html lang="ko">

    <head>

        <meta charset="utf-8">

        <title>
            CareerQuest
        </title>

        <style>

            body {
                max-width: 900px;
                margin: 50px auto;
                padding: 0 20px;
                font-family:
                    Arial,
                    "Malgun Gothic",
                    sans-serif;
                line-height: 1.7;
            }

            code {
                background: #f3f3f3;
                padding: 3px 6px;
                border-radius: 4px;
            }

        </style>

    </head>

    <body>

        <h1>
            CareerQuest
        </h1>

        <p>
            CareerQuest DB 조회 및
            AI 모델 연동 API
        </p>

        <h2>
            바로가기
        </h2>

        <p>
            <a href="/docs">
                Swagger API 문서
            </a>
        </p>

        <p>
            <a href="/api/summary">
                DB 요약
            </a>
        </p>

        <p>
            <a href="/api/skills">
                Skill 사전
            </a>
        </p>

        <p>
            <a href="/api/job-roles">
                JobRole
            </a>
        </p>

        <p>
            <a href="/api/students">
                학생
            </a>
        </p>

        <p>
            <a href="/api/gap/contract">
                GAP 연동 계약
            </a>
        </p>

        <hr>

        <p>
            GAP 계산은 모델팀이 전달한
            <code>skill_id</code>와
            학생의
            <code>StudentSkill</code>을
            비교합니다.
        </p>

        <p>
            최종 추천 판단은
            AI 모델이 담당합니다.
        </p>

    </body>

    </html>
    """


# ============================================================
# System
# ============================================================

@app.get(
    "/health",

    tags=[
        "System"
    ],
)
def health() -> dict:

    return {
        "status":
            "ok",

        "database":
            fetch_scalar(
                (
                    "SELECT "
                    "current_database()"
                )
            ),
    }


# ============================================================
# DB Summary
# ============================================================

@app.get(
    "/api/summary",

    tags=[
        "Database"
    ],
)
def database_summary() -> dict:

    resources = (
        fetch_all(
            """
            SELECT
                resource_type,

                COUNT(*) AS total,

                COUNT(*) FILTER (
                    WHERE is_active = TRUE
                ) AS active

            FROM resource

            GROUP BY
                resource_type

            ORDER BY
                resource_type
            """
        )
    )

    return {
        "database":
            fetch_scalar(
                (
                    "SELECT "
                    "current_database()"
                )
            ),

        "skill_count":
            fetch_scalar(
                """
                SELECT
                    COUNT(*)

                FROM skill

                WHERE
                    is_active = TRUE
                """
            ),

        "job_role_count":
            fetch_scalar(
                """
                SELECT
                    COUNT(*)

                FROM job_role

                WHERE
                    is_active = TRUE
                """
            ),

        "student_count":
            fetch_scalar(
                """
                SELECT
                    COUNT(*)

                FROM student
                """
            ),

        "student_experience_count":
            fetch_scalar(
                """
                SELECT
                    COUNT(*)

                FROM student_experience
                """
            ),

        "student_skill_count":
            fetch_scalar(
                """
                SELECT
                    COUNT(*)

                FROM student_skill
                """
            ),

        "resources":
            resources,
    }


# ============================================================
# Skill
# ============================================================

@app.get(
    "/api/skills",

    tags=[
        "Skill"
    ],
)
def skills() -> list[dict]:

    return fetch_all(
        """
        SELECT
            s.skill_id,
            s.name,
            s.normalized_name,
            s.category,

            STRING_AGG(
                sa.alias,
                ', '
                ORDER BY sa.alias
            ) AS aliases

        FROM skill s

        LEFT JOIN skill_alias sa
            ON sa.skill_id =
               s.skill_id

        WHERE
            s.is_active = TRUE

        GROUP BY
            s.skill_id,
            s.name,
            s.normalized_name,
            s.category

        ORDER BY
            s.skill_id
        """
    )


# ============================================================
# JobRole
# ============================================================

@app.get(
    "/api/job-roles",

    tags=[
        "Job Role"
    ],
)
def job_roles() -> list[dict]:

    return fetch_all(
        """
        SELECT
            jr.job_role_id,
            jr.name,
            jr.category,

            COUNT(
                DISTINCT
                rjr.resource_id
            ) AS mapped_jobs,

            COUNT(
                DISTINCT
                rs.skill_id
            ) AS role_skill_count

        FROM job_role jr

        LEFT JOIN resource_job_role rjr
            ON rjr.job_role_id =
               jr.job_role_id

        LEFT JOIN role_skill rs
            ON rs.job_role_id =
               jr.job_role_id

        WHERE
            jr.is_active = TRUE

        GROUP BY
            jr.job_role_id,
            jr.name,
            jr.category

        ORDER BY
            jr.job_role_id
        """
    )


# ============================================================
# Student
# ============================================================

@app.get(
    "/api/students",

    tags=[
        "Student"
    ],
)
def students() -> list[dict]:

    return fetch_all(
        """
        SELECT
            st.student_id,
            st.external_user_id,

            u.name AS university,
            d.name AS department,

            st.school_year,
            st.gpa,

            jr.job_role_id
                AS target_job_role_id,

            jr.name
                AS target_job_role

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

        ORDER BY
            st.student_id
        """
    )


@app.get(
    (
        "/api/students/"
        "{student_id}/resources"
    ),

    tags=[
        "Student"
    ],
)
def student_resources(
    student_id: int,
) -> list[dict]:

    return fetch_all(
        """
        SELECT
            sr.student_resource_id,

            r.resource_id,
            r.resource_type,
            r.name,

            sr.status,
            sr.completed_at,
            sr.grade_text

        FROM student_resource sr

        JOIN resource r
            ON r.resource_id =
               sr.resource_id

        WHERE
            sr.student_id =
                :student_id

        ORDER BY
            r.resource_type,
            r.name
        """,

        {
            "student_id":
                student_id,
        },
    )


# ============================================================
# Student Experience
# ============================================================

@app.get(
    (
        "/api/students/"
        "{student_id}/experiences"
    ),

    tags=[
        "Student Experience"
    ],
)
def student_experiences(
    student_id: int,
) -> list[dict]:

    return fetch_all(
        """
        SELECT
            experience_id,
            student_id,

            experience_type,
            title,
            organization,
            description,

            start_date,
            end_date

        FROM student_experience

        WHERE
            student_id =
                :student_id

        ORDER BY
            experience_id
        """,

        {
            "student_id":
                student_id,
        },
    )


# ============================================================
# Student Skill
# ============================================================

@app.get(
    (
        "/api/students/"
        "{student_id}/skills"
    ),

    tags=[
        "Student Skill"
    ],
)
def student_skills(
    student_id: int,
) -> list[dict]:

    return fetch_all(
        """
        SELECT
            ss.student_skill_id,

            s.skill_id,
            s.name AS skill_name,
            s.category,

            ss.level,
            ss.confidence,
            ss.verified

        FROM student_skill ss

        JOIN skill s
            ON s.skill_id =
               ss.skill_id

        WHERE
            ss.student_id =
                :student_id

        ORDER BY
            s.name
        """,

        {
            "student_id":
                student_id,
        },
    )


@app.get(
    (
        "/api/students/"
        "{student_id}/skill-evidence"
    ),

    tags=[
        "Student Skill"
    ],
)
def skill_evidence(
    student_id: int,
) -> list[dict]:

    return fetch_all(
        """
        SELECT
            sse.evidence_id,

            s.skill_id,
            s.name AS skill_name,

            sse.evidence_type,

            sse.resource_id,
            r.name AS resource_name,

            sse.experience_id,
            se.title AS experience_title,

            sse.description,
            sse.weight

        FROM student_skill_evidence sse

        JOIN student_skill ss
            ON ss.student_skill_id =
               sse.student_skill_id

        JOIN skill s
            ON s.skill_id =
               ss.skill_id

        LEFT JOIN resource r
            ON r.resource_id =
               sse.resource_id

        LEFT JOIN student_experience se
            ON se.experience_id =
               sse.experience_id

        WHERE
            ss.student_id =
                :student_id

        ORDER BY
            s.name,
            sse.evidence_id
        """,

        {
            "student_id":
                student_id,
        },
    )


# ============================================================
# StudentExperience -> Skill 모델 입력
# ============================================================

@app.get(
    (
        "/api/model/experience/"
        "{experience_id}/input"
    ),

    tags=[
        "Model"
    ],
)
def model_input(
    experience_id: int,
) -> dict:

    try:

        return build_mapping_input(
            experience_id
        )

    except Exception as error:

        raise HTTPException(
            status_code=404,
            detail=str(
                error
            ),
        ) from error


@app.get(
    "/api/model/contract",

    tags=[
        "Model"
    ],
)
def model_contract() -> dict:

    return {
        "contract_version":
            "student_experience_skill_v1",

        "description":
            (
                "StudentExperience를 분석해 "
                "CareerQuest Skill DB에 존재하는 "
                "skill_id만 반환하는 계약"
            ),

        "output_example": {
            "contract_version":
                "student_experience_skill_v1",

            "student_id":
                1,

            "experience_id":
                1,

            "mappings": [
                {
                    "skill_id":
                        1,

                    "evidence_text":
                        (
                            "학생 경험 원문의 "
                            "실제 문구"
                        ),

                    "confidence":
                        0.95,

                    "level":
                        None,

                    "reason":
                        "직접 사용한 Skill",
                }
            ],

            "unmapped_skills":
                [],
        },
    }


# ============================================================
# SkillRow -> StudentSkill -> GAP
# ============================================================

@app.post(
    "/api/gap/calculate",

    response_model=(
        list[
            GapResultItem
        ]
    ),

    tags=[
        "Gap"
    ],
)
def calculate_gap(
    request: GapRequest,
) -> list[dict]:

    """
    모델팀이 전달한 SkillRow의 skill_id와
    StudentSkill을 비교해 GAP만 계산합니다.

    최종 추천 순위와 추천 이유는
    이 API에서 결정하지 않습니다.
    """

    try:

        return calculate_gap_request(
            request
        )

    except GapInputError as error:

        raise HTTPException(
            status_code=422,

            detail=str(
                error
            ),
        ) from error

    except SQLAlchemyError as error:

        raise HTTPException(
            status_code=500,

            detail=(
                f"DB 조회 실패: "
                f"{error}"
            ),
        ) from error


@app.get(
    "/api/gap/contract",

    tags=[
        "Gap"
    ],
)
def gap_contract() -> dict:

    return {
        "description":
            (
                "모델팀 근거 집계 SkillRow와 "
                "학생 StudentSkill을 "
                "skill_id 기준으로 비교해 "
                "GAP만 반환합니다."
            ),

        "input_example": {
            "student_id":
                1,

            "skills": [
                {
                    "skill_id":
                        23,
                },
                {
                    "skill_id":
                        41,

                    "required_level":
                        "IM",
                },
                {
                    "skill_id":
                        None,

                    "skill":
                        "Vector DB",
                },
            ],

            "level_order": [
                "IL",
                "IM",
                "IH",
            ],
        },

        "output_example": [
            {
                "skill_id":
                    23,

                "student_level":
                    None,

                "gap":
                    "미보유",
            },
            {
                "skill_id":
                    41,

                "student_level":
                    "IL",

                "gap":
                    "수준부족",
            },
            {
                "skill_id":
                    None,

                "skill":
                    "Vector DB",

                "student_level":
                    None,

                "gap":
                    "매핑되지 않음",
            },
        ],

        "notes": [
            (
                "skill_id는 모델팀이 "
                "CareerQuest Skill DB 기준으로 "
                "부여해 전달합니다."
            ),
            (
                "백엔드는 Skill 이름을 다시 "
                "skill_id로 추론하지 않습니다."
            ),
            (
                "백엔드는 skill_id "
                "유효성만 검증합니다."
            ),
            (
                "skill_id가 null인 줄(Skill 사전에 "
                "매핑되지 않은 근거 Skill)은 지우지 않고 "
                "gap=\"매핑되지 않음\"으로 "
                "skill 이름과 함께 반환합니다."
            ),
            (
                "required_level이 없으면 "
                "보유/미보유만 판별합니다."
            ),
            (
                "수준 비교가 필요하면 "
                "level_order를 함께 전달합니다."
            ),
            (
                "최종 추천 대상·순위·이유·로드맵은 "
                "AI 모델이 담당합니다."
            ),
        ],
    }


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    uvicorn.run(
        "main:app",

        host="0.0.0.0",

        port=8000,

        reload=False,
    )