from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import pymupdf
import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from src.db.database import engine
from src.loaders.course_loader import (
    upsert_course_record,
    upsert_curriculum_document,
)


# ============================================================
# 설정
# ============================================================

KONYANG_AI_CURRICULUM_PAGE = (
    "https://ai.konyang.ac.kr/prog/curriculum/ai/sub01_05/view.do"
)

FALLBACK_PDF_URL_2026_AI = (
    "https://ai.konyang.ac.kr/uploads_clone/curriculum/"
    "STB_202610201036110470.pdf"
)

DEFAULT_DEPARTMENT = "인공지능학과"
DEFAULT_CURRICULUM_YEAR = 2026
DEFAULT_REVISION_TYPE = "BASE"

HTTP_TIMEOUT = 30

USER_AGENT = (
    "CareerQuestAI-AcademicProject/0.1 "
    "(university project)"
)

STRICT_COURSE_CODE_RE = re.compile(
    r"^\d{5}[A-Za-z]$"
)

COURSE_CODE_FIND_RE = re.compile(
    r"(?<![0-9A-Za-z])(\d{5}[A-Za-z])(?![0-9A-Za-z])"
)

EXPECTED_COURSE_COUNTS: dict[
    tuple[int, str],
    int,
] = {
    (
        2026,
        "인공지능학과",
    ):
        66,
}

# 2026 인공지능학과 PDF 기준
#
# 교양 25개 + 전공 33개 = 58개
#   → 학기 / 이수구분 존재
#
# 현장실습 8개
#   → 학기 / 이수구분이 없을 수 있음
EXPECTED_METADATA_MINIMUMS: dict[
    tuple[int, str],
    dict[str, int],
] = {
    (
        2026,
        "인공지능학과",
    ): {
        "year_level": 66,
        "semester": 58,
        "completion_type": 58,
        "credits": 66,
    },
}

BLOCKED_NAMES = {
    "합계",
    "소계",
    "계",
    "총계",
    "이수학점",
    "졸업학점",
    "총학점",
    "학점계",
    "교과목",
    "교과목명",
    "과목명",
}

INVALID_COMPLETION_TYPES = {
    "계",
    "합계",
    "소계",
    "총계",
    "학점계",
    "이수학점",
    "졸업학점",
    "이수구분",
    "교과영역",
    "이수영역",
    "영역",
    "구분",
    "이수시기",
    "방학",
}

HEADER_ALIASES: dict[
    str,
    tuple[str, ...],
] = {
    "year_level": (
        "학년",
        "이수학년",
    ),

    "semester": (
        "학기",
        "이수학기",
    ),

    "completion_type": (
        "이수구분",
        "교과영역",
        "이수영역",
        "영역구분",
        "교과구분",
    ),

    "course_code": (
        "교과목코드",
        "교과목번호",
        "과목코드",
        "교과코드",
        "교과번호",
    ),

    "name": (
        "교과목명",
        "교과목명칭",
        "과목명",
    ),

    "credits": (
        "학점",
    ),

    "theory_hours": (
        "이론",
        "이론시수",
        "이론시간",
    ),

    "practice_hours": (
        "실습",
        "실습시수",
        "실습시간",
    ),
}


# ============================================================
# 데이터 구조
# ============================================================

@dataclass
class CurriculumSource:

    page_url: str

    pdf_url: str

    file_name: str

    department_name: str

    curriculum_year: int

    revision_type: str = (
        DEFAULT_REVISION_TYPE
    )


@dataclass
class ParseState:

    year_level: (
        int | None
    ) = None

    semester: (
        str | None
    ) = None

    completion_type: (
        str | None
    ) = None


@dataclass
class TableDiagnostic:

    page_no: int

    table_index: int

    row_count: int

    header_rows: list[
        int
    ] = field(
        default_factory=list
    )

    # PyMuPDF Header만 보고 처음 판단한 결과
    header_original: dict[
        str,
        int,
    ] = field(
        default_factory=dict
    )

    # 실제 데이터 분포까지 보고 보정한 결과
    header_map: dict[
        str,
        int,
    ] = field(
        default_factory=dict
    )

    code_count: int = 0

    seen_years: set[
        int
    ] = field(
        default_factory=set
    )

    seen_semesters: set[
        str
    ] = field(
        default_factory=set
    )

    seen_completion_types: set[
        str
    ] = field(
        default_factory=set
    )


# ============================================================
# 문자열 / 숫자
# ============================================================

def clean_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(
            value
        ),
    ).strip()


def compact_text(
    value: Any,
) -> str:

    return (
        clean_text(
            value
        )
        .replace(
            " ",
            "",
        )
        .replace(
            "\n",
            "",
        )
    )


def optional_text(
    value: Any,
) -> str | None:

    value = (
        clean_text(
            value
        )
    )

    return (
        value
        or None
    )


def normalize_name(
    value: Any,
) -> str:

    # PDF_GLYPH_SGD_2026
    value = re.sub(
        r"SGD\s*柴\s*(?=지역사회문제해결)",
        "SGD·",
        clean_text(value),
    )

    if not value:
        return None

    if (
        compact_text(
            value
        )
        in BLOCKED_NAMES
    ):

        return None

    if (
        STRICT_COURSE_CODE_RE
        .fullmatch(
            value
        )
    ):

        return None


    return value


def normalize_year_level(
    value: Any,
) -> int | None:

    text = (
        compact_text(
            value
        )
    )

    if not text:
        return None

    if text in {
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
    }:

        return int(
            text
        )

    match = (
        re.fullmatch(
            r"([1-6])학년",
            text,
        )
    )

    if match:

        return int(
            match.group(
                1
            )
        )

    return None


def normalize_semester(
    value: Any,
) -> str | None:

    text = (
        compact_text(
            value
        )
    )

    if not text:
        return None

    if text in {
        "학기",
        "이수학기",
        "학기구분",
    }:

        return None

    if text in {
        "1",
        "1.0",
        "1학기",
    }:

        return "1"

    if text in {
        "2",
        "2.0",
        "2학기",
    }:

        return "2"

    if text in {
        "하계",
        "하계학기",
        "여름학기",
    }:

        return "하계"

    if text in {
        "동계",
        "동계학기",
        "겨울학기",
    }:

        return "동계"

    return None


def is_schedule_token(
    value: Any,
) -> bool:

    """
    이수구분이 아니라
    학년/학기 또는 운영시기를 나타내는 값인지 검사.

    예:
    1-1
    1-2
    2-1
    4-2
    1,2
    1/2
    """

    text = (
        compact_text(
            value
        )
    )

    if not text:
        return False

    return bool(
        re.fullmatch(
            r"(?:"
            r"[1-6]-(?:1|2)"
            r"|"
            r"(?:1|2)[,/](?:1|2)"
            r")",
            text,
        )
    )


def normalize_completion_type(
    value: Any,
) -> str | None:

    text = (
        clean_text(
            value
        )
    )

    if not text:
        return None

    compact = (
        compact_text(
            text
        )
    )

    if (
        compact
        in INVALID_COMPLETION_TYPES
    ):

        return None

    if (
        is_numeric_only(
            text
        )
    ):

        return None

    if (
        is_schedule_token(
            text
        )
    ):

        return None

    if (
        extract_course_code(
            text
        )
    ):

        return None

    if (
        normalize_year_level(
            text
        )
        is not None
    ):

        return None

    if (
        normalize_semester(
            text
        )
        is not None
    ):

        return None

    return text


def normalize_number(
    value: Any,
) -> float | None:

    text = (
        clean_text(
            value
        )
    )

    if not text:
        return None

    if not (
        re.fullmatch(
            r"[-+]?\d+(?:\.\d+)?",
            text,
        )
    ):

        return None

    number = (
        float(
            text
        )
    )

    if number < 0:
        return None

    return number


