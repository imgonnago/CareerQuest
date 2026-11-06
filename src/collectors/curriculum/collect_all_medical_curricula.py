from __future__ import annotations

import argparse
import json
import time

from dataclasses import (
    asdict,
    dataclass,
)

from src.collectors.curriculum.data_medicine_curriculum import (
    fetch_data_medicine_curriculum,
)

from src.collectors.curriculum.konyang_curriculum import (
    CurriculumSource,
    local_audit,
    parse_curriculum_pdf,
    validate_parser_result,
)

from src.collectors.curriculum.curriculum_pdf_resolver import (
    resolve_curriculum_pdf,
)

from src.collectors.curriculum.konyang_curriculum_index import (
    CENTRAL_CURRICULUM_URL,
    build_medical_curriculum_sources,
    fetch_central_curriculum_rows,
)


DEFAULT_YEAR = 2026

DEFAULT_DELAY_SECONDS = 1.0


@dataclass
class DepartmentDryRunResult:

    department_name: str

    strategy: str

    status: str

    source_url: (
        str
        | None
    )

    course_count: int = 0

    unique_code_count: int = 0

    year_level_count: int = 0

    semester_count: int = 0

    completion_type_count: int = 0

    credits_count: int = 0

    audit_code_count: (
        int
        | None
    ) = None

    code_overlap_count: (
        int
        | None
    ) = None

    note: (
        str
        | None
    ) = None

    error: (
        str
        | None
    ) = None


def safe_file_name(
    department_name: str,
    year: int,
) -> str:

    return (
        f"konyang_{year}_"
        f"{department_name}_curriculum.pdf"
    )


def basic_pdf_validation_errors(
    validation: dict,
) -> list[str]:

    """
    인공지능학과용 세부 메타데이터 기준을
    다른 학과에 강제로 적용하지 않는다.

    전체 학과 Dry Run에서는 우선:

    - 실제 코드 존재
    - Parser와 독립 Audit 코드 수 일치
    - 코드 집합 일치
    - 중복 없음

    만을 공통 필수 기준으로 사용한다.

    학년/학기/이수구분/학점 확보율은
    결과표에서 별도로 확인한다.
    """

    errors: list[
        str
    ] = []

    parser_course_count = int(
        validation.get(
            "parser_course_count",
            0,
        )
        or 0
    )

    parser_code_count = int(
        validation.get(
            "parser_code_count",
            0,
        )
        or 0
    )

    audit_code_count = int(
        validation.get(
            "audit_code_count",
            0,
        )
        or 0
    )

    overlap_count = int(
        validation.get(
            "overlap_count",
            0,
        )
        or 0
    )

    if (
        audit_code_count
        <= 0
    ):

        errors.append(
            (
                "공식 PDF에서 교과목 코드를 "
                "한 건도 찾지 못함"
            )
        )

    if (
        parser_course_count
        <= 0
    ):

        errors.append(
            (
                "Parser가 교과목을 "
                "한 건도 만들지 못함"
            )
        )

    if (
        parser_code_count
        != audit_code_count
    ):

        errors.append(
            (
                "Parser/Audit 고유 코드 수 불일치: "
                f"{parser_code_count}/"
                f"{audit_code_count}"
            )
        )

    if (
        overlap_count
        != audit_code_count
    ):

        errors.append(
            (
                "Parser/Audit 코드 집합 불일치: "
                f"{overlap_count}/"
                f"{audit_code_count}"
            )
        )

    if (
        validation.get(
            "missing_in_parser"
        )
    ):

        errors.append(
            (
                "Parser 누락 코드: "
                f"{validation['missing_in_parser']}"
            )
        )

    if (
        validation.get(
            "parser_only"
        )
    ):

        errors.append(
            (
                "Parser에만 있는 코드: "
                f"{validation['parser_only']}"
            )
        )

    if (
        validation.get(
            "duplicate_codes"
        )
    ):

        errors.append(
            (
                "중복 교과목 코드: "
                f"{validation['duplicate_codes']}"
            )
        )

    return errors


