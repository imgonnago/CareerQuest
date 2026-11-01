from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy import text

from src.db.database import engine


# ============================================================
# 기준
# ============================================================

TARGET_DEPARTMENT = "인공지능학과"
TARGET_CURRICULUM_YEAR = 2026


# ============================================================
# 문자열 처리
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()


def normalize_for_match(value: Any) -> str:
    """
    단순 문자열 비교용 정규화.

    실제 DB 저장/Skill 확정에는 사용하지 않는다.
    진단용이다.
    """

    value = clean_text(
        value
    ).lower()

    return re.sub(
        r"[^0-9a-zA-Z가-힣+#.]",
        "",
        value,
    )


# ============================================================
# Skill 조회
# ============================================================

def load_skills() -> list[dict]:

    sql = text(
        """
        SELECT
            s.skill_id,
            s.name,
            s.normalized_name,
            s.category,
            s.description,

            COALESCE(
                string_agg(
                    sa.alias,
                    ', '
                    ORDER BY sa.alias
                ),
                ''
            ) AS aliases

        FROM skill s

        LEFT JOIN skill_alias sa
            ON sa.skill_id = s.skill_id

        WHERE s.is_active = TRUE

        GROUP BY
            s.skill_id,
            s.name,
            s.normalized_name,
            s.category,
            s.description

        ORDER BY
            s.skill_id
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            sql
        ).mappings().all()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# COURSE 조회
# ============================================================

def load_courses() -> list[dict]:

    sql = text(
        """
        SELECT
            r.resource_id,
            r.name,
            r.description,

            cd.course_code,
            cd.year_level,
            cd.semester,
            cd.completion_type,
            cd.credits,
            cd.curriculum_year,

            d.department_id,
            d.name AS department_name

        FROM resource r

        JOIN course_detail cd
            ON cd.resource_id = r.resource_id

        JOIN department d
            ON d.department_id = cd.department_id

        WHERE r.resource_type = 'COURSE'
          AND cd.curriculum_year = :curriculum_year
          AND d.name = :department_name

        ORDER BY
            cd.year_level NULLS LAST,
            cd.semester NULLS LAST,
            cd.course_code NULLS LAST,
            r.resource_id
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "curriculum_year":
                    TARGET_CURRICULUM_YEAR,

                "department_name":
                    TARGET_DEPARTMENT,
            },
        ).mappings().all()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# 현재 RESOURCE_SKILL(TAUGHT) 조회
# ============================================================