def extract_course_code(
    value: Any,
) -> str | None:

    text = (
        clean_text(
            value
        )
    )

    if not text:
        return None

    if (
        STRICT_COURSE_CODE_RE
        .fullmatch(
            text
        )
    ):

        return (
            text.upper()
        )

    compact = (
        compact_text(
            text
        )
    )

    if (
        STRICT_COURSE_CODE_RE
        .fullmatch(
            compact
        )
    ):

        return (
            compact.upper()
        )

    match = (
        COURSE_CODE_FIND_RE
        .search(
            text
        )
    )

    if match:

        return (
            match.group(
                1
            )
            .upper()
        )

    return None


def is_numeric_only(
    value: Any,
) -> bool:

    return bool(
        re.fullmatch(
            r"[-+]?\d+(?:\.\d+)?",
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

    return session


# ============================================================
# PDF URL 탐색
# ============================================================

def extract_pdf_from_link(
    href: str,
    base_url: str,
) -> str | None:

    href = (
        clean_text(
            href
        )
    )

    if not href:
        return None

    absolute_url = (
        urljoin(
            base_url,
            href,
        )
    )

    parsed = (
        urlparse(
            absolute_url
        )
    )

    if (
        parsed.path
        .lower()
        .endswith(
            ".pdf"
        )
    ):

        return (
            absolute_url
        )

    query = (
        parse_qs(
            parsed.query
        )
    )

    for file_value in (
        query.get(
            "file"
        )
        or []
    ):

        file_value = (
            unquote(
                file_value
            )
        )

        if (
            ".pdf"
            in file_value.lower()
        ):

            return (
                urljoin(
                    absolute_url,
                    file_value,
                )
            )

    decoded = (
        unquote(
            href
        )
    )

    match = (
        re.search(
            r"(/[A-Za-z0-9_./-]+\.pdf)",
            decoded,
            flags=(
                re.IGNORECASE
            ),
        )
    )

    if match:

        return (
            urljoin(
                base_url,
                match.group(
                    1
                ),
            )
        )

    return None


def discover_curriculum_pdf(
    *,
    curriculum_year: int,
    department_name: str,
) -> CurriculumSource:

    session = (
        create_http_session()
    )

    pdf_url: (
        str | None
    ) = None

    try:

        response = (
            session.get(
                KONYANG_AI_CURRICULUM_PAGE,
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

        soup = (
            BeautifulSoup(
                response.text,
                "html.parser",
            )
        )

        target_phrase = (
            f"{curriculum_year}학년도 "
            f"{department_name} 교육과정"
        )

        preferred: list[
            str
        ] = []

        others: list[
            str
        ] = []

        for tag in (
            soup.find_all(
                [
                    "a",
                    "iframe",
                    "embed",
                    "object",
                ]
            )
        ):

            visible_text = (
                clean_text(
                    tag.get_text(
                        " ",
                        strip=True,
                    )
                )
            )

            for attribute in (
                "href",
                "src",
                "data",
                "onclick",
            ):

                value = (
                    tag.get(
                        attribute
                    )
                )

                if not value:
                    continue

                found = (
                    extract_pdf_from_link(
                        str(
                            value
                        ),
                        KONYANG_AI_CURRICULUM_PAGE,
                    )
                )

                if not found:
                    continue

                others.append(
                    found
                )

                if (
                    target_phrase
                    in visible_text
                ):

                    preferred.append(
                        found
                    )

        preferred = list(
            dict.fromkeys(
                preferred
            )
        )

        others = list(
            dict.fromkeys(
                others
            )
        )

        if preferred:

            pdf_url = (
                preferred[
                    0
                ]
            )

        elif others:

            pdf_url = (
                others[
                    0
                ]
            )

    except (
        requests
        .RequestException
    ) as exc:

        print(
            (
                "교육과정 페이지 "
                "자동 탐색 실패: "
                f"{exc}"
            )
        )

    if not pdf_url:

        if (
            curriculum_year
            == 2026

            and department_name
            == "인공지능학과"
        ):

            print(
                (
                    "공식 페이지에서 PDF "
                    "자동 탐색 실패 "
                    "-> 2026 AI fallback URL 사용"
                )
            )

            pdf_url = (
                FALLBACK_PDF_URL_2026_AI
            )

        else:

            raise RuntimeError(
                (
                    "공식 페이지에서 대상 "
                    "교육과정 PDF를 찾지 못했습니다: "
                    f"{curriculum_year} / "
                    f"{department_name}"
                )
            )

    file_name = (
        urlparse(
            pdf_url
        )
        .path
        .rsplit(
            "/",
            1,
        )[-1]
        or "curriculum.pdf"
    )

    return (
        CurriculumSource(
            page_url=(
                KONYANG_AI_CURRICULUM_PAGE
            ),

            pdf_url=(
                pdf_url
            ),

            file_name=(
                file_name
            ),

            department_name=(
                department_name
            ),

            curriculum_year=(
                curriculum_year
            ),
        )
    )


def download_pdf_bytes(
    source: CurriculumSource,
) -> bytes:

    session = (
        create_http_session()
    )

    response = (
        session.get(
            source.pdf_url,

            timeout=(
                HTTP_TIMEOUT
            ),

            headers={
                "Referer":
                    source.page_url,
            },
        )
    )

    response.raise_for_status()

    data = (
        response.content
    )

    if not data.startswith(
        b"%PDF"
    ):

        raise RuntimeError(
            (
                "다운로드한 파일이 "
                "PDF가 아닙니다: "
                f"{source.pdf_url}"
            )
        )

    return data


# ============================================================
# PyMuPDF
# ============================================================

def find_page_tables(
    page: pymupdf.Page,
) -> list[Any]:

    try:

        finder = (
            page.find_tables()
        )

        return (
            getattr(
                finder,
                "tables",
                None,
            )
            or []
        )

    except Exception as exc:

        print(
            (
                "  [WARN] "
                f"page {page.number + 1} "
                f"표 탐지 실패: {exc}"
            )
        )

        return []


# ============================================================
# 독립 Audit
# ============================================================

def local_audit(
    pdf_bytes: bytes,
) -> dict:

    document = (
        pymupdf.open(
            stream=(
                pdf_bytes
            ),

            filetype="pdf",
        )
    )

    page_counts: dict[
        int,
        int,
    ] = {}

    course_codes: set[
        str
    ] = set()

    try:

        for page_index in range(
            document.page_count
        ):

            page = (
                document
                .load_page(
                    page_index
                )
            )

            page_no = (
                page_index
                + 1
            )

            page_codes: set[
                str
            ] = set()

            for table in (
                find_page_tables(
                    page
                )
            ):

                try:

                    rows = (
                        table.extract()
                        or []
                    )

                except Exception:

                    continue

                for row in rows:

                    for cell in (
                        row
                        or []
                    ):

                        code = (
                            extract_course_code(
                                cell
                            )
                        )

                        if code:

                            page_codes.add(
                                code
                            )

            for word in (
                page.get_text(
                    "words"
                )
            ):

                code = (
                    extract_course_code(
                        word[
                            4
                        ]
                    )
                )

                if code:

                    page_codes.add(
                        code
                    )

            course_codes.update(
                page_codes
            )

            page_counts[
                page_no
            ] = (
                len(
                    page_codes
                )
            )

        page_count = (
            document.page_count
        )

    finally:

        document.close()

    return {
        "page_count":
            page_count,

        "page_counts":
            page_counts,

        "course_codes":
            course_codes,

        "candidate_count":
            sum(
                page_counts
                .values()
            ),
    }


# ============================================================
# Header 기본 탐지
# ============================================================

def alias_matches(
    header_text: str,
    aliases: tuple[
        str,
        ...,
    ],
) -> bool:

    header = (
        compact_text(
            header_text
        )
    )

    if not header:
        return False

    for alias in aliases:

        alias_compact = (
            compact_text(
                alias
            )
        )

        if (
            header
            == alias_compact
        ):

            return True

        if (
            len(
                alias_compact
            )
            >= 2

            and alias_compact
            in header
        ):

            return True

    return False


def row_header_keys(
    row: list[Any],
) -> set[str]:

    keys: set[
        str
    ] = set()

    for cell in (
        row
        or []
    ):

        text = (
            clean_text(
                cell
            )
        )

        if not text:
            continue

        for (
            key,
            aliases,
        ) in (
            HEADER_ALIASES
            .items()
        ):

            if (
                alias_matches(
                    text,
                    aliases,
                )
            ):

                keys.add(
                    key
                )

    return keys


def detect_header_map(
    rows: list[
        list[Any]
    ],
) -> tuple[
    dict[
        str,
        int,
    ],
    list[
        int
    ],
]:

    search_limit = (
        min(
            len(
                rows
            ),
            12,
        )
    )

    header_rows: list[
        int
    ] = []

    for row_index in range(
        search_limit
    ):

        row = (
            rows[
                row_index
            ]
            or []
        )

        if any(
            extract_course_code(
                cell
            )
            for cell in row
        ):

            continue

        if (
            row_header_keys(
                row
            )
        ):

            header_rows.append(
                row_index
            )

    if not header_rows:

        return (
            {},
            [],
        )

    max_columns = max(
        len(
            rows[
                index
            ]
            or []
        )

        for index
        in header_rows
    )

    combined_headers: list[
        str
    ] = []

    for column_index in range(
        max_columns
    ):

        parts: list[
            str
        ] = []

        for row_index in (
            header_rows
        ):

            row = (
                rows[
                    row_index
                ]
                or []
            )

            if (
                column_index
                >= len(
                    row
                )
            ):

                continue

            value = (
                clean_text(
                    row[
                        column_index
                    ]
                )
            )

            if (
                value

                and value
                not in parts
            ):

                parts.append(
                    value
                )

        combined_headers.append(
            " ".join(
                parts
            )
        )

    header_map: dict[
        str,
        int,
    ] = {}

    used_columns: set[
        int
    ] = set()

    key_order = (
        "course_code",
        "name",
        "year_level",
        "semester",
        "completion_type",
        "credits",
        "theory_hours",
        "practice_hours",
    )

    for key in (
        key_order
    ):

        aliases = (
            HEADER_ALIASES[
                key
            ]
        )

        for (
            column_index,
            header_text,
        ) in enumerate(
            combined_headers
        ):

            if (
                column_index
                in used_columns
            ):

                continue

            if (
                alias_matches(
                    header_text,
                    aliases,
                )
            ):

                header_map[
                    key
                ] = (
                    column_index
                )

                used_columns.add(
                    column_index
                )

                break

    return (
        header_map,
        header_rows,
    )


def get_cell(
    cells: list[str],
    header_map: dict[
        str,
        int,
    ],
    key: str,
) -> str:

    index = (
        header_map.get(
            key
        )
    )

    if (
        index is None

        or index < 0

        or index
        >= len(
            cells
        )
    ):

        return ""

    return (
        clean_text(
            cells[
                index
            ]
        )
    )


# ============================================================
# 실제 데이터 열 분석
# ============================================================

def data_rows_only(
    rows: list[
        list[Any]
    ],
    header_rows: list[
        int
    ],
) -> list[
    list[
        str
    ]
]:

    result: list[
        list[
            str
        ]
    ] = []

    for (
        row_index,
        row,
    ) in enumerate(
        rows
    ):

        if (
            row_index
            in header_rows
        ):

            continue

        cells = [
            clean_text(
                cell
            )

            for cell
            in (
                row
                or []
            )
        ]

        if any(
            cells
        ):

            result.append(
                cells
            )

    return result


def column_values(
    rows: list[
        list[
            str
        ]
    ],
    column_index: int,
) -> list[str]:

    values: list[
        str
    ] = []

    for row in rows:

        if (
            column_index
            >= len(
                row
            )
        ):

            continue

        value = (
            clean_text(
                row[
                    column_index
                ]
            )
        )

        if value:

            values.append(
                value
            )

    return values


def year_column_score(
    values: list[str],
) -> tuple[
    int,
    int,
]:

    normalized = [
        normalize_year_level(
            value
        )

        for value
        in values
    ]

    years = [
        value

        for value
        in normalized

        if value
        is not None
    ]

    return (
        len(
            years
        ),
        len(
            set(
                years
            )
        ),
    )


def semester_column_score(
    values: list[str],
) -> tuple[
    int,
    int,
]:

    normalized = [
        normalize_semester(
            value
        )

        for value
        in values
    ]

    semesters = [
        value

        for value
        in normalized

        if value
        is not None
    ]

    return (
        len(
            semesters
        ),
        len(
            set(
                semesters
            )
        ),
    )


def completion_column_score(
    values: list[str],
) -> tuple[
    int,
    int,
]:

    normalized = [
        normalize_completion_type(
            value
        )

        for value
        in values
    ]

    types = [
        value

        for value
        in normalized

        if value
        is not None
    ]

    return (
        len(
            types
        ),
        len(
            set(
                types
            )
        ),
    )


# ============================================================
# Header 의미 자동 보정
# ============================================================

def semantic_repair_header_map(
    rows: list[list[Any]],
    header_map: dict[str, int],
    header_rows: list[int],
) -> dict[str, int]:

    """
    Header 텍스트와 실제 데이터 분포를 함께 사용해
    학년 / 학기 / 이수구분 위치를 보정한다.

    건양대 교육과정 표의 기본 구조:

        이수구분 | 학년 | 학기 | 교과목코드 | 교과목명 | ...

    원칙:
    1. course_code 위치를 먼저 확정
    2. 코드 왼쪽에서 semester 열 탐색
    3. semester를 제외하고 year 열 탐색
    4. year/semester를 제외하고 남는 텍스트 열을
       completion_type으로 사용
    5. 1-1 / 1-2 같은 이수시기 값은
       completion_type으로 인정하지 않음
    """

    result = dict(
        header_map
    )

    data_rows = (
        data_rows_only(
            rows,
            header_rows,
        )
    )

    if not data_rows:
        return result

    # ========================================================
    # 1. 교과목 코드 열 확정
    # ========================================================

    code_index = (
        result.get(
            "course_code"
        )
    )

    max_columns = max(
        (
            len(
                row
            )
            for row
            in data_rows
        ),
        default=0,
    )

    if (
        code_index
        is None
    ):

        best_column: (
            int | None
        ) = None

        best_count = 0

        for column_index in range(
            max_columns
        ):

            count = sum(
                1
                for value
                in column_values(
                    data_rows,
                    column_index,
                )
                if extract_course_code(
                    value
                )
            )

            if (
                count
                > best_count
            ):

                best_count = (
                    count
                )

                best_column = (
                    column_index
                )

        if (
            best_column
            is not None
        ):

            code_index = (
                best_column
            )

            result[
                "course_code"
            ] = (
                best_column
            )

    if (
        code_index
        is None
        or code_index
        <= 0
    ):

        return result

    prefix_columns = list(
        range(
            code_index
        )
    )

    # ========================================================
    # 2. 학기 열
    # ========================================================

    raw_semester = (
        header_map.get(
            "semester"
        )
    )

    semester_scores: list[
        tuple[
            int,
            int,
            int,
            int,
        ]
    ] = []

    for column_index in (
        prefix_columns
    ):

        values = (
            column_values(
                data_rows,
                column_index,
            )
        )

        normalized = [
            normalize_semester(
                value
            )
            for value
            in values
        ]

        valid = [
            value
            for value
            in normalized
            if value
            is not None
        ]

        count = len(
            valid
        )

        diversity = len(
            set(
                valid
            )
        )

        raw_bonus = (
            1
            if (
                column_index
                == raw_semester
            )
            else 0
        )

        # 우선순위:
        # 실제 학기 값이 많이 존재
        # → 기존 Header와 일치
        # → 1/2 등 종류가 너무 많지 않음
        semester_scores.append(
            (
                count,
                raw_bonus,
                -diversity,
                -column_index,
            )
        )

    if semester_scores:

        best = max(
            semester_scores
        )

        best_count = (
            best[
                0
            ]
        )

        best_column = (
            -best[
                3
            ]
        )

        if (
            best_count
            > 0
        ):

            result[
                "semester"
            ] = (
                best_column
            )

        else:

            result.pop(
                "semester",
                None,
            )

    semester_column = (
        result.get(
            "semester"
        )
    )

    # ========================================================
    # 3. 학년 열
    # ========================================================

    raw_year = (
        header_map.get(
            "year_level"
        )
    )

    year_scores: list[
        tuple[
            int,
            int,
            int,
            int,
        ]
    ] = []

    for column_index in (
        prefix_columns
    ):

        if (
            column_index
            == semester_column
        ):

            continue

        values = (
            column_values(
                data_rows,
                column_index,
            )
        )

        normalized = [
            normalize_year_level(
                value
            )
            for value
            in values
        ]

        valid = [
            value
            for value
            in normalized
            if value
            is not None
        ]

        count = len(
            valid
        )

        diversity = len(
            set(
                valid
            )
        )

        raw_bonus = (
            1
            if (
                column_index
                == raw_year
            )
            else 0
        )

        # 학년은 1/2/3/4처럼
        # 서로 다른 값이 나오는 것이 중요
        year_scores.append(
            (
                diversity,
                count,
                raw_bonus,
                -column_index,
            )
        )

    if year_scores:

        best = max(
            year_scores
        )

        best_diversity = (
            best[
                0
            ]
        )

        best_count = (
            best[
                1
            ]
        )

        best_column = (
            -best[
                3
            ]
        )

        if (
            best_count
            > 0
            and best_diversity
            > 0
        ):

            result[
                "year_level"
            ] = (
                best_column
            )

        else:

            result.pop(
                "year_level",
                None,
            )

    year_column = (
        result.get(
            "year_level"
        )
    )

    # ========================================================
    # 4. 이수구분 열
    #
    # 여기서는 점수제로 선택하지 않는다.
    #
    # 교과목 코드 앞에서
    # 학년/학기 열을 제외하고 남는 열 중
    # 실제 텍스트 분류값이 존재하는 첫 열을 사용.
    # ========================================================

    result.pop(
        "completion_type",
        None,
    )

    remaining_columns = [
        column_index

        for column_index
        in prefix_columns

        if (
            column_index
            not in {
                year_column,
                semester_column,
            }
        )
    ]

    for column_index in (
        remaining_columns
    ):

        values = (
            column_values(
                data_rows,
                column_index,
            )
        )

        valid_values = [
            value
            for value
            in (
                normalize_completion_type(
                    raw_value
                )
                for raw_value
                in values
            )
            if value
            is not None
        ]

        if not valid_values:
            continue

        # ----------------------------------------------------
        # 1-1 / 1-2 / 2-1 ...
        # 같은 이수시기 열이면 제외
        # ----------------------------------------------------

        schedule_count = sum(
            1
            for value
            in values
            if is_schedule_token(
                value
            )
        )

        if (
            schedule_count
            >= len(
                values
            )
            * 0.5
        ):

            continue

        # ----------------------------------------------------
        # 실제 이수구분 후보
        #
        # 예:
        # 진로 취창업
        # 인성과 도전
        # SW 기초
        # 의사소통
        # 기본
        # 심화
        # ----------------------------------------------------

        result[
            "completion_type"
        ] = (
            column_index
        )

        break

    return result
# ============================================================
# 행 파싱
# ============================================================

def infer_name_from_row(
    cells: list[str],
    code_index: int,
) -> str | None:

    for cell in (
        cells[
            code_index + 1:
        ]
    ):

        candidate = (
            normalize_name(
                cell
            )
        )

        if not candidate:
            continue

        if (
            is_numeric_only(
                candidate
            )
        ):

            continue

        if (
            compact_text(
                candidate
            )
            in INVALID_COMPLETION_TYPES
        ):

            continue

        return candidate

    return None


def numeric_tail_after_name(
    cells: list[str],
    *,
    name_index: int | None,
    code_index: int,
) -> list[float]:

    start = (
        name_index
        + 1

        if (
            name_index
            is not None

            and name_index
            >= 0
        )

        else (
            code_index
            + 1
        )
    )

    values: list[
        float
    ] = []

    for cell in (
        cells[
            start:
        ]
    ):

        value = (
            normalize_number(
                cell
            )
        )

        if (
            value
            is not None
        ):

            values.append(
                value
            )

    return values


def build_course_record(
    *,
    source: CurriculumSource,
    page_no: int,
    table_index: int | None,
    row_index: int | None,
    code: str,
    name: str,
    year_level: int | None,
    semester: str | None,
    completion_type: str | None,
    credits: float | None,
    theory_hours: float | None,
    practice_hours: float | None,
    source_text: str,
    source_table: str,
) -> dict:

    return {
        "department_name":
            source.department_name,

        "curriculum_year":
            source.curriculum_year,

        "revision_type":
            source.revision_type,

        "course_code":
            code,

        "name":
            name,

        "year_level":
            year_level,

        "semester":
            semester,

        "completion_type":
            normalize_completion_type(
                completion_type
            ),

        "credits":
            credits,

        "theory_hours":
            theory_hours,

        "practice_hours":
            practice_hours,

        "source_file_name":
            source.file_name,

        "source_page":
            page_no,

        "source_table":
            source_table,

        "source_row":
            row_index,

        "source_url":
            source.pdf_url,

        "source_text":
            source_text,

        "raw_metadata": {
            "table_index":
                table_index,

            "parser":
                source_table,
        },
    }


def parse_table_rows(
    *,
    rows: list[
        list[Any]
    ],
    source: CurriculumSource,
    page_no: int,
    table_index: int,
) -> tuple[
    list[
        dict
    ],
    TableDiagnostic,
]:

    (
        header_original,
        header_rows,
    ) = (
        detect_header_map(
            rows
        )
    )

    # ---------------------------------------------
    # 핵심:
    # Header 문자만 보지 않고 실제 값으로 보정
    # ---------------------------------------------

    header_map = (
        semantic_repair_header_map(
            rows,
            header_original,
            header_rows,
        )
    )

    diagnostic = (
        TableDiagnostic(
            page_no=(
                page_no
            ),

            table_index=(
                table_index
            ),

            row_count=(
                len(
                    rows
                )
            ),

            header_rows=[
                index
                + 1

                for index
                in header_rows
            ],

            header_original=dict(
                header_original
            ),

            header_map=dict(
                header_map
            ),
        )
    )

    # 표마다 독립된 상태 사용
    state = (
        ParseState()
    )

    courses: list[
        dict
    ] = []

    for (
        row_index,
        row,
    ) in enumerate(
        rows
    ):

        if (
            row_index
            in header_rows
        ):

            continue

        cells = [
            clean_text(
                cell
            )

            for cell
            in (
                row
                or []
            )
        ]

        if not any(
            cells
        ):

            continue

        # ----------------------------------------------------
        # 병합셀 값 먼저 갱신
        # ----------------------------------------------------

        parsed_year = (
            normalize_year_level(
                get_cell(
                    cells,
                    header_map,
                    "year_level",
                )
            )
        )

        if (
            parsed_year
            is not None
        ):

            state.year_level = (
                parsed_year
            )

        parsed_semester = (
            normalize_semester(
                get_cell(
                    cells,
                    header_map,
                    "semester",
                )
            )
        )

        if (
            parsed_semester
            is not None
        ):

            state.semester = (
                parsed_semester
            )

        parsed_completion = (
            normalize_completion_type(
                get_cell(
                    cells,
                    header_map,
                    "completion_type",
                )
            )
        )

        if (
            parsed_completion
            is not None
        ):

            state.completion_type = (
                parsed_completion
            )

        # ----------------------------------------------------
        # 교과목 코드
        # ----------------------------------------------------

        code: (
            str | None
        ) = None

        code_index: (
            int | None
        ) = None

        code_from_column = (
            get_cell(
                cells,
                header_map,
                "course_code",
            )
        )

        if (
            code_from_column
        ):

            code = (
                extract_course_code(
                    code_from_column
                )
            )

            if code:

                code_index = (
                    header_map.get(
                        "course_code"
                    )
                )

        # Header가 틀려도 전체 행에서 한 번 더 탐색
        if not code:

            for (
                index,
                cell,
            ) in enumerate(
                cells
            ):

                found = (
                    extract_course_code(
                        cell
                    )
                )

                if found:

                    code = (
                        found
                    )

                    code_index = (
                        index
                    )

                    break

        # 코드 없는 행은
        # 병합셀 상태만 갱신하고 Course 생성 안 함
        if (
            not code

            or code_index
            is None
        ):

            continue

        # ----------------------------------------------------
        # 이름
        # ----------------------------------------------------

        name_index = (
            header_map.get(
                "name"
            )
        )

        name = (
            normalize_name(
                get_cell(
                    cells,
                    header_map,
                    "name",
                )
            )
        )

        if not name:

            name = (
                infer_name_from_row(
                    cells,
                    code_index,
                )
            )

        if not name:

            continue

        # ----------------------------------------------------
        # 학점 / 이론 / 실습
        # ----------------------------------------------------

        credits = (
            normalize_number(
                get_cell(
                    cells,
                    header_map,
                    "credits",
                )
            )
        )

        theory_hours = (
            normalize_number(
                get_cell(
                    cells,
                    header_map,
                    "theory_hours",
                )
            )
        )

        practice_hours = (
            normalize_number(
                get_cell(
                    cells,
                    header_map,
                    "practice_hours",
                )
            )
        )

        if (
            credits
            is None

            or theory_hours
            is None

            or practice_hours
            is None
        ):

            tail = (
                numeric_tail_after_name(
                    cells,

                    name_index=(
                        name_index
                    ),

                    code_index=(
                        code_index
                    ),
                )
            )

            if (
                credits
                is None

                and len(
                    tail
                )
                >= 1
            ):

                credits = (
                    tail[
                        0
                    ]
                )

            if (
                theory_hours
                is None

                and len(
                    tail
                )
                >= 2
            ):

                theory_hours = (
                    tail[
                        1
                    ]
                )

            if (
                practice_hours
                is None

                and len(
                    tail
                )
                >= 3
            ):

                practice_hours = (
                    tail[
                        2
                    ]
                )

        # 현장실습은 15학점 등 가능
        if (
            credits
            is not None

            and not (
                0
                <= credits
                <= 24
            )
        ):

            credits = None

        if (
            theory_hours
            is not None

            and not (
                0
                <= theory_hours
                <= 30
            )
        ):

            theory_hours = None

        if (
            practice_hours
            is not None

            and not (
                0
                <= practice_hours
                <= 40
            )
        ):

            practice_hours = None

        courses.append(
            build_course_record(
                source=(
                    source
                ),

                page_no=(
                    page_no
                ),

                table_index=(
                    table_index
                ),

                row_index=(
                    row_index
                    + 1
                ),

                code=(
                    code
                ),

                name=(
                    name
                ),

                year_level=(
                    state.year_level
                ),

                semester=(
                    state.semester
                ),

                completion_type=(
                    state.completion_type
                ),

                credits=(
                    credits
                ),

                theory_hours=(
                    theory_hours
                ),

                practice_hours=(
                    practice_hours
                ),

                source_text=(
                    " | ".join(
                        cells
                    )
                ),

                source_table=(
                    "pymupdf-table-v4"
                ),
            )
        )

    diagnostic.code_count = (
        len(
            courses
        )
    )

    diagnostic.seen_years = {
        int(
            course[
                "year_level"
            ]
        )

        for course
        in courses

        if (
            course.get(
                "year_level"
            )
            is not None
        )
    }

    diagnostic.seen_semesters = {
        str(
            course[
                "semester"
            ]
        )

        for course
        in courses

        if (
            course.get(
                "semester"
            )
        )
    }

    diagnostic.seen_completion_types = {
        str(
            course[
                "completion_type"
            ]
        )

        for course
        in courses

        if (
            course.get(
                "completion_type"
            )
        )
    }

    return (
        courses,
        diagnostic,
    )


# ============================================================
# words 좌표 fallback
# ============================================================

def group_words_by_visual_line(
    words: list[
        tuple
    ],
    *,
    tolerance: float = 4.0,
) -> list[
    list[
        tuple
    ]
]:

    ordered = sorted(
        words,

        key=lambda word: (
            (
                float(
                    word[
                        1
                    ]
                )
                + float(
                    word[
                        3
                    ]
                )
            )
            / 2.0,

            float(
                word[
                    0
                ]
            ),
        ),
    )

    lines: list[
        list[
            tuple
        ]
    ] = []

    centers: list[
        float
    ] = []

    for word in (
        ordered
    ):

        center = (
            (
                float(
                    word[
                        1
                    ]
                )
                + float(
                    word[
                        3
                    ]
                )
            )
            / 2.0
        )

        target: (
            int | None
        ) = None

        for index in range(
            len(
                lines
            )
            - 1,
            -1,
            -1,
        ):

            if (
                abs(
                    centers[
                        index
                    ]
                    - center
                )
                <= tolerance
            ):

                target = (
                    index
                )

                break

            if (
                center
                - centers[
                    index
                ]
                > tolerance
            ):

                break

        if (
            target
            is None
        ):

            lines.append(
                [
                    word
                ]
            )

            centers.append(
                center
            )

        else:

            lines[
                target
            ].append(
                word
            )

            count = (
                len(
                    lines[
                        target
                    ]
                )
            )

            centers[
                target
            ] = (
                (
                    centers[
                        target
                    ]
                    * (
                        count
                        - 1
                    )
                )
                + center
            ) / count

    for line in lines:

        line.sort(
            key=lambda word:
                float(
                    word[
                        0
                    ]
                )
        )

    return lines


def fallback_name_from_words(
    line: list[
        tuple
    ],
    code_index: int,
) -> str | None:

    parts: list[
        str
    ] = []

    for word in (
        line[
            code_index
            + 1:
        ]
    ):

        token = (
            clean_text(
                word[
                    4
                ]
            )
        )

        if not token:
            continue

        if (
            is_numeric_only(
                token
            )
        ):

            if parts:
                break

            continue

        if (
            extract_course_code(
                token
            )
        ):

            continue

        parts.append(
            token
        )

    return (
        normalize_name(
            " ".join(
                parts
            )
        )
    )


def parse_missing_codes_from_words(
    *,
    page: pymupdf.Page,
    source: CurriculumSource,
    target_codes: set[
        str
    ],
) -> list[
    dict
]:

    if not target_codes:

        return []

    result: list[
        dict
    ] = []

    remaining = set(
        target_codes
    )

    lines = (
        group_words_by_visual_line(
            page.get_text(
                "words"
            )
        )
    )

    for (
        line_index,
        line,
    ) in enumerate(
        lines,
        start=1,
    ):

        tokens = [
            clean_text(
                word[
                    4
                ]
            )

            for word
            in line
        ]

        for (
            code_index,
            token,
        ) in enumerate(
            tokens
        ):

            code = (
                extract_course_code(
                    token
                )
            )

            if (
                not code

                or code
                not in remaining
            ):

                continue

            name = (
                fallback_name_from_words(
                    line,
                    code_index,
                )
            )

            if not name:

                continue

            result.append(
                build_course_record(
                    source=(
                        source
                    ),

                    page_no=(
                        page.number
                        + 1
                    ),

                    table_index=None,

                    row_index=(
                        line_index
                    ),

                    code=(
                        code
                    ),

                    name=(
                        name
                    ),

                    # words fallback에서는
                    # 자신 없는 메타데이터를 추측하지 않음
                    year_level=None,

                    semester=None,

                    completion_type=None,

                    credits=None,

                    theory_hours=None,

                    practice_hours=None,

                    source_text=(
                        " ".join(
                            tokens
                        )
                    ),

                    source_table=(
                        "pymupdf-words-fallback"
                    ),
                )
            )

            remaining.remove(
                code
            )

            break

        if not remaining:

            break

    return result


# ============================================================
# 중복 처리
# ============================================================

def course_quality_score(
    course: dict,
) -> tuple[
    int,
    int,
    int,
]:

    metadata_score = sum(
        course.get(
            key
        )
        is not None

        for key in (
            "year_level",
            "semester",
            "completion_type",
            "credits",
            "theory_hours",
            "practice_hours",
        )
    )

    table_score = (
        1

        if (
            course.get(
                "source_table"
            )
            != "pymupdf-words-fallback"
        )

        else 0
    )

    page = (
        int(
            course.get(
                "source_page"
            )
            or 9999
        )
    )

    # 메타데이터 많은 행 우선,
    # 동점이면 실제 앞쪽 교과목 표 우선
    return (
        metadata_score,
        table_score,
        -page,
    )


def deduplicate_courses(
    courses: list[
        dict
    ],
) -> list[
    dict
]:

    by_code: dict[
        str,
        dict,
    ] = {}

    for course in (
        courses
    ):

        code = (
            clean_text(
                course.get(
                    "course_code"
                )
            )
            .upper()
        )

        if not code:

            continue

        existing = (
            by_code.get(
                code
            )
        )

        if (
            existing
            is None

            or course_quality_score(
                course
            )
            >
            course_quality_score(
                existing
            )
        ):

            by_code[
                code
            ] = (
                course
            )

    result = list(
        by_code
        .values()
    )

    result.sort(
        key=lambda course: (
            course.get(
                "source_page"
            )
            or 9999,

            course.get(
                "source_row"
            )
            or 9999,

            course.get(
                "course_code"
            )
            or "",
        )
    )

    return result


# ============================================================
# 전체 Parser
# ============================================================

def parse_curriculum_pdf(
    pdf_bytes: bytes,
    source: CurriculumSource,
    audit: dict,
) -> tuple[
    list[
        dict
    ],
    list[
        TableDiagnostic
    ],
]:

    document = (
        pymupdf.open(
            stream=(
                pdf_bytes
            ),

            filetype="pdf",
        )
    )

    courses: list[
        dict
    ] = []

    diagnostics: list[
        TableDiagnostic
    ] = []

    try:

        for page_index in range(
            document.page_count
        ):

            page = (
                document
                .load_page(
                    page_index
                )
            )

            page_no = (
                page_index
                + 1
            )

            for (
                table_index,
                table,
            ) in enumerate(
                find_page_tables(
                    page
                ),
                start=1,
            ):

                try:

                    rows = (
                        table.extract()
                        or []
                    )

                except Exception as exc:

                    print(
                        (
                            f"  [WARN] "
                            f"p{page_no} "
                            f"table {table_index} "
                            f"추출 실패: "
                            f"{exc}"
                        )
                    )

                    continue

                (
                    table_courses,
                    diagnostic,
                ) = (
                    parse_table_rows(
                        rows=(
                            rows
                        ),

                        source=(
                            source
                        ),

                        page_no=(
                            page_no
                        ),

                        table_index=(
                            table_index
                        ),
                    )
                )

                courses.extend(
                    table_courses
                )

                diagnostics.append(
                    diagnostic
                )

        courses = (
            deduplicate_courses(
                courses
            )
        )

        parsed_codes = {
            course[
                "course_code"
            ]
            .upper()

            for course
            in courses

            if (
                course.get(
                    "course_code"
                )
            )
        }

        missing_codes = (
            set(
                audit[
                    "course_codes"
                ]
            )
            - parsed_codes
        )

        if missing_codes:

            print(
                (
                    "\n  표 파서 누락 코드 "
                    f"{len(missing_codes)}개 "
                    "-> words fallback 시도"
                )
            )

            for page_index in range(
                document.page_count
            ):

                if not missing_codes:

                    break

                page = (
                    document
                    .load_page(
                        page_index
                    )
                )

                page_codes = {
                    code

                    for word
                    in page.get_text(
                        "words"
                    )

                    if (
                        code := (
                            extract_course_code(
                                word[
                                    4
                                ]
                            )
                        )
                    )
                    is not None
                }

                target = (
                    missing_codes
                    & page_codes
                )

                if not target:

                    continue

                recovered = (
                    parse_missing_codes_from_words(
                        page=(
                            page
                        ),

                        source=(
                            source
                        ),

                        target_codes=(
                            target
                        ),
                    )
                )

                courses.extend(
                    recovered
                )

                missing_codes -= {
                    item[
                        "course_code"
                    ]

                    for item
                    in recovered
                }

            courses = (
                deduplicate_courses(
                    courses
                )
            )

        return (
            courses,
            diagnostics,
        )

    finally:

        document.close()


# ============================================================
# 검증
# ============================================================

def validate_parser_result(
    courses: list[
        dict
    ],
    audit: dict,
    source: CurriculumSource,
) -> dict:

    parser_codes = [
        clean_text(
            course.get(
                "course_code"
            )
        ).upper()

        for course
        in courses

        if clean_text(
            course.get(
                "course_code"
            )
        )
    ]

    parser_code_set = set(
        parser_codes
    )

    audit_codes = set(
        audit[
            "course_codes"
        ]
    )

    overlap = (
        parser_code_set
        & audit_codes
    )

    source_key = (
        source.curriculum_year,
        source.department_name,
    )

    expected_count = (
        EXPECTED_COURSE_COUNTS
        .get(
            source_key
        )
    )

    metadata_minimums = (
        EXPECTED_METADATA_MINIMUMS
        .get(
            source_key,
            {},
        )
    )

    metadata_counts = {
        "year_level":
            sum(
                course.get(
                    "year_level"
                )
                is not None

                for course
                in courses
            ),

        "semester":
            sum(
                course.get(
                    "semester"
                )
                is not None

                for course
                in courses
            ),

        "completion_type":
            sum(
                course.get(
                    "completion_type"
                )
                is not None

                for course
                in courses
            ),

        "credits":
            sum(
                course.get(
                    "credits"
                )
                is not None

                for course
                in courses
            ),
    }

    invalid_completion_codes = [
        course[
            "course_code"
        ]

        for course
        in courses

        if (
            course.get(
                "completion_type"
            )

            and (
                compact_text(
                    course[
                        "completion_type"
                    ]
                )
                in INVALID_COMPLETION_TYPES

                or is_schedule_token(
                    course[
                        "completion_type"
                    ]
                )
            )
        )
    ]

    completion_counter = (
        Counter(
            course[
                "completion_type"
            ]

            for course
            in courses

            if (
                course.get(
                    "completion_type"
                )
            )
        )
    )

    return {
        "parser_course_count":
            len(
                courses
            ),

        "parser_code_count":
            len(
                parser_code_set
            ),

        "audit_code_count":
            len(
                audit_codes
            ),

        "overlap_count":
            len(
                overlap
            ),

        "audit_overlap_ratio":
            (
                len(
                    overlap
                )
                / len(
                    audit_codes
                )

                if audit_codes

                else None
            ),

        "missing_in_parser":
            sorted(
                audit_codes
                - parser_code_set
            ),

        "parser_only":
            sorted(
                parser_code_set
                - audit_codes
            ),

        "duplicate_codes":
            sorted(
                code

                for code
                in set(
                    parser_codes
                )

                if (
                    parser_codes
                    .count(
                        code
                    )
                    > 1
                )
            ),

        "expected_count":
            expected_count,

        "metadata_counts":
            metadata_counts,

        "metadata_minimums":
            metadata_minimums,

        "invalid_completion_codes":
            sorted(
                invalid_completion_codes
            ),

        "completion_types":
            dict(
                sorted(
                    completion_counter
                    .items()
                )
            ),
    }


def assert_validation_passed(
    validation: dict,
) -> None:

    errors: list[
        str
    ] = []

    if (
        validation[
            "parser_course_count"
        ]
        != validation[
            "parser_code_count"
        ]
    ):

        errors.append(
            (
                "Parser 교과목 수와 "
                "고유 코드 수가 다릅니다: "
                f"{validation['parser_course_count']} / "
                f"{validation['parser_code_count']}"
            )
        )

    if (
        validation[
            "parser_code_count"
        ]
        != validation[
            "audit_code_count"
        ]
    ):

        errors.append(
            (
                "Parser 코드 수와 "
                "Audit 코드 수가 다릅니다: "
                f"{validation['parser_code_count']} / "
                f"{validation['audit_code_count']}"
            )
        )

    if (
        validation[
            "overlap_count"
        ]
        != validation[
            "audit_code_count"
        ]
    ):

        errors.append(
            (
                "Audit 코드 전체가 "
                "Parser와 일치하지 않습니다: "
                f"{validation['overlap_count']} / "
                f"{validation['audit_code_count']}"
            )
        )

    if (
        validation[
            "missing_in_parser"
        ]
    ):

        errors.append(
            (
                "Audit에는 있지만 "
                "Parser에는 없는 코드: "
                f"{validation['missing_in_parser']}"
            )
        )

    if (
        validation[
            "parser_only"
        ]
    ):

        errors.append(
            (
                "Parser에만 있는 코드: "
                f"{validation['parser_only']}"
            )
        )

    if (
        validation[
            "duplicate_codes"
        ]
    ):

        errors.append(
            (
                "중복 교과목 코드: "
                f"{validation['duplicate_codes']}"
            )
        )

    expected_count = (
        validation[
            "expected_count"
        ]
    )

    if (
        expected_count
        is not None

        and validation[
            "parser_code_count"
        ]
        != expected_count
    ):

        errors.append(
            (
                "기존 검증 기준 "
                "교과목 수와 다릅니다: "
                f"{validation['parser_code_count']} / "
                f"{expected_count}"
            )
        )

    counts = (
        validation[
            "metadata_counts"
        ]
    )

    minimums = (
        validation[
            "metadata_minimums"
        ]
    )

    for (
        key,
        minimum,
    ) in (
        minimums
        .items()
    ):

        current = (
            counts.get(
                key,
                0,
            )
        )

        if (
            current
            < minimum
        ):

            errors.append(
                (
                    "메타데이터 부족: "
                    f"{key}={current}, "
                    f"최소={minimum}"
                )
            )

    if (
        validation[
            "invalid_completion_codes"
        ]
    ):

        errors.append(
            (
                "잘못된 이수구분이 "
                "남아 있습니다: "
                f"{validation['invalid_completion_codes']}"
            )
        )

    if errors:

        print(
            "\n[검증 실패]"
        )

        for error in (
            errors
        ):

            print(
                f"  - {error}"
            )

        raise RuntimeError(
            (
                "교육과정 데이터 품질 검증에 "
                "실패하여 PostgreSQL 적재를 "
                "중단합니다."
            )
        )

    print(
        "\n[검증 통과]"
    )

    print(
        (
            "  Parser 교과목: "
            f"{validation['parser_course_count']}개"
        )
    )

    print(
        (
            "  Audit 교과목 코드: "
            f"{validation['audit_code_count']}개"
        )
    )

    print(
        (
            "  코드 일치: "
            f"{validation['overlap_count']}개"
        )
    )

    if (
        expected_count
        is not None
    ):

        print(
            (
                "  기준 교과목 수: "
                f"{expected_count}개"
            )
        )

    print(
        "  코드 일치율: 100.0%"
    )

    print(
        "  메타데이터 품질 기준 통과"
    )

    print(
        "  -> PostgreSQL 적재 허용"
    )


# ============================================================
# 출력
# ============================================================

def print_table_diagnostics(
    diagnostics: list[
        TableDiagnostic
    ],
) -> None:

    print(
        "\n[표 구조 진단]"
    )

    relevant = [
        item

        for item
        in diagnostics

        if (
            item.code_count
            > 0
        )
    ]

    for item in (
        relevant
    ):

        print(
            (
                f"  p{item.page_no} "
                f"table {item.table_index}"
                f" | codes={item.code_count}"
                f" | raw={item.header_original}"
                f" | fixed={item.header_map}"
                f" | years="
                f"{sorted(item.seen_years)}"
                f" | semesters="
                f"{sorted(item.seen_semesters)}"
                f" | types="
                f"{sorted(item.seen_completion_types)}"
            )
        )


def print_validation_summary(
    validation: dict,
) -> None:

    counts = (
        validation[
            "metadata_counts"
        ]
    )

    minimums = (
        validation[
            "metadata_minimums"
        ]
    )

    print(
        "\n[검증 요약]"
    )

    print(
        (
            "  Parser 교과목: "
            f"{validation['parser_course_count']}개"
        )
    )

    print(
        (
            "  Parser 고유 코드: "
            f"{validation['parser_code_count']}개"
        )
    )

    print(
        (
            "  Audit 고유 코드: "
            f"{validation['audit_code_count']}개"
        )
    )

    print(
        (
            "  코드 일치: "
            f"{validation['overlap_count']}개"
        )
    )

    labels = {
        "year_level":
            "학년",

        "semester":
            "학기",

        "completion_type":
            "이수구분",

        "credits":
            "학점",
    }

    for key in (
        "year_level",
        "semester",
        "completion_type",
        "credits",
    ):

        current = (
            counts.get(
                key,
                0,
            )
        )

        minimum = (
            minimums.get(
                key
            )
        )

        suffix = (
            f" (최소 {minimum})"

            if (
                minimum
                is not None
            )

            else ""
        )

        print(
            (
                f"  {labels[key]}: "
                f"{current} / "
                f"{validation['parser_course_count']}"
                f"{suffix}"
            )
        )

    print(
        (
            "  이수구분 분포: "
            f"{validation['completion_types']}"
        )
    )

    if (
        validation[
            "expected_count"
        ]
        is not None
    ):

        print(
            (
                "  기존 검증 기준: "
                f"{validation['expected_count']}개"
            )
        )


def print_preview(
    courses: list[
        dict
    ],
    limit: int = 100,
) -> None:

    print(
        "\n[결정론적 Parser 미리보기]"
    )

    for (
        index,
        course,
    ) in enumerate(
        courses[
            :limit
        ],
        start=1,
    ):

        year = (
            course.get(
                "year_level"
            )
        )

        semester = (
            course.get(
                "semester"
            )
        )

        credits = (
            course.get(
                "credits"
            )
        )

        year_text = (
            f"{year}학년"

            if (
                year
                is not None
            )

            else "-학년"
        )

        if (
            semester
            in {
                "1",
                "2",
            }
        ):

            semester_text = (
                f"{semester}학기"
            )

        elif semester:

            semester_text = (
                str(
                    semester
                )
            )

        else:

            semester_text = (
                "-학기"
            )

        credits_text = (
            f"{credits}학점"

            if (
                credits
                is not None
            )

            else "-학점"
        )

        print(
            (
                f"{index:03d}. "
                f"p{course.get('source_page')} | "
                f"{year_text} | "
                f"{semester_text} | "
                f"{course.get('completion_type') or '-'} | "
                f"{course.get('course_code') or '-'} | "
                f"{course.get('name')} | "
                f"{credits_text} | "
                f"{course.get('source_table')}"
            )
        )


# ============================================================
# SourceDocument
# ============================================================

def build_source_content(
    courses: list[
        dict
    ],
) -> str:

    lines = [
        (
            "페이지 | 학년 | 학기 | 이수구분 | "
            "교과목코드 | 교과목명 | "
            "학점 | 이론 | 실습"
        )
    ]

    for course in (
        courses
    ):

        lines.append(
            " | ".join(
                [
                    str(
                        course.get(
                            "source_page"
                        )
                        or ""
                    ),

                    str(
                        course.get(
                            "year_level"
                        )
                        or ""
                    ),

                    str(
                        course.get(
                            "semester"
                        )
                        or ""
                    ),

                    str(
                        course.get(
                            "completion_type"
                        )
                        or ""
                    ),

                    str(
                        course.get(
                            "course_code"
                        )
                        or ""
                    ),

                    str(
                        course.get(
                            "name"
                        )
                        or ""
                    ),

                    (
                        str(
                            course.get(
                                "credits"
                            )
                        )

                        if (
                            course.get(
                                "credits"
                            )
                            is not None
                        )

                        else ""
                    ),

                    (
                        str(
                            course.get(
                                "theory_hours"
                            )
                        )

                        if (
                            course.get(
                                "theory_hours"
                            )
                            is not None
                        )

                        else ""
                    ),

                    (
                        str(
                            course.get(
                                "practice_hours"
                            )
                        )

                        if (
                            course.get(
                                "practice_hours"
                            )
                            is not None
                        )

                        else ""
                    ),
                ]
            )
        )

    return (
        "\n".join(
            lines
        )
    )


# ============================================================
# PostgreSQL
# ============================================================

def save_to_database(
    *,
    source: CurriculumSource,
    pdf_bytes: bytes,
    courses: list[
        dict
    ],
    validation: dict,
) -> dict:

    content = (
        build_source_content(
            courses
        )
    )

    created_count = 0
    updated_count = 0

    parent_source_document_id: (
        int | None
    ) = None

    with Session(
        engine
    ) as session:

        try:

            parent_document = (
                upsert_curriculum_document(
                    session,

                    department_name=(
                        source.department_name
                    ),

                    curriculum_year=(
                        source.curriculum_year
                    ),

                    content=(
                        content
                    ),

                    source_file_name=(
                        source.file_name
                    ),

                    source_url=(
                        source.pdf_url
                    ),

                    revision_type=(
                        source.revision_type
                    ),

                    raw_metadata={
                        "collector":
                            "konyang_curriculum",

                        "parser":
                            "pymupdf_deterministic_v4",

                        "llm_used":
                            False,

                        "pdf_sha256":
                            (
                                hashlib.sha256(
                                    pdf_bytes
                                )
                                .hexdigest()
                            ),

                        "course_count":
                            len(
                                courses
                            ),

                        "validation":
                            validation,

                        "source_page_url":
                            source.page_url,
                    },
                )
            )

            session.flush()

            parent_source_document_id = (
                int(
                    parent_document
                    .source_document_id
                )
            )

            for (
                index,
                course,
            ) in enumerate(
                courses,
                start=1,
            ):

                result = (
                    upsert_course_record(
                        session,

                        course,

                        parent_source_document_id=(
                            parent_source_document_id
                        ),
                    )
                )

                if (
                    result.get(
                        "created"
                    )
                ):

                    created_count += 1

                    status = (
                        "CREATED"
                    )

                else:

                    updated_count += 1

                    status = (
                        "UPDATED"
                    )

                print(
                    (
                        f"  [{index:02d}/"
                        f"{len(courses):02d}] "
                        f"{status} | "
                        f"{course.get('course_code')} | "
                        f"{course.get('name')}"
                    )
                )

            session.commit()

        except Exception:

            session.rollback()

            raise

    return {
        "total":
            len(
                courses
            ),

        "created":
            created_count,

        "updated":
            updated_count,

        "parent_source_document_id":
            parent_source_document_id,
    }


# ============================================================
# 실행
# ============================================================

def run(
    *,
    curriculum_year: int = (
        DEFAULT_CURRICULUM_YEAR
    ),

    department_name: str = (
        DEFAULT_DEPARTMENT
    ),

    save_db: bool = True,
) -> dict:

    print(
        "=" * 72
    )

    print(
        (
            "CareerQuest - 건양대학교 교육과정 "
            "결정론적 파싱/검증/적재 v4"
        )
    )

    print(
        "=" * 72
    )

    # --------------------------------------------------------
    # 1
    # --------------------------------------------------------

    print(
        "\n[1/5] 공식 교육과정 PDF 탐색"
    )

    source = (
        discover_curriculum_pdf(
            curriculum_year=(
                curriculum_year
            ),

            department_name=(
                department_name
            ),
        )
    )

    print(
        (
            f"  학과: "
            f"{source.department_name}"
        )
    )

    print(
        (
            f"  연도: "
            f"{source.curriculum_year}"
        )
    )

    print(
        (
            f"  PDF: "
            f"{source.pdf_url}"
        )
    )

    # --------------------------------------------------------
    # 2
    # --------------------------------------------------------

    print(
        "\n[2/5] PDF 다운로드"
    )

    pdf_bytes = (
        download_pdf_bytes(
            source
        )
    )

    print(
        (
            "  다운로드 완료: "
            f"{len(pdf_bytes):,} bytes"
        )
    )

    # --------------------------------------------------------
    # 3
    # --------------------------------------------------------

    print(
        "\n[3/5] 독립 코드 Audit"
    )

    audit = (
        local_audit(
            pdf_bytes
        )
    )

    print(
        (
            f"  페이지 수: "
            f"{audit['page_count']}"
        )
    )

    print(
        (
            "  Audit 후보 합계: "
            f"{audit['candidate_count']}개"
        )
    )

    print(
        (
            "  고유 교과목 코드: "
            f"{len(audit['course_codes'])}개"
        )
    )

    for (
        page_no,
        count,
    ) in (
        audit[
            "page_counts"
        ]
        .items()
    ):

        if count:

            print(
                (
                    f"    page "
                    f"{page_no}: "
                    f"{count}개"
                )
            )

    # --------------------------------------------------------
    # 4
    # --------------------------------------------------------

    print(
        "\n[4/5] PyMuPDF 결정론적 구조화 v4"
    )

    (
        courses,
        diagnostics,
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

    print_table_diagnostics(
        diagnostics
    )

    print_validation_summary(
        validation
    )

    assert_validation_passed(
        validation
    )

    print_preview(
        courses
    )

    # --------------------------------------------------------
    # 5
    # --------------------------------------------------------

    if save_db:

        print(
            "\n[5/5] PostgreSQL 적재"
        )

        db_result = (
            save_to_database(
                source=(
                    source
                ),

                pdf_bytes=(
                    pdf_bytes
                ),

                courses=(
                    courses
                ),

                validation=(
                    validation
                ),
            )
        )

        print(
            "\n[DB 적재 완료]"
        )

        print(
            (
                f"  총: "
                f"{db_result['total']}개"
            )
        )

        print(
            (
                f"  신규: "
                f"{db_result['created']}개"
            )
        )

        print(
            (
                f"  갱신: "
                f"{db_result['updated']}개"
            )
        )

        print(
            (
                "  Parent SourceDocument ID: "
                f"{db_result['parent_source_document_id']}"
            )
        )

    else:

        print(
            "\n[5/5] Dry run - DB 저장 생략"
        )

        db_result = None

    print(
        "\n"
        + "=" * 72
    )

    print(
        "교육과정 수집 작업 완료"
    )

    print(
        "=" * 72
    )

    return {
        "source": {
            "page_url":
                source.page_url,

            "pdf_url":
                source.pdf_url,

            "file_name":
                source.file_name,
        },

        "parser":
            "pymupdf_deterministic_v4",

        "llm_used":
            False,

        "audit": {
            **audit,

            "course_codes":
                sorted(
                    audit[
                        "course_codes"
                    ]
                ),
        },

        "courses":
            courses,

        "validation":
            validation,

        "db":
            db_result,
    }


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
            DEFAULT_CURRICULUM_YEAR
        ),
    )

    parser.add_argument(
        "--department",

        default=(
            DEFAULT_DEPARTMENT
        ),
    )

    parser.add_argument(
        "--dry-run",

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
        curriculum_year=(
            args.year
        ),

        department_name=(
            args.department
        ),

        save_db=(
            not args.dry_run
        ),
    )


if __name__ == "__main__":

    main()