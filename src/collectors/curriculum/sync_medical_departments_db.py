from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.collectors.curriculum.medical_departments import (
    MEDICAL_DEPARTMENTS,
)
from src.db.database import engine
from src.db.models import (
    Department,
    University,
)
from src.loaders.course_loader import (
    normalize_name,
)


# ============================================================
# 설정
# ============================================================

OFFICIAL_SOURCE_URL = (
    "https://www.konyang.ac.kr/"
    "kor/sub03_01_01.do"
)

OFFICIAL_SOURCE_TITLE = (
    "건양대학교 메디컬캠퍼스 "
    "캠퍼스별 대학현황"
)

OFFICIAL_SOURCE_LAST_MODIFIED = (
    "2026-05-18"
)


# ============================================================
# University 탐색
# ============================================================

def find_konyang_university(
    session: Session,
) -> University:
    """
    가장 안전하게 건양대학교 university_id를 정한다.

    1. 기존 메디컬캠퍼스 학과들이 연결된 university_id 중
       가장 많이 사용되는 ID를 선택
    2. 그것도 불가능하면 이름이 '건양대학교'인 University가
       정확히 1개일 때만 사용
    """

    target_names = {
        normalize_name(
            department.name
        )
        for department
        in MEDICAL_DEPARTMENTS
    }

    existing_departments = list(
        session.scalars(
            select(
                Department
            )
        ).all()
    )

    matched = [
        department

        for department
        in existing_departments

        if (
            normalize_name(
                department.name
            )
            in target_names
        )
    ]

    university_counter = Counter(
        department.university_id
        for department
        in matched
    )

    if university_counter:

        university_id, count = (
            university_counter
            .most_common(
                1
            )[0]
        )

        # 현재 Preflight 결과상
        # 13개가 이미 같은 건양대학교에 연결돼 있으므로
        # 충분히 강한 근거다.
        if count >= 5:

            university = (
                session.get(
                    University,
                    university_id,
                )
            )

            if (
                university is not None
                and "건양대학교"
                in university.name
            ):
                return university

    # --------------------------------------------------------
    # fallback
    # --------------------------------------------------------

    universities = list(
        session.scalars(
            select(
                University
            )
            .where(
                University.name
                == "건양대학교"
            )
        ).all()
    )

    if len(
        universities
    ) == 1:

        return universities[
            0
        ]

    if not universities:

        raise RuntimeError(
            (
                "DB에서 건양대학교 University를 "
                "찾지 못했습니다."
            )
        )

    raise RuntimeError(
        (
            "건양대학교 University가 여러 건이라 "
            "자동으로 university_id를 "
            "결정할 수 없습니다."
        )
    )


# ============================================================
# Department 탐색
# ============================================================

def find_department(
    session: Session,
    *,
    university_id: int,
    name: str,
) -> Department | None:

    wanted = (
        normalize_name(
            name
        )
    )

    candidates = list(
        session.scalars(
            select(
                Department
            )
            .where(
                Department.university_id
                == university_id
            )
        ).all()
    )

    matches = [
        department

        for department
        in candidates

        if (
            normalize_name(
                department.name
            )
            == wanted
        )
    ]

    if len(
        matches
    ) > 1:

        raise RuntimeError(
            (
                "동일 학과명이 여러 건 존재합니다: "
                f"{name} -> "
                f"{[item.department_id for item in matches]}"
            )
        )

    if matches:

        return matches[
            0
        ]

    return None


# ============================================================
# metadata
# ============================================================

def build_registry_metadata(
    department,
    existing_metadata: dict | None,
) -> dict:

    metadata = dict(
        existing_metadata
        or {}
    )

    metadata[
        "careerquest_medical_registry"
    ] = {
        "campus":
            "메디컬캠퍼스",

        "college":
            department.college,

        "official_department_name":
            department.name,

        "source_url":
            OFFICIAL_SOURCE_URL,

        "source_title":
            OFFICIAL_SOURCE_TITLE,

        "source_last_modified":
            OFFICIAL_SOURCE_LAST_MODIFIED,

        "verified_current":
            True,
    }

    return metadata


# ============================================================
# Sync plan
# ============================================================

