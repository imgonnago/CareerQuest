from __future__ import annotations

import argparse
import re

from dataclasses import dataclass

from sqlalchemy import text

from src.db.database import engine


# ============================================================
# 설정
# ============================================================

MAPPING_METHOD = "literal_job_title_v1"

DEFAULT_CONFIDENCE = 0.98


# ============================================================
# 표준 JobRole
#
# 반드시 현재 seed_master.py의 8개 직무명과 동일해야 함.
# ============================================================

STANDARD_ROLES = (
    "AI Engineer",
    "Machine Learning Engineer",
    "Generative AI Engineer",
    "Data Scientist",
    "Data Analyst",
    "Data Engineer",
    "Backend Developer",
    "MLOps Engineer",
)


# ============================================================
# 직무명 규칙
#
# 주의:
# 더 구체적인 직무부터 먼저 검사한다.
#
# 예:
# "AI 데이터 엔지니어"
# -> AI Engineer보다 Data Engineer를 먼저 잡아야 함.
# ============================================================

ROLE_RULES = (
    # --------------------------------------------------------
    # Generative AI Engineer
    # --------------------------------------------------------

    (
        "Generative AI Engineer",
        (
            r"생성형\s*ai",
            r"generative\s*ai",
            r"gen\s*ai",
            r"genai",
            r"llm\s*(?:engineer|개발자|엔지니어)",
            r"rag\s*(?:engineer|개발자|엔지니어)",
        ),
    ),

    # --------------------------------------------------------
    # MLOps Engineer
    # --------------------------------------------------------

    (
        "MLOps Engineer",
        (
            r"mlops\s*(?:engineer|엔지니어|개발자)",
            r"머신러닝\s*플랫폼\s*(?:엔지니어|개발자)",
            r"ml\s*platform\s*engineer",
        ),
    ),

    # --------------------------------------------------------
    # Machine Learning Engineer
    # --------------------------------------------------------

    (
        "Machine Learning Engineer",
        (
            r"machine\s*learning\s*engineer",
            r"머신러닝\s*엔지니어",
            r"머신러닝\s*개발자",
            r"\bml\s*engineer\b",
        ),
    ),

    # --------------------------------------------------------
    # Data Scientist
    # --------------------------------------------------------

    (
        "Data Scientist",
        (
            r"data\s*scientist",
            r"데이터\s*사이언티스트",
            r"데이터과학자",
            r"데이터\s*과학자",
        ),
    ),

    # --------------------------------------------------------
    # Data Analyst
    # --------------------------------------------------------

    (
        "Data Analyst",
        (
            r"data\s*analyst",
            r"데이터\s*분석가",
            r"데이터분석가",
            r"데이터\s*분석\s*(?:담당자|전문가)",
        ),
    ),

    # --------------------------------------------------------
    # Data Engineer
    # --------------------------------------------------------

    (
        "Data Engineer",
        (
            r"data\s*engineer",
            r"데이터\s*엔지니어",
            r"데이터엔지니어",
            r"데이터\s*플랫폼\s*엔지니어",
        ),
    ),

    # --------------------------------------------------------
    # Backend Developer
    # --------------------------------------------------------

    (
        "Backend Developer",
        (
            r"backend\s*(?:developer|engineer)",
            r"back-end\s*(?:developer|engineer)",
            r"백엔드\s*개발자",
            r"백엔드\s*엔지니어",
            r"서버\s*개발자",
            r"서버\s*엔지니어",
        ),
    ),

    # --------------------------------------------------------
    # AI Engineer
    #
    # 가장 일반적이므로 마지막에 검사
    # --------------------------------------------------------

    (
        "AI Engineer",
        (
            r"\bai\s*engineer\b",
            r"ai\s*엔지니어",
            r"ai엔지니어",
            r"인공지능\s*엔지니어",
            r"인공지능\s*개발자",
            r"ai\s*개발자",
            r"ai개발자",
        ),
    ),
)


# ============================================================
# 데이터 구조
# ============================================================

@dataclass
class RoleMatch:
    resource_id: int
    job_name: str

    job_role_id: int
    role_name: str

    confidence: float

    matched_pattern: str

    evidence_text: str


# ============================================================
# 문자열
# ============================================================

