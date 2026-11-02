from __future__ import annotations

import argparse
import hashlib
import re

from dataclasses import dataclass

from typing import Any

from urllib.parse import (
    parse_qs,
    unquote,
    urljoin,
    urlparse,
)

import pymupdf
import requests

from bs4 import BeautifulSoup

from pydantic import (
    BaseModel,
    Field,
)

from sqlalchemy.orm import (
    Session,
)

from engine.llm import (
    LLMConfig,
    LLM_MODEL,
    chat,
)

from src.db.database import engine

from src.loaders.course_loader import (
    upsert_course_record,
    upsert_curriculum_document,
)


# ============================================================
# 설정
# ============================================================

KONYANG_AI_CURRICULUM_PAGE = (
    "https://ai.konyang.ac.kr/"
    "prog/curriculum/ai/sub01_05/view.do"
)

# 공식 페이지에서 PDF 자동 탐색이 실패할 때만 사용
FALLBACK_PDF_URL = (
    "https://ai.konyang.ac.kr/"
    "uploads_clone/curriculum/"
    "STB_202610201036110470.pdf"
)

DEFAULT_DEPARTMENT = (
    "인공지능학과"
)

DEFAULT_CURRICULUM_YEAR = (
    2026
)

DEFAULT_REVISION_TYPE = (
    "BASE"
)

HTTP_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/150.0 Safari/537.36"
)

STRICT_COURSE_CODE_RE = (
    re.compile(
        r"^\d{5}[A-Za-z]$"
    )
)

BLOCKED_NAMES = {
    "합계",
    "소계",
    "계",
    "이수학점",
    "졸업학점",
    "총학점",
    "학점계",
    "교과목",
    "교과목명",
}


CURRICULUM_LLM = (
    LLMConfig(
        model=(
            LLM_MODEL
        ),

        temperature=0,

        think=False,
    )
)


# ============================================================
# 구조
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


class QwenCourse(
    BaseModel
):

    source_page: int | None = None

    year_level: int | None = None

    semester: str | None = None

    completion_type: str | None = None

    course_code: str | None = None

    name: str

    credits: float | None = None

    theory_hours: float | None = None

    practice_hours: float | None = None


class QwenCurriculum(
    BaseModel
):

    courses: list[
        QwenCourse
    ] = Field(
        default_factory=list
    )


# ============================================================
# Qwen Prompt
# ============================================================

QWEN_PROMPT = """
당신은 대한민국 대학 공식 교육과정 자료를
CareerQuest 데이터베이스용으로
정확하게 구조화하는 데이터 추출기입니다.

아래에는 PDF에서 PyMuPDF로 추출한
페이지별 텍스트와 표 데이터가 제공됩니다.

제공된 내용만 사용하고
보이지 않는 정보를 추측하거나
새로 만들지 마세요.

각 실제 교과목 행에서 다음 필드를 추출하세요.

- source_page: PAGE 번호
- year_level: 이수 학년
- semester: 학기
- completion_type: 이수구분 또는 교과영역
- course_code: 교과목 코드
- name: 교과목명
- credits: 학점
- theory_hours: 이론 시수
- practice_hours: 실습 시수

규칙:

1. 실제 교과목만 반환하세요.
2. 확인되지 않는 값은 null로 두세요.
3. 표 제목, 헤더, 안내문은 교과목이 아닙니다.
4. 합계, 소계, 계, 이수학점, 졸업학점 등의 행은 제외하세요.
5. 병합 셀 때문에 학년/학기가 뒤 행에서 비어 있으면 같은 표 영역의 앞 행 정보를 이어서 적용할 수 있습니다.
6. PAGE 번호를 source_page로 사용하세요.
7. 동일한 교과목을 중복 생성하지 마세요.
8. 교과목명은 원문 표기를 유지하세요.
9. course_code를 임의로 생성하지 마세요.
10. 서로 다른 행의 코드와 이름을 임의로 결합하지 마세요.
11. 설명문, 역량표, 교육목표 등에 등장하는 단어를 교과목으로 만들지 마세요.
12. TEXT와 TABLE이 같은 내용을 중복 포함해도 실제 교과목은 한 번만 반환하세요.
13. TABLE 데이터가 존재하면 행 구조 판단에 우선 활용하세요.
14. 교과목 코드가 없는 분류/요약 항목은 반환하지 마세요.

목적은 OCR 원문 복원이 아니라
PostgreSQL에 저장할 신뢰 가능한
교과목 행 구조화입니다.
""".strip()


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

    return clean_text(
        value
    )