def load_existing_taught_mappings() -> list[dict]:

    sql = text(
        """
        SELECT
            rs.resource_skill_id,
            rs.resource_id,
            rs.skill_id,

            cd.course_code,

            r.name AS course_name,
            s.name AS skill_name,

            rs.confidence,
            rs.mapping_method,
            rs.verified

        FROM resource_skill rs

        JOIN resource r
            ON r.resource_id = rs.resource_id

        JOIN course_detail cd
            ON cd.resource_id = r.resource_id

        JOIN department d
            ON d.department_id = cd.department_id

        JOIN skill s
            ON s.skill_id = rs.skill_id

        WHERE r.resource_type = 'COURSE'
          AND cd.curriculum_year = :curriculum_year
          AND d.name = :department_name
          AND rs.relation_type = 'TAUGHT'

        ORDER BY
            cd.course_code,
            rs.confidence DESC NULLS LAST,
            s.name
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {
                "curriculum_year":
                    TARGET_CURRICULUM_YEAR,

                "department_name":
                    TARGET_DEPARTMENT,
            },
        ).mappings().all()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# Skill의 이름 + Alias 목록 만들기
# ============================================================

def build_skill_terms(
    skill: dict,
) -> list[str]:

    terms: set[str] = set()

    name = clean_text(
        skill.get(
            "name"
        )
    )

    normalized_name = clean_text(
        skill.get(
            "normalized_name"
        )
    )

    aliases_raw = clean_text(
        skill.get(
            "aliases"
        )
    )

    if name:
        terms.add(
            name
        )

    if normalized_name:
        terms.add(
            normalized_name
        )

    if aliases_raw:
        for alias in aliases_raw.split(
            ","
        ):

            alias = clean_text(
                alias
            )

            if alias:
                terms.add(
                    alias
                )

    return sorted(
        terms
    )


# ============================================================
# 매우 보수적인 literal candidate 탐색
#
# DB 저장용이 아니다.
# "과목명에 Skill/alias가 실제로 적혀있는가?"만 확인한다.
# ============================================================

def find_literal_candidates(
    courses: list[dict],
    skills: list[dict],
) -> dict[int, list[dict]]:

    result: dict[
        int,
        list[dict]
    ] = defaultdict(
        list
    )

    for course in courses:

        resource_id = int(
            course[
                "resource_id"
            ]
        )

        course_name = normalize_for_match(
            course.get(
                "name"
            )
        )

        if not course_name:
            continue

        for skill in skills:

            skill_id = int(
                skill[
                    "skill_id"
                ]
            )

            matched_terms: list[str] = []

            for term in build_skill_terms(
                skill
            ):

                normalized_term = (
                    normalize_for_match(
                        term
                    )
                )

                # 너무 짧은 단어는 오탐 가능성이 큼
                # AI, R, C 같은 문자열 자동매칭 방지
                if len(
                    normalized_term
                ) < 3:
                    continue

                if (
                    normalized_term
                    in course_name
                ):
                    matched_terms.append(
                        term
                    )

            if not matched_terms:
                continue

            result[
                resource_id
            ].append(
                {
                    "skill_id":
                        skill_id,

                    "skill_name":
                        skill[
                            "name"
                        ],

                    "matched_terms":
                        sorted(
                            set(
                                matched_terms
                            )
                        ),
                }
            )

    return dict(
        result
    )


# ============================================================
# 출력
# ============================================================

def print_skills(
    skills: list[dict],
) -> None:

    print(
        "\n"
        + "=" * 80
    )

    print(
        "1. 현재 Skill DB"
    )

    print(
        "=" * 80
    )

    for skill in skills:

        aliases = clean_text(
            skill.get(
                "aliases"
            )
        )

        category = clean_text(
            skill.get(
                "category"
            )
        )

        description = clean_text(
            skill.get(
                "description"
            )
        )

        print(
            (
                f"[{skill['skill_id']:>3}] "
                f"{skill['name']}"
            )
        )

        print(
            (
                f"      category: "
                f"{category or '-'}"
            )
        )

        print(
            (
                f"      aliases : "
                f"{aliases or '-'}"
            )
        )

        if description:
            print(
                (
                    f"      desc    : "
                    f"{description}"
                )
            )


def print_courses(
    courses: list[dict],
) -> None:

    print(
        "\n"
        + "=" * 80
    )

    print(
        "2. 현재 2026 인공지능학과 COURSE"
    )

    print(
        "=" * 80
    )

    for index, course in enumerate(
        courses,
        start=1,
    ):

        description = clean_text(
            course.get(
                "description"
            )
        )

        print(
            (
                f"{index:02d}. "
                f"[{course.get('course_code')}] "
                f"{course.get('name')}"
            )
        )

        print(
            (
                f"      resource_id="
                f"{course.get('resource_id')} "
                f"| "
                f"{course.get('year_level') or '-'}학년 "
                f"| "
                f"{course.get('semester') or '-'}학기 "
                f"| "
                f"{course.get('completion_type') or '-'} "
                f"| "
                f"{course.get('credits') if course.get('credits') is not None else '-'}학점"
            )
        )

        print(
            (
                "      description: "
                f"{'있음' if description else '없음'}"
            )
        )


def print_literal_candidates(
    courses: list[dict],
    candidates: dict[int, list[dict]],
) -> None:

    print(
        "\n"
        + "=" * 80
    )

    print(
        "3. 과목명에 Skill 이름/Alias가 직접 들어간 후보"
    )

    print(
        "=" * 80
    )

    found_count = 0

    for course in courses:

        resource_id = int(
            course[
                "resource_id"
            ]
        )

        hits = candidates.get(
            resource_id,
            [],
        )

        if not hits:
            continue

        found_count += 1

        print(
            (
                f"\n"
                f"[{course.get('course_code')}] "
                f"{course.get('name')}"
            )
        )

        for hit in hits:

            terms = ", ".join(
                hit[
                    "matched_terms"
                ]
            )

            print(
                (
                    f"  -> "
                    f"{hit['skill_name']} "
                    f"(skill_id="
                    f"{hit['skill_id']})"
                )
            )

            print(
                (
                    f"     literal match: "
                    f"{terms}"
                )
            )

    if found_count == 0:
        print(
            "직접 문자열 매칭 후보 없음"
        )


def print_existing_mappings(
    mappings: list[dict],
) -> None:

    print(
        "\n"
        + "=" * 80
    )

    print(
        "4. 기존 RESOURCE_SKILL(TAUGHT)"
    )

    print(
        "=" * 80
    )

    if not mappings:

        print(
            "현재 COURSE의 TAUGHT 매핑 없음"
        )

        return

    for row in mappings:

        confidence = (
            float(
                row[
                    "confidence"
                ]
            )

            if row[
                "confidence"
            ]
            is not None

            else None
        )

        confidence_text = (
            f"{confidence:.2f}"

            if confidence
            is not None

            else "-"
        )

        print(
            (
                f"[{row['course_code']}] "
                f"{row['course_name']} "
                f"-> "
                f"{row['skill_name']} "
                f"| confidence="
                f"{confidence_text} "
                f"| method="
                f"{row['mapping_method'] or '-'} "
                f"| verified="
                f"{row['verified']}"
            )
        )


# ============================================================
# 실행
# ============================================================

def run() -> None:

    print(
        "=" * 80
    )

    print(
        "CareerQuest - COURSE / SKILL 매핑 기준 점검"
    )

    print(
        "=" * 80
    )

    # --------------------------------------------------------
    # Skill
    # --------------------------------------------------------

    skills = (
        load_skills()
    )

    # --------------------------------------------------------
    # Course
    # --------------------------------------------------------

    courses = (
        load_courses()
    )

    # --------------------------------------------------------
    # 기존 매핑
    # --------------------------------------------------------

    existing_mappings = (
        load_existing_taught_mappings()
    )

    # --------------------------------------------------------
    # 진단용 직접 문자열 후보
    # --------------------------------------------------------

    literal_candidates = (
        find_literal_candidates(
            courses,
            skills,
        )
    )

    # --------------------------------------------------------
    # 요약
    # --------------------------------------------------------

    print(
        "\n[기본 현황]"
    )

    print(
        (
            f"  Skill: "
            f"{len(skills)}개"
        )
    )

    print(
        (
            f"  COURSE: "
            f"{len(courses)}개"
        )
    )

    print(
        (
            "  기존 "
            "RESOURCE_SKILL(TAUGHT): "
            f"{len(existing_mappings)}개"
        )
    )

    description_count = sum(
        1
        for course
        in courses
        if clean_text(
            course.get(
                "description"
            )
        )
    )

    print(
        (
            "  description 보유 COURSE: "
            f"{description_count}/"
            f"{len(courses)}"
        )
    )

    # --------------------------------------------------------
    # 기대값 경고
    # --------------------------------------------------------

    if len(
        courses
    ) != 66:

        print(
            (
                "\n[주의] "
                f"현재 COURSE가 "
                f"{len(courses)}개입니다. "
                "기대값은 66개입니다."
            )
        )

    if len(
        skills
    ) != 45:

        print(
            (
                "\n[주의] "
                f"현재 Skill이 "
                f"{len(skills)}개입니다. "
                "기존 기준값은 45개입니다."
            )
        )

    # --------------------------------------------------------
    # 상세 출력
    # --------------------------------------------------------

    print_skills(
        skills
    )

    print_courses(
        courses
    )

    print_literal_candidates(
        courses,
        literal_candidates,
    )

    print_existing_mappings(
        existing_mappings
    )

    # --------------------------------------------------------
    # 종료
    # --------------------------------------------------------

    print(
        "\n"
        + "=" * 80
    )

    print(
        "점검 완료"
    )

    print(
        "=" * 80
    )

    print(
        "\n이 스크립트는 DB를 수정하지 않았습니다."
    )


if __name__ == "__main__":
    run()