def analyze_pdf_department(
    *,
    department_name: str,
    year: int,
    source_url: str,
) -> DepartmentDryRunResult:

    source = (
        CurriculumSource(
            page_url=(
                CENTRAL_CURRICULUM_URL
            ),

            pdf_url=(
                source_url
            ),

            file_name=(
                safe_file_name(
                    department_name,
                    year,
                )
            ),

            department_name=(
                department_name
            ),

            curriculum_year=(
                year
            ),
        )
    )

    resolved = (
        resolve_curriculum_pdf(
            department_name=(
                department_name
            ),

            year=(
                year
            ),

            central_url=(
                source_url
            ),
        )
    )

    pdf_bytes = (
        resolved.data
    )

    # 실제로 성공한 최종 PDF URL을
    # Source에 기록한다.
    source.pdf_url = (
        resolved.pdf_url
    )

    source.page_url = (
        resolved.page_url
    )

    if (
            resolved.method
            != "CENTRAL_DIRECT"
    ):
        print(
            (
                "  PDF source fallback: "
                f"{resolved.method}"
            )
        )

        print(
            (
                "  resolved URL: "
                f"{resolved.pdf_url}"
            )
        )

    audit = (
        local_audit(
            pdf_bytes
        )
    )

    (
        courses,
        _diagnostics,
    ) = (
        parse_curriculum_pdf(
            pdf_bytes,
            source,
            audit,
        )
    )

    validation = (
        validate_parser_result(
            courses,
            audit,
            source,
        )
    )

    metadata_counts = (
        validation.get(
            "metadata_counts"
        )
        or {}
    )

    errors = (
        basic_pdf_validation_errors(
            validation
        )
    )

    return (
        DepartmentDryRunResult(
            department_name=(
                department_name
            ),

            strategy=(
                "CENTRAL_PDF"
            ),

            status=(
                "OK_PDF"

                if not errors

                else (
                    "CHECK_PDF"
                )
            ),

            source_url=(
                source_url
            ),

            course_count=int(
                validation.get(
                    "parser_course_count",
                    len(
                        courses
                    ),
                )
                or 0
            ),

            unique_code_count=int(
                validation.get(
                    "parser_code_count",
                    0,
                )
                or 0
            ),

            year_level_count=int(
                metadata_counts.get(
                    "year_level",
                    0,
                )
                or 0
            ),

            semester_count=int(
                metadata_counts.get(
                    "semester",
                    0,
                )
                or 0
            ),

            completion_type_count=int(
                metadata_counts.get(
                    "completion_type",
                    0,
                )
                or 0
            ),

            credits_count=int(
                metadata_counts.get(
                    "credits",
                    0,
                )
                or 0
            ),

            audit_code_count=int(
                validation.get(
                    "audit_code_count",
                    0,
                )
                or 0
            ),

            code_overlap_count=int(
                validation.get(
                    "overlap_count",
                    0,
                )
                or 0
            ),

            note=(
                "; ".join(
                    errors
                )

                if errors

                else None
            ),
        )
    )


def analyze_html_department(
    *,
    department_name: str,
    year: int,
    source_url: str,
) -> DepartmentDryRunResult:

    if (
        department_name
        != "데이터의학과"
    ):

        return (
            DepartmentDryRunResult(
                department_name=(
                    department_name
                ),

                strategy=(
                    "HTML"
                ),

                status=(
                    "CHECK_HTML"
                ),

                source_url=(
                    source_url
                ),

                note=(
                    "현재 HTML 전용 Parser는 "
                    "데이터의학과만 지원함"
                ),
            )
        )

    parsed = (
        fetch_data_medicine_curriculum(
            target_year=(
                year
            )
        )
    )

    courses = (
        parsed.courses
    )

    return (
        DepartmentDryRunResult(
            department_name=(
                department_name
            ),

            strategy=(
                "HTML"
            ),

            status=(
                "OK_HTML"
            ),

            source_url=(
                parsed.source_url
            ),

            course_count=(
                len(
                    courses
                )
            ),

            unique_code_count=0,

            year_level_count=sum(
                item.get(
                    "year_level"
                )
                is not None

                for item
                in courses
            ),

            semester_count=sum(
                item.get(
                    "semester"
                )
                is not None

                for item
                in courses
            ),

            completion_type_count=sum(
                item.get(
                    "completion_type"
                )
                is not None

                for item
                in courses
            ),

            credits_count=sum(
                item.get(
                    "credits"
                )
                is not None

                for item
                in courses
            ),

            note=(
                "공식 HTML에는 교과목 코드/"
                "학점/이수구분이 표시되지 않아 "
                "임의 생성하지 않음"
            ),
        )
    )