def normalize_semester(
    value: Any,
) -> str | None:

    value = (
        clean_text(
            value
        )
    )

    if not value:
        return None

    compact = (
        value.replace(
            " ",
            "",
        )
    )

    mapping = {
        "1학기":
            "1",

        "2학기":
            "2",

        "1":
            "1",

        "2":
            "2",

        "하계":
            "하계",

        "동계":
            "동계",
    }

    return (
        mapping.get(
            compact,
            value,
        )
    )


def normalize_number(
    value: Any,
) -> float | None:

    if value is None:
        return None

    try:

        number = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):

        text_value = (
            clean_text(
                value
            )
        )

        match = (
            re.search(
                r"\d+(?:\.\d+)?",
                text_value,
            )
        )

        if not match:
            return None

        number = float(
            match.group()
        )

    if number < 0:
        return None

    return number


# ============================================================
# Course 정규화
# ============================================================

def normalize_course(
    item: QwenCourse,

    source: CurriculumSource,

    page_count: int,
) -> dict | None:

    name = (
        normalize_name(
            item.name
        )
    )

    if not name:
        return None

    if (
        name.replace(
            " ",
            "",
        )
        in BLOCKED_NAMES
    ):

        return None

    source_page = (
        item.source_page
    )

    if (
        source_page is None

        or not (
            1
            <= source_page
            <= page_count
        )
    ):

        source_page = 1

    year_level = (
        item.year_level
    )

    if (
        year_level is not None

        and not (
            1
            <= year_level
            <= 6
        )
    ):

        year_level = None

    credits = (
        normalize_number(
            item.credits
        )
    )

    theory_hours = (
        normalize_number(
            item.theory_hours
        )
    )

    practice_hours = (
        normalize_number(
            item.practice_hours
        )
    )

    if (
        credits is not None
        and credits > 12
    ):

        credits = None

    if (
        theory_hours is not None
        and theory_hours > 20
    ):

        theory_hours = None

    if (
        practice_hours is not None
        and practice_hours > 30
    ):

        practice_hours = None

    return {
        "department_name":
            source.department_name,

        "curriculum_year":
            source.curriculum_year,

        "revision_type":
            source.revision_type,

        "course_code":
            optional_text(
                item.course_code
            ),

        "name":
            name,

        "year_level":
            year_level,

        "semester":
            normalize_semester(
                item.semester
            ),

        "completion_type":
            optional_text(
                item.completion_type
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
            source_page,

        "source_table":
            "qwen-local-text",

        "source_url":
            source.pdf_url,
    }


def deduplicate_courses(
    courses: list[dict],
) -> list[dict]:

    result: list[
        dict
    ] = []

    seen: set[
        tuple
    ] = set()

    for course in courses:

        key = (
            clean_text(
                course.get(
                    "course_code"
                )
            ).upper(),

            clean_text(
                course.get(
                    "name"
                )
            ),

            course.get(
                "year_level"
            ),

            course.get(
                "semester"
            ),
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        result.append(
            course
        )

    return result


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
        }
    )

    return session