def build_sync_plan(
    session: Session,
    *,
    university: University,
) -> list[dict]:

    plan: list[
        dict
    ] = []

    for registry in (
        MEDICAL_DEPARTMENTS
    ):

        existing = (
            find_department(
                session,

                university_id=(
                    university.university_id
                ),

                name=(
                    registry.name
                ),
            )
        )

        # ----------------------------------------------------
        # 신규
        # ----------------------------------------------------

        if existing is None:

            plan.append(
                {
                    "action":
                        "CREATE",

                    "registry":
                        registry,

                    "department":
                        None,

                    "changes": {
                        "name":
                            registry.name,

                        "college_name":
                            registry.college,

                        "is_active":
                            True,
                    },
                }
            )

            continue

        # ----------------------------------------------------
        # 기존
        # ----------------------------------------------------

        changes: dict = {}

        if (
            existing.name
            != registry.name
        ):

            changes[
                "name"
            ] = (
                registry.name
            )

        if (
            existing.college_name
            != registry.college
        ):

            changes[
                "college_name"
            ] = (
                registry.college
            )

        if not existing.is_active:

            changes[
                "is_active"
            ] = (
                True
            )

        # metadata는 항상 공식 registry 검증 정보를
        # 최신 상태로 보강한다.
        desired_metadata = (
            build_registry_metadata(
                registry,
                existing.raw_metadata,
            )
        )

        if (
            existing.raw_metadata
            != desired_metadata
        ):

            changes[
                "raw_metadata"
            ] = (
                desired_metadata
            )

        plan.append(
            {
                "action":
                    (
                        "UPDATE"
                        if changes
                        else "KEEP"
                    ),

                "registry":
                    registry,

                "department":
                    existing,

                "changes":
                    changes,
            }
        )

    return plan


# ============================================================
# 출력
# ============================================================

def print_plan(
    plan: list[dict],
) -> None:

    print()
    print(
        "[동기화 계획]"
    )

    for (
        index,
        item,
    ) in enumerate(
        plan,
        start=1,
    ):

        registry = (
            item[
                "registry"
            ]
        )

        department = (
            item[
                "department"
            ]
        )

        action = (
            item[
                "action"
            ]
        )

        department_id = (
            department.department_id

            if department
            is not None

            else "-"
        )

        print(
            (
                f"{index:02d}. "
                f"{registry.name:<12} "
                f"| {action:<6} "
                f"| id={department_id} "
                f"| college={registry.college}"
            )
        )

        if (
            action
            in {
                "CREATE",
                "UPDATE",
            }
        ):

            for (
                key,
                value,
            ) in (
                item[
                    "changes"
                ].items()
            ):

                # raw_metadata 전체를
                # 콘솔에 길게 출력하지 않는다.
                if (
                    key
                    == "raw_metadata"
                ):

                    print(
                        (
                            "      "
                            "raw_metadata="
                            "공식 메디컬캠퍼스 "
                            "registry 정보 보강"
                        )
                    )

                else:

                    print(
                        (
                            f"      "
                            f"{key}="
                            f"{value}"
                        )
                    )


# ============================================================
# 실제 적용
# ============================================================

def apply_sync_plan(
    session: Session,
    *,
    university: University,
    plan: list[dict],
) -> dict:

    stats = {
        "created": 0,
        "updated": 0,
        "kept": 0,
    }

    for item in (
        plan
    ):

        action = (
            item[
                "action"
            ]
        )

        registry = (
            item[
                "registry"
            ]
        )

        # ----------------------------------------------------
        # CREATE
        # ----------------------------------------------------

        if (
            action
            == "CREATE"
        ):

            department = (
                Department(
                    university_id=(
                        university.university_id
                    ),

                    # 공식 홈페이지에서는
                    # KEDI ID를 제공하지 않는다.
                    # 가짜 값을 만들지 않는다.
                    kedi_major_id=None,

                    standard_major_id=None,

                    name=(
                        registry.name
                    ),

                    college_name=(
                        registry.college
                    ),

                    degree_type=None,

                    lesson_years=None,

                    department_characteristic=None,

                    curriculum_description=None,

                    major_area_code=None,

                    major_area_name=None,

                    admission_quota=None,

                    # API 조사년도가 아니므로
                    # 임의로 2026을 넣지 않는다.
                    survey_year=None,

                    source_updated_at=None,

                    raw_metadata=(
                        build_registry_metadata(
                            registry,
                            None,
                        )
                    ),

                    is_active=True,
                )
            )

            session.add(
                department
            )

            session.flush()

            stats[
                "created"
            ] += 1

            print(
                (
                    "  CREATED | "
                    f"{registry.name} | "
                    f"id="
                    f"{department.department_id}"
                )
            )

            continue

        # ----------------------------------------------------
        # UPDATE
        # ----------------------------------------------------

        if (
            action
            == "UPDATE"
        ):

            department = (
                item[
                    "department"
                ]
            )

            changes = (
                item[
                    "changes"
                ]
            )

            for (
                key,
                value,
            ) in (
                changes.items()
            ):

                setattr(
                    department,
                    key,
                    value,
                )

            session.flush()

            stats[
                "updated"
            ] += 1

            print(
                (
                    "  UPDATED | "
                    f"{registry.name} | "
                    f"id="
                    f"{department.department_id}"
                )
            )

            continue

        stats[
            "kept"
        ] += 1

    return stats