def select_sources(
    sources,
    only_departments: (
        set[str]
        | None
    ),
):

    if not only_departments:

        return list(
            sources
        )

    return [
        source

        for source
        in sources

        if (
            source.department_name
            in only_departments
        )
    ]


def run_batch(
    *,
    year: int,
    delay_seconds: float,
    only_departments: (
        set[str]
        | None
    ) = None,
) -> list[
    DepartmentDryRunResult
]:

    print(
        "=" * 78
    )

    print(
        (
            "CareerQuest - "
            "건양대학교 메디컬캠퍼스 "
            f"{year} 교육과정 전체 Dry Run"
        )
    )

    print(
        "=" * 78
    )

    central_rows = (
        fetch_central_curriculum_rows(
            target_year=(
                year
            )
        )
    )

    sources = (
        build_medical_curriculum_sources(
            target_year=(
                year
            ),

            central_rows=(
                central_rows
            ),
        )
    )

    sources = (
        select_sources(
            sources,
            only_departments,
        )
    )

    results: list[
        DepartmentDryRunResult
    ] = []

    total = (
        len(
            sources
        )
    )

    for (
        index,
        source,
    ) in enumerate(
        sources,
        start=1,
    ):

        print(
            (
                f"\n[{index:02d}/"
                f"{total:02d}] "
                f"{source.department_name} "
                f"({source.strategy})"
            )
        )

        # ----------------------------------------------------
        # 아직 자동 소스가 확정되지 않은 학과
        # ----------------------------------------------------

        if (
            source.status
            == "NEEDS_REVIEW"
        ):

            result = (
                DepartmentDryRunResult(
                    department_name=(
                        source.department_name
                    ),

                    strategy=(
                        source.strategy
                    ),

                    status=(
                        "SKIPPED_REVIEW"
                    ),

                    source_url=(
                        source.source_url
                    ),

                    note=(
                        source.note
                    ),
                )
            )

            results.append(
                result
            )

            print(
                "  -> SKIPPED_REVIEW"
            )

            continue

        # ----------------------------------------------------
        # URL 자체가 없는 비정상 상태
        # ----------------------------------------------------

        if not source.source_url:

            result = (
                DepartmentDryRunResult(
                    department_name=(
                        source.department_name
                    ),

                    strategy=(
                        source.strategy
                    ),

                    status=(
                        "FAILED_SOURCE"
                    ),

                    source_url=None,

                    note=(
                        source.note
                    ),

                    error=(
                        "source_url 없음"
                    ),
                )
            )

            results.append(
                result
            )

            print(
                "  -> FAILED_SOURCE"
            )

            continue

        # ----------------------------------------------------
        # 실제 Dry Run
        #
        # 한 학과가 실패해도 전체 중단하지 않는다.
        # ----------------------------------------------------

        try:

            if (
                source.strategy
                == "CENTRAL_PDF"
            ):

                result = (
                    analyze_pdf_department(
                        department_name=(
                            source.department_name
                        ),

                        year=(
                            year
                        ),

                        source_url=(
                            source.source_url
                        ),
                    )
                )

            elif (
                source.strategy
                == "HTML"
            ):

                result = (
                    analyze_html_department(
                        department_name=(
                            source.department_name
                        ),

                        year=(
                            year
                        ),

                        source_url=(
                            source.source_url
                        ),
                    )
                )

            else:

                result = (
                    DepartmentDryRunResult(
                        department_name=(
                            source.department_name
                        ),

                        strategy=(
                            source.strategy
                        ),

                        status=(
                            "SKIPPED_UNKNOWN"
                        ),

                        source_url=(
                            source.source_url
                        ),

                        note=(
                            "지원하지 않는 strategy: "
                            f"{source.strategy}"
                        ),
                    )
                )

        except Exception as exc:

            result = (
                DepartmentDryRunResult(
                    department_name=(
                        source.department_name
                    ),

                    strategy=(
                        source.strategy
                    ),

                    status=(
                        "FAILED"
                    ),

                    source_url=(
                        source.source_url
                    ),

                    error=(
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                )
            )

        results.append(
            result
        )

        print(
            (
                f"  -> "
                f"{result.status}"
                f" | courses="
                f"{result.course_count}"
                f" | codes="
                f"{result.unique_code_count}"
                f" | year="
                f"{result.year_level_count}"
                f" | semester="
                f"{result.semester_count}"
                f" | type="
                f"{result.completion_type_count}"
                f" | credits="
                f"{result.credits_count}"
            )
        )

        if result.note:

            print(
                (
                    "     note: "
                    f"{result.note}"
                )
            )

        if result.error:

            print(
                (
                    "     error: "
                    f"{result.error}"
                )
            )

        # ----------------------------------------------------
        # 공식 서버 요청 간격
        # ----------------------------------------------------

        if (
            source.strategy
            == "CENTRAL_PDF"

            and delay_seconds
            > 0
        ):

            time.sleep(
                delay_seconds
            )

    print_batch_summary(
        results
    )

    return results


def print_batch_summary(
    results: list[
        DepartmentDryRunResult
    ],
) -> None:

    print(
        "\n"
        + "=" * 78
    )

    print(
        "[전체 Dry Run 요약]"
    )

    print(
        "=" * 78
    )

    for item in (
        results
    ):

        print(
            (
                f"{item.department_name:<12}"
                f" | {item.status:<14}"
                f" | 과목 "
                f"{item.course_count:>3}"
                f" | 코드 "
                f"{item.unique_code_count:>3}"
                f" | 학년 "
                f"{item.year_level_count:>3}"
                f" | 학기 "
                f"{item.semester_count:>3}"
                f" | 구분 "
                f"{item.completion_type_count:>3}"
                f" | 학점 "
                f"{item.credits_count:>3}"
            )
        )

    status_counts: dict[
        str,
        int
    ] = {}

    for item in results:

        status_counts[
            item.status
        ] = (
            status_counts.get(
                item.status,
                0,
            )
            + 1
        )

    print(
        "\n[상태 집계]"
    )

    for (
        status,
        count,
    ) in sorted(
        status_counts.items()
    ):

        print(
            (
                f"  {status}: "
                f"{count}"
            )
        )

    check_items = [
        item

        for item
        in results

        if (
            item.status
            not in {
                "OK_PDF",
                "OK_HTML",
                "SKIPPED_REVIEW",
            }
        )
    ]

    if check_items:

        print(
            "\n[추가 확인 필요한 학과]"
        )

        for item in (
            check_items
        ):

            print(
                (
                    f"  - "
                    f"{item.department_name}: "
                    f"{item.status}"
                )
            )

            if item.note:

                print(
                    (
                        "      note="
                        f"{item.note}"
                    )
                )

            if item.error:

                print(
                    (
                        "      error="
                        f"{item.error}"
                    )
                )

    else:

        print(
            (
                "\nPDF/HTML 자동 처리 대상은 "
                "모두 기본 검증을 통과했습니다."
            )
        )


def parse_only_departments(
    raw: (
        str
        | None
    ),
) -> (
    set[str]
    | None
):

    if not raw:
        return None

    result = {
        item.strip()

        for item
        in raw.split(
            ","
        )

        if item.strip()
    }

    return (
        result
        or None
    )


def parse_args(
) -> argparse.Namespace:

    parser = (
        argparse.ArgumentParser()
    )

    parser.add_argument(
        "--year",

        type=int,

        default=(
            DEFAULT_YEAR
        ),
    )

    parser.add_argument(
        "--delay",

        type=float,

        default=(
            DEFAULT_DELAY_SECONDS
        ),

        help=(
            "학과 PDF 요청 사이 대기 초. "
            "기본 1.0초."
        ),
    )

    parser.add_argument(
        "--only",

        default=None,

        help=(
            "일부 학과만 실행. "
            '예: --only "의학과,간호학과"'
        ),
    )

    parser.add_argument(
        "--json",

        action="store_true",
    )

    return (
        parser.parse_args()
    )


def main(
) -> None:

    args = (
        parse_args()
    )

    results = (
        run_batch(
            year=(
                args.year
            ),

            delay_seconds=max(
                0.0,
                args.delay,
            ),

            only_departments=(
                parse_only_departments(
                    args.only
                )
            ),
        )
    )

    if args.json:

        print(
            "\n[JSON]"
        )

        print(
            json.dumps(
                [
                    asdict(
                        item
                    )

                    for item
                    in results
                ],

                ensure_ascii=False,

                indent=2,
            )
        )


if __name__ == "__main__":

    main()