# ============================================================
# PDF 탐색
# ============================================================
def extract_pdf_from_link(
    href: str,
    base_url: str,
) -> str | None:

    href = clean_text(
        href
    )

    if not href:
        return None

    absolute_url = urljoin(
        base_url,
        href,
    )

    parsed = urlparse(
        absolute_url
    )

    # 이미 PDF 직접 링크
    if (
        parsed.path
        .lower()
        .endswith(
            ".pdf"
        )
    ):
        return absolute_url

    # pdf.js viewer 형태:
    #
    # /pdf.js/web/viewer.html
    # ?file=/uploads/.../xxx.pdf
    query = parse_qs(
        parsed.query
    )

    file_values = (
        query.get(
            "file"
        )
        or []
    )

    for file_value in file_values:

        file_value = unquote(
            file_value
        )

        if (
            ".pdf"
            not in file_value.lower()
        ):
            continue

        return urljoin(
            absolute_url,
            file_value,
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

    pdf_url: str | None = None

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

        soup = (
            BeautifulSoup(
                response.text,
                "html.parser",
            )
        )

        candidates: list[str] = []

        # ----------------------------------------------------
        # href 링크 탐색
        # ----------------------------------------------------

        for tag in (
            soup.find_all(
                href=True
            )
        ):

            href = clean_text(
                tag.get(
                    "href"
                )
            )

            extracted = (
                extract_pdf_from_link(
                    href,
                    KONYANG_AI_CURRICULUM_PAGE,
                )
            )

            if extracted:
                candidates.append(
                    extracted
                )

        # ----------------------------------------------------
        # HTML 내부 문자열에 직접 들어간 PDF / viewer URL 탐색
        # ----------------------------------------------------

        for raw_url in re.findall(
            r"""(?:"|')([^"']+(?:\.pdf|viewer\.html\?file=[^"']+))(?:"|')""",
            response.text,
            flags=re.IGNORECASE,
        ):

            extracted = (
                extract_pdf_from_link(
                    raw_url,
                    KONYANG_AI_CURRICULUM_PAGE,
                )
            )

            if extracted:
                candidates.append(
                    extracted
                )

        unique_candidates = list(
            dict.fromkeys(
                candidates
            )
        )

        if unique_candidates:

            # 현재 인공지능학과 공식 페이지에서
            # 발견되는 교육과정 PDF 사용
            pdf_url = (
                unique_candidates[
                    0
                ]
            )

    except requests.RequestException as exc:

        print(
            (
                "교육과정 페이지 "
                "자동 탐색 실패: "
                f"{exc}"
            )
        )

    # --------------------------------------------------------
    # 공식 페이지 구조 변경 등에 대비한 fallback
    # --------------------------------------------------------

    if not pdf_url:

        print(
            (
                "공식 페이지에서 PDF "
                "자동 탐색 실패 "
                "-> fallback URL 사용"
            )
        )

        pdf_url = (
            FALLBACK_PDF_URL
        )

    file_name = (
        urlparse(
            pdf_url
        )
        .path
        .rsplit(
            "/",
            1,
        )[
            -1
        ]
        or "curriculum.pdf"
    )

    return CurriculumSource(
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
# 로컬 Audit
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
                document.load_page(
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

            # 표 탐색
            try:

                finder = (
                    page.find_tables()
                )

                tables = (
                    getattr(
                        finder,
                        "tables",
                        None,
                    )
                    or []
                )

            except Exception:

                tables = []

            for table in tables:

                try:

                    rows = (
                        table.extract()
                    )

                except Exception:

                    continue

                for row in (
                    rows
                    or []
                ):

                    for cell in row:

                        token = (
                            clean_text(
                                cell
                            )
                        )

                        if (
                            STRICT_COURSE_CODE_RE
                            .fullmatch(
                                token
                            )
                        ):

                            page_codes.add(
                                token.upper()
                            )

            # 텍스트 레이어에서도 코드 탐색
            for word in (
                page.get_text(
                    "words"
                )
            ):

                token = (
                    clean_text(
                        word[
                            4
                        ]
                    )
                )

                if (
                    STRICT_COURSE_CODE_RE
                    .fullmatch(
                        token
                    )
                ):

                    page_codes.add(
                        token.upper()
                    )

            course_codes.update(
                page_codes
            )

            page_counts[
                page_no
            ] = len(
                page_codes
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
                page_counts.values()
            ),
    }


# ============================================================
# PDF -> Qwen Context
# ============================================================

def build_qwen_pdf_context(
    pdf_bytes: bytes,
) -> str:

    document = (
        pymupdf.open(
            stream=(
                pdf_bytes
            ),

            filetype="pdf",
        )
    )

    page_blocks: list[
        str
    ] = []

    try:

        for page_index in range(
            document.page_count
        ):

            page = (
                document.load_page(
                    page_index
                )
            )

            page_no = (
                page_index
                + 1
            )

            page_text = (
                page.get_text(
                    "text",
                    sort=True,
                )
                .strip()
            )

            table_blocks: list[
                str
            ] = []

            table_codes: set[
                str
            ] = set()

            try:

                finder = (
                    page.find_tables()
                )

                tables = (
                    getattr(
                        finder,
                        "tables",
                        None,
                    )
                    or []
                )

            except Exception:

                tables = []

            for (
                table_index,
                table,
            ) in enumerate(
                tables,
                start=1,
            ):

                try:

                    rows = (
                        table.extract()
                    )

                except Exception:

                    continue

                row_lines: list[
                    str
                ] = []

                for row in (
                    rows
                    or []
                ):

                    cells = [
                        clean_text(
                            cell
                        )

                        for cell in row
                    ]

                    if not any(
                        cells
                    ):
                        continue

                    for cell in cells:

                        if (
                            STRICT_COURSE_CODE_RE
                            .fullmatch(
                                cell
                            )
                        ):

                            table_codes.add(
                                cell.upper()
                            )

                    row_lines.append(
                        " | ".join(
                            cells
                        )
                    )

                if row_lines:

                    table_blocks.append(
                        (
                            f"[TABLE "
                            f"{table_index}]\n"
                            + "\n".join(
                                row_lines
                            )
                        )
                    )

            page_codes = {
                clean_text(
                    word[
                        4
                    ]
                ).upper()

                for word in (
                    page.get_text(
                        "words"
                    )
                )

                if (
                    STRICT_COURSE_CODE_RE
                    .fullmatch(
                        clean_text(
                            word[
                                4
                            ]
                        )
                    )
                )
            }

            table_text = (
                "\n\n".join(
                    table_blocks
                )
                or "(표 추출 없음)"
            )

            # 표가 페이지의 모든 교과목 코드를
            # 포함한다면 중복되는 전체 텍스트는
            # 보내지 않아 context를 절약한다.
            needs_text = (
                not table_blocks

                or not (
                    page_codes
                    .issubset(
                        table_codes
                    )
                )
            )

            text_value = (
                page_text

                if needs_text

                else (
                    "(TABLE 데이터로 "
                    "충분하여 생략)"
                )
            )

            text_value = (
                text_value
                or "(텍스트 추출 없음)"
            )

            page_blocks.append(
                (
                    f"===== PAGE "
                    f"{page_no} =====\n"

                    f"[TABLES]\n"
                    f"{table_text}\n\n"

                    f"[TEXT]\n"
                    f"{text_value}"
                )
            )

    finally:

        document.close()

    return (
        "\n\n".join(
            page_blocks
        )
    )


# ============================================================
# Qwen 구조화
# ============================================================

def extract_with_qwen(
    pdf_bytes: bytes,

    source: CurriculumSource,

    page_count: int,
) -> list[dict]:

    pdf_context = (
        build_qwen_pdf_context(
            pdf_bytes
        )
    )

    prompt = (
        QWEN_PROMPT

        + "\n\n"
        + "[대상 메타데이터]\n"

        + "- 학교: 건양대학교\n"

        + (
            f"- 학과: "
            f"{source.department_name}\n"
        )

        + (
            "- 교육과정 연도: "
            f"{source.curriculum_year}\n"
        )

        + (
            "- PDF 총 페이지: "
            f"{page_count}\n\n"
        )

        + "[PDF 추출 데이터]\n"

        + pdf_context
    )

    message = (
        chat(
            [
                {
                    "role":
                        "system",

                    "content":
                        (
                            "당신은 대학 교육과정 "
                            "구조화 데이터 추출기입니다. "
                            "제공된 원문에 없는 정보는 "
                            "생성하지 마세요."
                        ),
                },
                {
                    "role":
                        "user",

                    "content":
                        prompt,
                },
            ],

            CURRICULUM_LLM,

            format=(
                QwenCurriculum
                .model_json_schema()
            ),
        )
    )

    content = (
        clean_text(
            message.get(
                "content"
            )
        )
    )

    if not content:

        raise RuntimeError(
            (
                "Qwen이 빈 응답을 "
                "반환했습니다."
            )
        )

    parsed = (
        QwenCurriculum
        .model_validate_json(
            content
        )
    )

    courses: list[
        dict
    ] = []

    for item in (
        parsed.courses
    ):

        record = (
            normalize_course(
                item,
                source,
                page_count,
            )
        )

        if record:

            courses.append(
                record
            )

    filtered: list[
        dict
    ] = []

    for course in courses:

        course_code = (
            clean_text(
                course.get(
                    "course_code"
                )
            )
            .upper()
        )

        if not course_code:
            continue

        if not (
            STRICT_COURSE_CODE_RE
            .fullmatch(
                course_code
            )
        ):
            continue

        course[
            "course_code"
        ] = (
            course_code
        )

        filtered.append(
            course
        )

    return (
        deduplicate_courses(
            filtered
        )
    )


# ============================================================
# Qwen 결과 검증
# ============================================================

def validate_qwen_result(
    courses: list[dict],

    audit: dict,
) -> dict:

    if not courses:

        raise RuntimeError(
            (
                "Qwen이 교과목을 "
                "하나도 추출하지 못했습니다."
            )
        )

    if (
        len(
            courses
        )
        > 300
    ):

        raise RuntimeError(
            (
                "Qwen 추출량이 "
                "비정상적으로 큽니다: "
                f"{len(courses)}개"
            )
        )

    qwen_codes = {
        clean_text(
            course.get(
                "course_code"
            )
        ).upper()

        for course in courses

        if clean_text(
            course.get(
                "course_code"
            )
        )
    }

    local_codes = (
        audit[
            "course_codes"
        ]
    )

    overlap = (
        qwen_codes
        & local_codes
    )

    no_code_courses = [
        course

        for course in courses

        if not clean_text(
            course.get(
                "course_code"
            )
        )
    ]

    return {
        "qwen_course_count":
            len(
                courses
            ),

        "qwen_coded_count":
            len(
                qwen_codes
            ),

        "local_candidate_count":
            audit[
                "candidate_count"
            ],

        "local_code_count":
            len(
                local_codes
            ),

        "overlap_count":
            len(
                overlap
            ),

        "local_overlap_ratio":
            (
                len(
                    overlap
                )
                / len(
                    local_codes
                )

                if local_codes

                else None
            ),

        "missing_in_qwen":
            sorted(
                local_codes
                - qwen_codes
            ),

        "qwen_only":
            sorted(
                qwen_codes
                - local_codes
            ),

        "no_code_courses":
            no_code_courses,
    }


# ============================================================
# DB 저장 Gate
# ============================================================

def assert_validation_passed(
    validation: dict,
) -> None:

    errors: list[
        str
    ] = []

    if (
        validation[
            "qwen_course_count"
        ]
        != validation[
            "qwen_coded_count"
        ]
    ):

        errors.append(
            (
                "Qwen 교과목 수와 "
                "코드 보유 수가 다릅니다: "
                f"{validation['qwen_course_count']} / "
                f"{validation['qwen_coded_count']}"
            )
        )

    if (
        validation[
            "qwen_coded_count"
        ]
        != validation[
            "local_code_count"
        ]
    ):

        errors.append(
            (
                "Qwen 코드 수와 "
                "로컬 코드 수가 다릅니다: "
                f"{validation['qwen_coded_count']} / "
                f"{validation['local_code_count']}"
            )
        )

    if (
        validation[
            "overlap_count"
        ]
        != validation[
            "local_code_count"
        ]
    ):

        errors.append(
            (
                "로컬 코드 전체가 "
                "Qwen 결과와 일치하지 않습니다: "
                f"{validation['overlap_count']} / "
                f"{validation['local_code_count']}"
            )
        )

    ratio = (
        validation[
            "local_overlap_ratio"
        ]
    )

    if (
        ratio is None
        or ratio < 1.0
    ):

        errors.append(
            (
                "코드 일치율이 "
                "100%가 아닙니다: "
                f"{ratio}"
            )
        )

    if validation[
        "missing_in_qwen"
    ]:

        errors.append(
            (
                "로컬에는 있지만 "
                "Qwen에는 없는 코드: "
                f"{validation['missing_in_qwen']}"
            )
        )

    if validation[
        "qwen_only"
    ]:

        errors.append(
            (
                "Qwen에만 있는 코드: "
                f"{validation['qwen_only']}"
            )
        )

    if validation[
        "no_code_courses"
    ]:

        errors.append(
            (
                "코드 없는 Qwen "
                "교과목이 존재합니다."
            )
        )

    if errors:

        print(
            "\n[검증 실패]"
        )

        for error in errors:

            print(
                f"  - {error}"
            )

        raise RuntimeError(
            (
                "교육과정 검증에 실패하여 "
                "PostgreSQL 적재를 중단합니다."
            )
        )

    print(
        "\n[검증 통과]"
    )

    print(
        (
            f"  Qwen 교과목: "
            f"{validation['qwen_course_count']}개"
        )
    )

    print(
        (
            "  로컬 교과목 코드: "
            f"{validation['local_code_count']}개"
        )
    )

    print(
        (
            "  코드 일치: "
            f"{validation['overlap_count']}개"
        )
    )

    print(
        "  일치율: 100.0%"
    )

    print(
        "  -> PostgreSQL 적재 허용"
    )


# ============================================================
# SourceDocument 내용
# ============================================================

def build_source_content(
    courses: list[dict],
) -> str:

    lines = [
        (
            "건양대학교 인공지능학과 "
            "교육과정"
        )
    ]

    for course in courses:

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

                    str(
                        (
                            course.get(
                                "credits"
                            )

                            if course.get(
                                "credits"
                            )
                            is not None

                            else ""
                        )
                    ),

                    str(
                        (
                            course.get(
                                "theory_hours"
                            )

                            if course.get(
                                "theory_hours"
                            )
                            is not None

                            else ""
                        )
                    ),

                    str(
                        (
                            course.get(
                                "practice_hours"
                            )

                            if course.get(
                                "practice_hours"
                            )
                            is not None

                            else ""
                        )
                    ),
                ]
            )
        )

    return "\n".join(
        lines
    )