# ============================================================
# 최종 검증
# ============================================================

def verify_after_sync(
    session: Session,
    *,
    university_id: int,
) -> None:

    found_ids: set[
        int
    ] = set()

    problems: list[
        str
    ] = []

    for registry in (
        MEDICAL_DEPARTMENTS
    ):

        department = (
            find_department(
                session,

                university_id=(
                    university_id
                ),

                name=(
                    registry.name
                ),
            )
        )

        if department is None:

            problems.append(
                (
                    f"{registry.name}: "
                    "없음"
                )
            )

            continue

        found_ids.add(
            department.department_id
        )

        if not department.is_active:

            problems.append(
                (
                    f"{registry.name}: "
                    "비활성"
                )
            )

        if (
            department.college_name
            != registry.college
        ):

            problems.append(
                (
                    f"{registry.name}: "
                    "college 불일치 "
                    f"({department.college_name} / "
                    f"{registry.college})"
                )
            )

    if (
        len(
            found_ids
        )
        != 18
    ):

        problems.append(
            (
                "고유 Department ID가 "
                f"18개가 아님: "
                f"{len(found_ids)}"
            )
        )

    if problems:

        raise RuntimeError(
            (
                "동기화 후 검증 실패:\n- "
                + "\n- ".join(
                    problems
                )
            )
        )


# ============================================================
# 실행
# ============================================================

def run(
    *,
    apply: bool,
) -> None:

    print(
        "=" * 76
    )

    print(
        (
            "CareerQuest - "
            "메디컬캠퍼스 Department "
            "공식 Registry Sync"
        )
    )

    print(
        "=" * 76
    )

    print(
        (
            "공식 기준: "
            f"{OFFICIAL_SOURCE_URL}"
        )
    )

    print(
        (
            "공식 페이지 최종수정일: "
            f"{OFFICIAL_SOURCE_LAST_MODIFIED}"
        )
    )

    with Session(
        engine
    ) as session:

        university = (
            find_konyang_university(
                session
            )
        )

        print()
        print(
            (
                "대상 University: "
                f"id={university.university_id} | "
                f"{university.name} | "
                f"campus="
                f"{university.campus_name or '-'}"
            )
        )

        plan = (
            build_sync_plan(
                session,

                university=(
                    university
                ),
            )
        )

        print_plan(
            plan
        )

        counts = Counter(
            item[
                "action"
            ]
            for item
            in plan
        )

        print()
        print(
            "[계획 요약]"
        )

        print(
            (
                "  CREATE: "
                f"{counts.get('CREATE', 0)}"
            )
        )

        print(
            (
                "  UPDATE: "
                f"{counts.get('UPDATE', 0)}"
            )
        )

        print(
            (
                "  KEEP: "
                f"{counts.get('KEEP', 0)}"
            )
        )

        # ----------------------------------------------------
        # Dry Run
        # ----------------------------------------------------

        if not apply:

            print()
            print(
                "[DRY RUN]"
            )

            print(
                (
                    "DB에는 아무 변경도 하지 않았습니다."
                )
            )

            print(
                (
                    "내용이 맞으면 "
                    "--apply를 붙여 실행하세요."
                )
            )

            session.rollback()

            return

        # ----------------------------------------------------
        # 실제 반영
        # ----------------------------------------------------

        try:

            print()
            print(
                "[DB 적용]"
            )

            stats = (
                apply_sync_plan(
                    session,

                    university=(
                        university
                    ),

                    plan=(
                        plan
                    ),
                )
            )

            verify_after_sync(
                session,

                university_id=(
                    university.university_id
                ),
            )

            session.commit()

        except Exception:

            session.rollback()

            print()
            print(
                (
                    "오류 발생 -> "
                    "전체 Department Sync rollback"
                )
            )

            raise

    print()
    print(
        "=" * 76
    )

    print(
        "[동기화 완료]"
    )

    print(
        "=" * 76
    )

    print(
        (
            f"신규: "
            f"{stats['created']}개"
        )
    )

    print(
        (
            f"갱신: "
            f"{stats['updated']}개"
        )
    )

    print(
        (
            f"변경 없음: "
            f"{stats['kept']}개"
        )
    )

    print()
    print(
        (
            "메디컬캠퍼스 18개 학과 "
            "존재/활성/소속 검증 통과"
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
        "--apply",
        action="store_true",
        help=(
            "실제로 DB에 반영합니다. "
            "미지정 시 dry-run."
        ),
    )

    return (
        parser.parse_args()
    )


def main(
) -> None:

    args = (
        parse_args()
    )

    run(
        apply=(
            args.apply
        )
    )


if __name__ == "__main__":

    main()