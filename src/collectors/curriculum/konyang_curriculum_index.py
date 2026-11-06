from __future__ import annotations

import argparse
import json
import re

from dataclasses import (
    asdict,
    dataclass,
)

from typing import Any

from urllib.parse import (
    parse_qs,
    unquote,
    urljoin,
    urlparse,
)

import requests

from bs4 import (
    BeautifulSoup,
    Tag,
)

from src.collectors.curriculum.medical_departments import (
    MEDICAL_DEPARTMENTS,
    MedicalDepartment,
)


# ============================================================
# 설정
# ============================================================

CENTRAL_CURRICULUM_URL = (
    "https://sec.konyang.ac.kr/"
    "prog/curriculum/kor/"
    "sub06_01_04_04/list.do"
)

DEFAULT_YEAR = 2026

HTTP_TIMEOUT = 30

USER_AGENT = (
    "CareerQuestAI-AcademicProject/0.1 "
    "(university project; "
    "low-frequency official curriculum fetch)"
)


# ============================================================
# 구조
# ============================================================

@dataclass
class CentralCurriculumRow:

    year: int

    category: str

    title: str

    source_url: (
        str
        | None
    )

    raw_action: (
        str
        | None
    )

    row_text: str


@dataclass
class MedicalCurriculumSource:

    department_name: str

    college: str

    year: int

    strategy: str

    status: str

    source_url: (
        str
        | None
    )

    central_title: (
        str
        | None
    ) = None

    central_category: (
        str
        | None
    ) = None

    note: (
        str
        | None
    ) = None


# ============================================================
# 문자열
# ============================================================

def clean_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    return (
        re.sub(
            r"\s+",
            " ",
            str(
                value
            ),
        )
        .strip()
    )


def compact_text(
    value: Any,
) -> str:

    return (
        re.sub(
            r"\s+",
            "",
            clean_text(
                value
            ),
        )
    )


# ============================================================
# HTTP
# ============================================================