# ============================================================
# PostgreSQL 저장
# ============================================================

def save_to_database(
    *,
    source: CurriculumSource,
    pdf_bytes: bytes,
    courses: list[dict],
    validation: dict,
) -> dict:

    content = (
        build_source_content(
            courses
        )
    )

    created_count = 0
    updated_count = 0

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
                            (
                                "qwen_local_text_"
                                "with_local_audit"
                            ),

                        "qwen_model":
                            LLM_MODEL,

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

                        "validation": {
                            "qwen_course_count":
                                validation[
                                    "qwen_course_count"
                                ],

                            "qwen_coded_count":
                                validation[
                                    "qwen_coded_count"
                                ],

                            "local_code_count":
                                validation[
                                    "local_code_count"
                                ],

                            "overlap_count":
                                validation[
                                    "overlap_count"
                                ],

                            "local_overlap_ratio":
                                validation[
                                    "local_overlap_ratio"
                                ],
                        },

                        "source_page_url":
                            source.page_url,
                    },
                )
            )

            session.flush()

            parent_source_document_id = (
                parent_document
                .source_document_id
            )

            for course in courses:

                result = (
                    upsert_course_record(
                        session,

                        course,

                        parent_source_document_id=(
                            parent_source_document_id
                        ),
                    )
                )

                if result[
                    "created"
                ]:

                    created_count += 1

                else:

                    updated_count += 1

            session.commit()

        except Exception:

            session.rollback()

            raise

    return {
        "created":
            created_count,

        "updated":
            updated_count,

        "total":
            len(
                courses
            ),
    }