def normalize_title(
    value: str,
) -> str:

    value = (
        value
        or ""
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


# ============================================================
# JobRole DB 조회
# ============================================================

def load_job_roles(
) -> dict[str, int]:

    sql = text(
        """
        SELECT
            job_role_id,
            name

        FROM job_role

        WHERE is_active = TRUE
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql
            ).mappings().all()
        )


    result = {
        row["name"]:
            int(
                row["job_role_id"]
            )

        for row
        in rows
    }


    # --------------------------------------------------------
    # 8개 표준 직무가 DB에 모두 있는지 검증
    # --------------------------------------------------------

    missing = [
        role

        for role
        in STANDARD_ROLES

        if role
        not in result
    ]


    if missing:

        raise RuntimeError(
            "DB에 없는 JobRole: "
            + ", ".join(
                missing
            )
        )


    return result


# ============================================================
# JOB 조회
# ============================================================

def load_jobs(
) -> list[dict]:

    sql = text(
        """
        SELECT
            resource_id,
            name

        FROM resource

        WHERE
            resource_type = 'JOB'

          AND is_active = TRUE

        ORDER BY
            resource_id
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql
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
# 기존 JobRole 매핑 조회
#
# 이미 사람이든 모델이든 매핑한 JOB은 건드리지 않는다.
# ============================================================

def load_existing_role_resources(
) -> set[int]:

    sql = text(
        """
        SELECT DISTINCT
            resource_id

        FROM resource_job_role
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql
            ).scalars().all()
        )


    return {
        int(
            value
        )

        for value
        in rows
    }


# ============================================================
# 제목 → JobRole
# ============================================================

def classify_job_title(
    title: str,
) -> tuple[
    str,
    str,
] | None:

    title = (
        normalize_title(
            title
        )
    )


    if not title:

        return None


    for (
        role_name,
        patterns,
    ) in ROLE_RULES:

        for pattern in patterns:

            match = re.search(
                pattern,
                title,
                flags=re.IGNORECASE,
            )


            if match:

                return (
                    role_name,
                    pattern,
                )


    return None


# ============================================================
# 매핑 계획
# ============================================================

def build_plan(
) -> tuple[
    list[RoleMatch],
    list[dict],
    dict,
]:

    roles = (
        load_job_roles()
    )


    jobs = (
        load_jobs()
    )


    existing_resources = (
        load_existing_role_resources()
    )


    plan: list[
        RoleMatch
    ] = []


    unmapped: list[
        dict
    ] = []


    protected = 0


    for job in jobs:

        resource_id = int(
            job[
                "resource_id"
            ]
        )


        job_name = (
            job[
                "name"
            ]
            or ""
        )


        # ----------------------------------------------------
        # 기존 Role 매핑 보호
        # ----------------------------------------------------

        if (
            resource_id
            in existing_resources
        ):

            protected += 1

            continue


        result = (
            classify_job_title(
                job_name
            )
        )


        if result is None:

            unmapped.append(
                {
                    "resource_id":
                        resource_id,

                    "name":
                        job_name,
                }
            )

            continue


        (
            role_name,
            matched_pattern,
        ) = result


        plan.append(
            RoleMatch(
                resource_id=(
                    resource_id
                ),

                job_name=(
                    job_name
                ),

                job_role_id=(
                    roles[
                        role_name
                    ]
                ),

                role_name=(
                    role_name
                ),

                confidence=(
                    DEFAULT_CONFIDENCE
                ),

                matched_pattern=(
                    matched_pattern
                ),

                # 제목 그 자체가 근거
                evidence_text=(
                    job_name
                ),
            )
        )


    stats = {
        "jobs":
            len(
                jobs
            ),

        "existing_protected":
            protected,

        "new_candidates":
            len(
                plan
            ),

        "unmapped":
            len(
                unmapped
            ),
    }


    return (
        plan,
        unmapped,
        stats,
    )


# ============================================================
# DRY RUN 출력
# ============================================================

def print_plan(
    plan: list[
        RoleMatch
    ],

    unmapped: list[
        dict
    ],

    stats: dict,
) -> None:

    print(
        "=" * 80
    )


    print(
        "CareerQuest - JOB -> JobRole 보수적 제목 매핑"
    )


    print(
        "=" * 80
    )


    for item in plan:

        print()


        print(
            (
                f"[JOB {item.resource_id}] "
                f"{item.job_name}"
            )
        )


        print(
            (
                f"  -> {item.role_name}"
                f" | confidence="
                f"{item.confidence:.2f}"
            )
        )


        print(
            (
                "     evidence : "
                f"{item.evidence_text}"
            )
        )


    print(
        "\n"
        + "=" * 80
    )


    print(
        "UNMAPPED"
    )


    print(
        "=" * 80
    )


    if not unmapped:

        print(
            "없음"
        )


    else:

        for job in unmapped:

            print(
                (
                    f"[{job['resource_id']}] "
                    f"{job['name']}"
                )
            )


    print(
        "\n"
        + "=" * 80
    )


    print(
        "검증 요약"
    )


    print(
        "=" * 80
    )


    print(
        (
            f"JOB 전체              : "
            f"{stats['jobs']}개"
        )
    )


    print(
        (
            f"기존 JobRole 보호     : "
            f"{stats['existing_protected']}개"
        )
    )


    print(
        (
            f"신규 매핑 후보        : "
            f"{stats['new_candidates']}개"
        )
    )


    print(
        (
            f"팀원 모델 검토 대상   : "
            f"{stats['unmapped']}개"
        )
    )


# ============================================================
# 저장
# ============================================================

INSERT_SQL = text(
    """
    INSERT INTO resource_job_role (
        resource_id,

        job_role_id,

        is_primary,

        mapping_method,

        confidence,

        evidence_text
    )

    VALUES (
        :resource_id,

        :job_role_id,

        TRUE,

        :mapping_method,

        :confidence,

        :evidence_text
    )

    ON CONFLICT (
        resource_id,
        job_role_id
    )

    DO NOTHING
    """
)


def save_plan(
    plan: list[
        RoleMatch
    ],
) -> int:

    inserted = 0


    with engine.begin() as conn:

        for item in plan:

            result = (
                conn.execute(
                    INSERT_SQL,

                    {
                        "resource_id":
                            item.resource_id,

                        "job_role_id":
                            item.job_role_id,

                        "mapping_method":
                            MAPPING_METHOD,

                        "confidence":
                            item.confidence,

                        "evidence_text":
                            item.evidence_text,
                    },
                )
            )


            if (
                result.rowcount
                > 0
            ):

                inserted += 1


    return inserted


# ============================================================
# 현재 상태 출력
# ============================================================

def show_current(
) -> None:

    sql = text(
        """
        SELECT
            r.resource_id,

            r.name AS job_name,

            jr.name AS role_name,

            rjr.is_primary,

            rjr.mapping_method,

            rjr.confidence

        FROM resource_job_role rjr

        JOIN resource r
            ON r.resource_id =
               rjr.resource_id

        JOIN job_role jr
            ON jr.job_role_id =
               rjr.job_role_id

        WHERE
            r.resource_type =
                'JOB'

        ORDER BY
            r.resource_id,
            rjr.is_primary DESC,
            jr.name
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
        "현재 JOB -> JobRole"
    )


    print(
        "=" * 80
    )


    for row in rows:

        print(
            (
                f"[{row['resource_id']}] "
                f"{row['job_name']}"
            )
        )


        print(
            (
                f"  -> {row['role_name']}"
                f" | primary="
                f"{row['is_primary']}"
                f" | confidence="
                f"{float(row['confidence']):.2f}"
                f" | method="
                f"{row['mapping_method']}"
            )
        )


    print()


    print(
        (
            "총 JobRole 매핑: "
            f"{len(rows)}개"
        )
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
        "--save",

        action="store_true",
    )


    parser.add_argument(
        "--show",

        action="store_true",
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


    if args.show:

        show_current()

        return


    (
        plan,
        unmapped,
        stats,
    ) = build_plan()


    print_plan(
        plan,
        unmapped,
        stats,
    )


    if not args.save:

        print()


        print(
            "[DRY RUN]"
        )


        print(
            "DB를 수정하지 않았습니다."
        )


        print(
            (
                "JobRole이 제목과 정확히 맞는지 "
                "확인한 뒤 --save를 사용하세요."
            )
        )


        return


    inserted = (
        save_plan(
            plan
        )
    )


    print()


    print(
        "=" * 80
    )


    print(
        (
            "신규 JOB -> JobRole 저장: "
            f"{inserted}개"
        )
    )


    print(
        "=" * 80
    )


    show_current()


if __name__ == "__main__":

    main()