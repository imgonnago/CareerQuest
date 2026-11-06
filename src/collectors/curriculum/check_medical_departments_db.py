from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.collectors.curriculum.medical_departments import (
    MEDICAL_DEPARTMENTS,
)
from src.db.database import engine
from src.db.models import Department
from src.loaders.course_loader import (
    normalize_name,
    resolve_department,
)


def main() -> None:

    print(
        "=" * 76
    )

    print(
        "CareerQuest - 메디컬캠퍼스 Department DB Preflight"
    )

    print(
        "=" * 76
    )

    with Session(
        engine
    ) as session:

        all_departments = list(
            session.scalars(
                select(
                    Department
                )
                .order_by(
                    Department.name,
                    Department.department_id,
                )
            ).all()
        )

        print()
        print(
            f"DB 전체 Department: "
            f"{len(all_departments)}개"
        )

        # ----------------------------------------------------
        # 정규화 이름 기준 중복 진단
        # ----------------------------------------------------

        normalized_groups: dict[
            str,
            list[Department],
        ] = defaultdict(
            list
        )

        for department in (
            all_departments
        ):

            normalized_groups[
                normalize_name(
                    department.name
                )
            ].append(
                department
            )

        # ----------------------------------------------------
        # 메디컬캠퍼스 18개 확인
        # ----------------------------------------------------

        print()
        print(
            "[메디컬캠퍼스 학과 확인]"
        )

        found_count = 0

        missing: list[
            str
        ] = []

        inactive: list[
            str
        ] = []

        ambiguous: list[
            str
        ] = []

        resolved_ids: set[
            int
        ] = set()

        for (
            index,
            target,
        ) in enumerate(
            MEDICAL_DEPARTMENTS,
            start=1,
        ):

            try:

                resolved = (
                    resolve_department(
                        session,
                        target.name,
                    )
                )

            except LookupError:

                print(
                    (
                        f"{index:02d}. "
                        f"{target.name:<12} "
                        "| MISSING"
                    )
                )

                missing.append(
                    target.name
                )

                continue

            found_count += 1

            resolved_ids.add(
                resolved.department_id
            )

            if not resolved.is_active:

                inactive.append(
                    target.name
                )

            same_name_matches = (
                normalized_groups.get(
                    normalize_name(
                        target.name
                    ),
                    [],
                )
            )

            if (
                len(
                    same_name_matches
                )
                > 1
            ):

                ambiguous.append(
                    target.name
                )

            print(
                (
                    f"{index:02d}. "
                    f"{target.name:<12} "
                    f"| OK "
                    f"| id={resolved.department_id} "
                    f"| DB명={resolved.name} "
                    f"| 단과대="
                    f"{resolved.college_name or '-'} "
                    f"| active={resolved.is_active}"
                )
            )

        # ----------------------------------------------------
        # 결과
        # ----------------------------------------------------

        print()
        print(
            "=" * 76
        )

        print(
            "[Preflight 요약]"
        )

        print(
            "=" * 76
        )

        print(
            (
                f"대상 학과: "
                f"{len(MEDICAL_DEPARTMENTS)}개"
            )
        )

        print(
            (
                f"찾음: "
                f"{found_count}개"
            )
        )

        print(
            (
                f"고유 Department ID: "
                f"{len(resolved_ids)}개"
            )
        )

        print(
            (
                f"누락: "
                f"{len(missing)}개"
            )
        )

        print(
            (
                f"비활성: "
                f"{len(inactive)}개"
            )
        )

        print(
            (
                f"동명이름 후보: "
                f"{len(ambiguous)}개"
            )
        )

        if missing:

            print()
            print(
                "[누락 학과]"
            )

            for name in missing:

                print(
                    f"  - {name}"
                )

        if inactive:

            print()
            print(
                "[비활성 학과]"
            )

            for name in inactive:

                print(
                    f"  - {name}"
                )

        if ambiguous:

            print()
            print(
                "[동일 이름 Department가 여러 건 존재]"
            )

            for name in ambiguous:

                matches = (
                    normalized_groups[
                        normalize_name(
                            name
                        )
                    ]
                )

                print(
                    f"  - {name}"
                )

                for department in (
                    matches
                ):

                    print(
                        (
                            "      "
                            f"id={department.department_id}, "
                            f"name={department.name}, "
                            f"college="
                            f"{department.college_name or '-'}, "
                            f"survey_year="
                            f"{department.survey_year or '-'}, "
                            f"active={department.is_active}"
                        )
                    )

        print()

        if (
            not missing
            and found_count
            == len(
                MEDICAL_DEPARTMENTS
            )
            and len(
                resolved_ids
            )
            == len(
                MEDICAL_DEPARTMENTS
            )
        ):

            print(
                "[PASS]"
            )

            print(
                (
                    "메디컬캠퍼스 18개 학과 모두 "
                    "DB Department와 연결 가능."
                )
            )

            print(
                (
                    "→ 교육과정 COURSE 일괄 적재 "
                    "단계로 진행 가능"
                )
            )

        else:

            print(
                "[FAIL]"
            )

            print(
                (
                    "Department DB를 먼저 보완해야 "
                    "안전하게 교육과정을 적재할 수 있습니다."
                )
            )


if __name__ == "__main__":
    main()