# ============================================================
# 출력
# ============================================================

def print_preview(
    courses: list[dict],

    limit: int = 40,
) -> None:

    print(
        "\n[Qwen 추출 미리보기]"
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

        print(
            (
                f"{index:03d}. "

                f"p"
                f"{course.get('source_page')}"

                f" | "
                f"{course.get('year_level') or '-'}"
                f"학년"

                f" | "
                f"{course.get('semester') or '-'}"
                f"학기"

                f" | "
                f"{course.get('completion_type') or '-'}"

                f" | "
                f"{course.get('course_code') or '-'}"

                f" | "
                f"{course.get('name')}"

                f" | "
                f"{course.get('credits') if course.get('credits') is not None else '-'}"
                f"학점"
            )
        )

    if (
        len(
            courses
        )
        > limit
    ):

        print(
            (
                "... 외 "
                f"{len(courses) - limit}개"
            )
        )


def print_mismatch_details(
    validation: dict,
) -> None:

    print(
        "\n[상세 불일치 검사]"
    )

    print(
        (
            "  로컬에는 있지만 "
            "Qwen에는 없는 코드: "
            f"{validation['missing_in_qwen']}"
        )
    )

    print(
        (
            "  Qwen에만 있는 코드: "
            f"{validation['qwen_only']}"
        )
    )

    print(
        (
            "  코드 없는 Qwen 항목: "
            f"{len(validation['no_code_courses'])}개"
        )
    )


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
            "CareerQuest - "
            "건양대학교 교육과정 "
            "수집/검증/적재 "
            "(Qwen)"
        )
    )

    print(
        "=" * 72
    )

    # --------------------------------------------------------
    # 1. PDF
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
    # 2. 다운로드
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
    # 3. 로컬 audit
    # --------------------------------------------------------

    print(
        (
            "\n[3/5] "
            "로컬 파서 감사(audit)"
        )
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
            "  로컬 엄격 후보 행: "
            f"{audit['candidate_count']}개"
        )
    )

    print(
        (
            "  로컬 고유 교과목 코드: "
            f"{len(audit['course_codes'])}개"
        )
    )

    for (
        page_no,
        count,
    ) in audit[
        "page_counts"
    ].items():

        if count:

            print(
                (
                    f"    page "
                    f"{page_no}: "
                    f"{count}개 후보"
                )
            )

    # --------------------------------------------------------
    # 4. Qwen
    # --------------------------------------------------------

    print(
        (
            f"\n[4/5] "
            f"{LLM_MODEL} "
            "교육과정 구조화"
        )
    )

    courses = (
        extract_with_qwen(
            pdf_bytes,

            source,

            audit[
                "page_count"
            ],
        )
    )

    validation = (
        validate_qwen_result(
            courses,
            audit,
        )
    )

    print_mismatch_details(
        validation
    )

    print(
        "\n[검증 요약]"
    )

    print(
        (
            f"  Qwen 교과목: "
            f"{validation['qwen_course_count']}개"
        )
    )

    print(
        (
            "  Qwen 코드 보유: "
            f"{validation['qwen_coded_count']}개"
        )
    )

    print(
        (
            "  로컬 고유 코드: "
            f"{validation['local_code_count']}개"
        )
    )

    print(
        (
            "  코드 일치: "
            f"{validation['overlap_count']}개"
        )
    )

    if (
        validation[
            "local_overlap_ratio"
        ]
        is not None
    ):

        print(
            (
                "  로컬 코드 중 "
                "Qwen과 일치: "
                f"{validation['local_overlap_ratio']:.1%}"
            )
        )

    assert_validation_passed(
        validation
    )

    print_preview(
        courses
    )

    # --------------------------------------------------------
    # 5. DB
    # --------------------------------------------------------

    save_result = {
        "created":
            0,

        "updated":
            0,

        "total":
            len(
                courses
            ),
    }

    if save_db:

        print(
            "\n[5/5] PostgreSQL 적재"
        )

        save_result = (
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
            (
                f"  생성 "
                f"{save_result['created']}개 "
                f"/ 갱신 "
                f"{save_result['updated']}개 "
                f"/ 전체 "
                f"{save_result['total']}개"
            )
        )

    else:

        print(
            (
                "\n[5/5] "
                "Dry run - DB 저장 생략"
            )
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

        "model":
            LLM_MODEL,

        "audit": {
            **audit,

            "course_codes":
                sorted(
                    audit[
                        "course_codes"
                    ]
                ),
        },

        "validation":
            validation,

        "save":
            save_result,
    }


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:

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

    return parser.parse_args()


def main() -> None:

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