def create_http_session(
) -> requests.Session:

    session = (
        requests.Session()
    )

    session.headers.update(
        {
            "User-Agent":
                USER_AGENT,

            "Accept-Language":
                "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )

    return (
        session
    )


# ============================================================
# 다운로드 링크 추출
# ============================================================

def extract_pdf_from_viewer(
    url: str,
    *,
    base_url: str,
) -> (
    str
    | None
):

    absolute = (
        urljoin(
            base_url,
            url,
        )
    )

    parsed = (
        urlparse(
            absolute
        )
    )

    # 직접 PDF
    if (
        parsed.path
        .lower()
        .endswith(
            ".pdf"
        )
    ):

        return (
            absolute
        )

    # PDF.js
    query = (
        parse_qs(
            parsed.query
        )
    )

    for file_value in (
        query.get(
            "file",
            [],
        )
    ):

        decoded = (
            unquote(
                file_value
            )
        )

        if (
            ".pdf"
            in decoded.lower()
        ):

            return (
                urljoin(
                    absolute,
                    decoded,
                )
            )

    return None


def looks_like_download_url(
    value: str,
) -> bool:

    lowered = (
        value.lower()
    )

    keywords = (
        ".pdf",
        "download",
        "filedown",
        "file_down",
        "filedownload",
        "attach",
        "atch",
        "downfile",
        "getfile",
        "viewer.html?file=",
        "curriculum",
    )

    return any(
        keyword
        in lowered

        for keyword
        in keywords
    )


def candidate_urls_from_blob(
    value: str,
    *,
    base_url: str,
) -> list[str]:

    value = (
        clean_text(
            value
        )
    )

    if not value:
        return []

    candidates: list[
        str
    ] = []

    # --------------------------------------------------------
    # 값 자체가 URL/path일 때
    # --------------------------------------------------------

    if (
        value.startswith(
            (
                "http://",
                "https://",
                "/",
                "./",
                "../",
            )
        )

        or looks_like_download_url(
            value
        )
    ):

        candidates.append(
            value
        )

    # --------------------------------------------------------
    # onclick 등 JS 안의 따옴표 문자열
    # --------------------------------------------------------

    for match in (
        re.finditer(
            r"""['"]([^'"]+)['"]""",
            value,
        )
    ):

        token = (
            clean_text(
                match.group(
                    1
                )
            )
        )

        if not token:
            continue

        if (
            token.startswith(
                (
                    "http://",
                    "https://",
                    "/",
                    "./",
                    "../",
                )
            )

            or looks_like_download_url(
                token
            )
        ):

            candidates.append(
                token
            )

    # --------------------------------------------------------
    # 문자열 안 PDF 직접 탐색
    # --------------------------------------------------------

    for match in (
        re.finditer(
            (
                r"(?:"
                r"https?://[^\s\"'<>]+\.pdf"
                r"(?:\?[^\s\"'<>]*)?"
                r"|"
                r"/[^\s\"'<>]+\.pdf"
                r"(?:\?[^\s\"'<>]*)?"
                r")"
            ),
            value,
            flags=(
                re.IGNORECASE
            ),
        )
    ):

        candidates.append(
            match.group(
                0
            )
        )

    resolved: list[
        str
    ] = []

    for candidate in (
        candidates
    ):

        candidate = (
            candidate.strip()
        )

        if (
            candidate.lower()
            .startswith(
                "javascript:"
            )
        ):

            continue

        viewer_pdf = (
            extract_pdf_from_viewer(
                candidate,
                base_url=(
                    base_url
                ),
            )
        )

        if viewer_pdf:

            resolved.append(
                viewer_pdf
            )

            continue

        if (
            looks_like_download_url(
                candidate
            )
        ):

            resolved.append(
                urljoin(
                    base_url,
                    candidate,
                )
            )

    return list(
        dict.fromkeys(
            resolved
        )
    )


def extract_row_download(
    row: Tag,
    *,
    base_url: str,
) -> tuple[
    str | None,
    str | None,
]:

    raw_actions: list[
        str
    ] = []

    candidate_urls: list[
        str
    ] = []

    # row 내부 모든 태그 검사
    for tag in (
        row.find_all(
            True
        )
    ):

        if not isinstance(
            tag,
            Tag,
        ):

            continue

        for attribute in (
            "href",
            "src",
            "onclick",
            "data-url",
            "data-file",
            "data-href",
            "data-download",
            "value",
        ):

            value = (
                tag.get(
                    attribute
                )
            )

            if not value:
                continue

            value = (
                clean_text(
                    value
                )
            )

            raw_actions.append(
                (
                    f"{attribute}="
                    f"{value}"
                )
            )

            candidate_urls.extend(
                candidate_urls_from_blob(
                    value,
                    base_url=(
                        base_url
                    ),
                )
            )

    candidate_urls = list(
        dict.fromkeys(
            candidate_urls
        )
    )

    # --------------------------------------------------------
    # 직접 PDF 우선
    # --------------------------------------------------------

    for url in (
        candidate_urls
    ):

        if (
            urlparse(
                url
            )
            .path
            .lower()
            .endswith(
                ".pdf"
            )
        ):

            return (
                url,
                (
                    " | ".join(
                        raw_actions
                    )
                    or None
                ),
            )

    # --------------------------------------------------------
    # 그 외 공식 download endpoint
    # --------------------------------------------------------

    if candidate_urls:

        return (
            candidate_urls[
                0
            ],
            (
                " | ".join(
                    raw_actions
                )
                or None
            ),
        )

    return (
        None,
        (
            " | ".join(
                raw_actions
            )
            or None
        ),
    )


# ============================================================
# 중앙 페이지 파싱
# ============================================================

def infer_year_from_text(
    text: str,
) -> (
    int
    | None
):

    match = (
        re.search(
            (
                r"(20\d{2})\s*"
                r"(?:학년도|년도|년)"
            ),
            text,
        )
    )

    if not match:
        return None

    return int(
        match.group(
            1
        )
    )


def parse_central_curriculum_rows(
    html: str,
    *,
    target_year: int,
    base_url: str = (
        CENTRAL_CURRICULUM_URL
    ),
) -> list[
    CentralCurriculumRow
]:

    soup = (
        BeautifulSoup(
            html,
            "html.parser",
        )
    )

    rows: list[
        CentralCurriculumRow
    ] = []

    for tr in (
        soup.find_all(
            "tr"
        )
    ):

        cells = (
            tr.find_all(
                [
                    "th",
                    "td",
                ],
                recursive=False,
            )
        )

        if not cells:
            continue

        cell_texts = [
            clean_text(
                cell.get_text(
                    " ",
                    strip=True,
                )
            )

            for cell
            in cells
        ]

        row_text = (
            " | ".join(
                text

                for text
                in cell_texts

                if text
            )
        )

        if not row_text:
            continue

        year = (
            infer_year_from_text(
                row_text
            )
        )

        if (
            year
            != target_year
        ):

            continue

        # 의대 과정성과 자료는
        # 일반 교과목 교육과정이 아님
        if (
            "과정성과"
            in row_text
        ):

            continue

        if (
            "교육과정"
            not in row_text
        ):

            continue

        category = ""

        title = ""

        # 중앙 페이지 현재 구조:
        #
        # 순번 | 구분 | 제목 | 다운로드
        if (
            len(
                cell_texts
            )
            >= 3
        ):

            category = (
                cell_texts[
                    1
                ]
            )

            title = (
                cell_texts[
                    2
                ]
            )

        else:

            # 페이지 구조 변경 fallback
            title_candidates = [
                text

                for text
                in cell_texts

                if (
                    str(
                        target_year
                    )
                    in text

                    and "교육과정"
                    in text
                )
            ]

            if title_candidates:

                title = (
                    title_candidates[
                        0
                    ]
                )

        if not title:
            continue

        (
            source_url,
            raw_action,
        ) = (
            extract_row_download(
                tr,
                base_url=(
                    base_url
                ),
            )
        )

        rows.append(
            CentralCurriculumRow(
                year=(
                    target_year
                ),

                category=(
                    category
                ),

                title=(
                    title
                ),

                source_url=(
                    source_url
                ),

                raw_action=(
                    raw_action
                ),

                row_text=(
                    row_text
                ),
            )
        )

    # 동일 제목 중복 제거
    deduped: dict[
        str,
        CentralCurriculumRow,
    ] = {}

    for item in (
        rows
    ):

        key = (
            compact_text(
                item.title
            )
        )

        existing = (
            deduped.get(
                key
            )
        )

        if (
            existing
            is None
        ):

            deduped[
                key
            ] = (
                item
            )

            continue

        # 같은 행이 중복되면
        # URL 추출 성공한 쪽 우선
        if (
            existing.source_url
            is None

            and item.source_url
            is not None
        ):

            deduped[
                key
            ] = (
                item
            )

    return list(
        deduped.values()
    )


def fetch_central_curriculum_rows(
    *,
    target_year: int,
) -> list[
    CentralCurriculumRow
]:

    session = (
        create_http_session()
    )

    response = (
        session.get(
            CENTRAL_CURRICULUM_URL,
            timeout=(
                HTTP_TIMEOUT
            ),
        )
    )

    response.raise_for_status()

    if (
        response
        .apparent_encoding
    ):

        response.encoding = (
            response
            .apparent_encoding
        )

    return (
        parse_central_curriculum_rows(
            response.text,

            target_year=(
                target_year
            ),

            base_url=(
                response.url
            ),
        )
    )


# ============================================================
# 학과 ↔ 중앙 Row 매칭
# ============================================================

def department_match_score(
    department: MedicalDepartment,
    row: CentralCurriculumRow,
) -> int:

    category = (
        compact_text(
            row.category
        )
    )

    title = (
        compact_text(
            row.title
        )
    )

    best = 0

    for alias in (
        department
        .all_names
    ):

        alias_compact = (
            compact_text(
                alias
            )
        )

        if not alias_compact:
            continue

        # ----------------------------------------------------
        # 구분 칸이 정확히 학과명
        # ----------------------------------------------------

        if (
            category
            == alias_compact
        ):

            best = max(
                best,
                100,
            )

        # ----------------------------------------------------
        # 제목:
        # 2026학년도 OO학과 교육과정
        # ----------------------------------------------------

        if (
            f"{alias_compact}교육과정"
            in title
        ):

            best = max(
                best,
                90,
            )

        # 일반 fallback
        if (
            alias_compact
            in title
        ):

            best = max(
                best,
                50,
            )

    return (
        best
    )


def find_best_central_row(
    department: MedicalDepartment,
    rows: list[
        CentralCurriculumRow
    ],
) -> (
    CentralCurriculumRow
    | None
):

    scored = [
        (
            department_match_score(
                department,
                row,
            ),

            index,

            row,
        )

        for index, row
        in enumerate(
            rows
        )
    ]

    scored = [
        item

        for item
        in scored

        if (
            item[
                0
            ]
            > 0
        )
    ]

    if not scored:
        return None

    # 같은 학과가 여러 번 있으면
    # 점수 높은 항목 우선.
    #
    # 동일 점수면 목록 뒤쪽 항목 우선.
    # 일부개정본 등이 뒤에 추가되는 상황 대비.
    scored.sort(
        key=lambda item: (
            item[
                0
            ],

            item[
                1
            ],
        ),

        reverse=True,
    )

    return (
        scored[
            0
        ][
            2
        ]
    )


# ============================================================
# 메디컬캠퍼스 소스 생성
# ============================================================

def build_medical_curriculum_sources(
    *,
    target_year: int,
    central_rows: list[
        CentralCurriculumRow
    ],
) -> list[
    MedicalCurriculumSource
]:

    result: list[
        MedicalCurriculumSource
    ] = []

    for department in (
        MEDICAL_DEPARTMENTS
    ):

        # ----------------------------------------------------
        # 데이터의학과
        # ----------------------------------------------------

        if (
            department
            .source_strategy
            == "HTML"
        ):

            result.append(
                MedicalCurriculumSource(
                    department_name=(
                        department.name
                    ),

                    college=(
                        department.college
                    ),

                    year=(
                        target_year
                    ),

                    strategy=(
                        "HTML"
                    ),

                    status=(
                        "READY_HTML"
                    ),

                    source_url=(
                        department.html_url
                    ),

                    note=(
                        department.note
                    ),
                )
            )

            continue

        # ----------------------------------------------------
        # 의료인공지능학과
        # ----------------------------------------------------

        if (
            department
            .source_strategy
            == "REVIEW"
        ):

            result.append(
                MedicalCurriculumSource(
                    department_name=(
                        department.name
                    ),

                    college=(
                        department.college
                    ),

                    year=(
                        target_year
                    ),

                    strategy=(
                        "REVIEW"
                    ),

                    status=(
                        "NEEDS_REVIEW"
                    ),

                    source_url=None,

                    note=(
                        department.note
                    ),
                )
            )

            continue

        # ----------------------------------------------------
        # 중앙 PDF 대상
        # ----------------------------------------------------

        row = (
            find_best_central_row(
                department,
                central_rows,
            )
        )

        if row is None:

            result.append(
                MedicalCurriculumSource(
                    department_name=(
                        department.name
                    ),

                    college=(
                        department.college
                    ),

                    year=(
                        target_year
                    ),

                    strategy=(
                        "CENTRAL_PDF"
                    ),

                    status=(
                        "NOT_FOUND"
                    ),

                    source_url=None,

                    note=(
                        "중앙 연도별 교육과정 페이지에서 "
                        "해당 학과 행을 찾지 못했습니다."
                    ),
                )
            )

            continue

        # 행은 찾았지만 URL 추출 실패
        if (
            row.source_url
            is None
        ):

            result.append(
                MedicalCurriculumSource(
                    department_name=(
                        department.name
                    ),

                    college=(
                        department.college
                    ),

                    year=(
                        target_year
                    ),

                    strategy=(
                        "CENTRAL_PDF"
                    ),

                    status=(
                        "FOUND_NO_URL"
                    ),

                    source_url=None,

                    central_title=(
                        row.title
                    ),

                    central_category=(
                        row.category
                    ),

                    note=(
                        "중앙 목록 행은 찾았지만 "
                        "다운로드 URL을 HTML에서 "
                        "직접 복구하지 못했습니다."
                    ),
                )
            )

            continue

        result.append(
            MedicalCurriculumSource(
                department_name=(
                    department.name
                ),

                college=(
                    department.college
                ),

                year=(
                    target_year
                ),

                strategy=(
                    "CENTRAL_PDF"
                ),

                status=(
                    "READY_PDF"
                ),

                source_url=(
                    row.source_url
                ),

                central_title=(
                    row.title
                ),

                central_category=(
                    row.category
                ),
            )
        )

    return (
        result
    )


# ============================================================
# Discovery 검증
# ============================================================

def validate_discovery(
    sources: list[
        MedicalCurriculumSource
    ],
    *,
    target_year: int,
) -> list[str]:

    warnings: list[
        str
    ] = []

    if (
        len(
            sources
        )
        != 18
    ):

        warnings.append(
            (
                "메디컬캠퍼스 학과 수가 "
                "18개가 아닙니다: "
                f"{len(sources)}"
            )
        )

    if (
        target_year
        == 2026
    ):

        pdf_rows = [
            item

            for item
            in sources

            if (
                item.strategy
                == "CENTRAL_PDF"
            )
        ]

        html_rows = [
            item

            for item
            in sources

            if (
                item.strategy
                == "HTML"
            )
        ]

        review_rows = [
            item

            for item
            in sources

            if (
                item.strategy
                == "REVIEW"
            )
        ]

        if (
            len(
                pdf_rows
            )
            != 16
        ):

            warnings.append(
                (
                    "2026 중앙 PDF 대상이 "
                    "16개가 아닙니다: "
                    f"{len(pdf_rows)}"
                )
            )

        if (
            len(
                html_rows
            )
            != 1
        ):

            warnings.append(
                (
                    "2026 HTML 예외가 "
                    "1개가 아닙니다: "
                    f"{len(html_rows)}"
                )
            )

        if (
            len(
                review_rows
            )
            != 1
        ):

            warnings.append(
                (
                    "2026 REVIEW 예외가 "
                    "1개가 아닙니다: "
                    f"{len(review_rows)}"
                )
            )

    return (
        warnings
    )


# ============================================================
# 출력
# ============================================================

def print_central_rows(
    rows: list[
        CentralCurriculumRow
    ],
) -> None:

    print(
        "\n[중앙 교육과정 목록 진단]"
    )

    print(
        (
            "  전체 교육과정 행: "
            f"{len(rows)}"
        )
    )

    for (
        index,
        row,
    ) in enumerate(
        rows,
        start=1,
    ):

        url_state = (
            "URL_OK"

            if (
                row.source_url
            )

            else (
                "NO_URL"
            )
        )

        print(
            (
                f"  {index:02d}. "
                f"{row.category or '-'} | "
                f"{row.title} | "
                f"{url_state}"
            )
        )


def print_medical_sources(
    sources: list[
        MedicalCurriculumSource
    ],
) -> None:

    print(
        "\n[메디컬캠퍼스 교육과정 소스]"
    )

    for (
        index,
        item,
    ) in enumerate(
        sources,
        start=1,
    ):

        print(
            (
                f"  {index:02d}. "
                f"{item.department_name:<12} | "
                f"{item.status:<12} | "
                f"{item.strategy:<11} | "
                f"{item.source_url or '-'}"
            )
        )

    status_counts: dict[
        str,
        int,
    ] = {}

    for item in (
        sources
    ):

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
        "\n[상태 요약]"
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


def print_unresolved_diagnostics(
    sources: list[
        MedicalCurriculumSource
    ],
    central_rows: list[
        CentralCurriculumRow
    ],
) -> None:

    unresolved = [
        item

        for item
        in sources

        if (
            item.status
            in {
                "NOT_FOUND",
                "FOUND_NO_URL",
            }
        )
    ]

    if not unresolved:
        return

    print(
        "\n[미해결 중앙 PDF 진단]"
    )

    for item in (
        unresolved
    ):

        print(
            (
                f"  - "
                f"{item.department_name}: "
                f"{item.status}"
            )
        )

        department = next(
            department

            for department
            in MEDICAL_DEPARTMENTS

            if (
                department.name
                == item.department_name
            )
        )

        row = (
            find_best_central_row(
                department,
                central_rows,
            )
        )

        if row:

            print(
                (
                    "      title="
                    f"{row.title}"
                )
            )

            print(
                (
                    "      category="
                    f"{row.category}"
                )
            )

            print(
                (
                    "      raw_action="
                    f"{row.raw_action or '-'}"
                )
            )


# ============================================================
# 실행
# ============================================================

def run(
    *,
    target_year: int,
    json_output: bool = False,
) -> list[
    MedicalCurriculumSource
]:

    print(
        "=" * 72
    )

    print(
        (
            "CareerQuest - "
            "건양대학교 메디컬캠퍼스 "
            "교육과정 소스 탐색"
        )
    )

    print(
        "=" * 72
    )

    # --------------------------------------------------------
    # 1
    # --------------------------------------------------------

    print(
        (
            "\n[1/3] 중앙 연도별 교육과정 "
            f"페이지 조회: {target_year}"
        )
    )

    central_rows = (
        fetch_central_curriculum_rows(
            target_year=(
                target_year
            ),
        )
    )

    print_central_rows(
        central_rows
    )

    # --------------------------------------------------------
    # 2
    # --------------------------------------------------------

    print(
        (
            "\n[2/3] "
            "메디컬캠퍼스 18개 학과와 매칭"
        )
    )

    sources = (
        build_medical_curriculum_sources(
            target_year=(
                target_year
            ),

            central_rows=(
                central_rows
            ),
        )
    )

    print_medical_sources(
        sources
    )

    print_unresolved_diagnostics(
        sources,
        central_rows,
    )

    # --------------------------------------------------------
    # 3
    # --------------------------------------------------------

    print(
        "\n[3/3] Discovery 검증"
    )

    warnings = (
        validate_discovery(
            sources,
            target_year=(
                target_year
            ),
        )
    )

    if warnings:

        print(
            "  경고:"
        )

        for warning in (
            warnings
        ):

            print(
                f"    - {warning}"
            )

    else:

        print(
            (
                "  Registry 구조 검증 통과 "
                "(18개 학과)"
            )
        )

    if json_output:

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
                    in sources
                ],

                ensure_ascii=False,

                indent=2,
            )
        )

    return (
        sources
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
        "--year",

        type=int,

        default=(
            DEFAULT_YEAR
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

    run(
        target_year=(
            args.year
        ),

        json_output=(
            args.json
        ),
    )


if __name__ == "__main__":

    main()