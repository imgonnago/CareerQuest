from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import pymupdf
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.db.database import engine
from src.loaders.course_loader import (
    upsert_course_record,
    upsert_curriculum_document,
)


# ============================================================
# 기본 설정
# ============================================================

KONYANG_AI_CURRICULUM_PAGE = (
    "https://ai.konyang.ac.kr/prog/curriculum/ai/sub01_05/view.do"
)

DEFAULT_DEPARTMENT = "인공지능학과"
DEFAULT_CURRICULUM_YEAR = 2026
DEFAULT_REVISION_TYPE = "BASE"

HTTP_TIMEOUT = 30

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash",
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0 Safari/537.36"
)

# 건양대 교육과정에서 확인한 실제 교과목 코드 형식
# 예:
# 00043L
# 00018M
# 61901A
STRICT_COURSE_CODE_RE = re.compile(
    r"^\d{5}[A-Za-z]$"
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
    revision_type: str = DEFAULT_REVISION_TYPE


# ============================================================
# Gemini 구조화 출력
# ============================================================

class GeminiCourse(BaseModel):
    source_page: int | None = None
    year_level: int | None = None
    semester: str | None = None
    completion_type: str | None = None
    course_code: str | None = None
    name: str
    credits: float | None = None
    theory_hours: float | None = None
    practice_hours: float | None = None


class GeminiCurriculum(BaseModel):
    courses: list[GeminiCourse] = Field(
        default_factory=list
    )


# ============================================================
# Gemini Prompt
# ============================================================

GEMINI_PROMPT = """
당신은 대한민국 대학의 공식 교육과정 PDF를
CareerQuest 데이터베이스용으로 정확하게 구조화하는 데이터 추출기입니다.

이 문서는 건양대학교 인공지능학과의 공식 교육과정 PDF입니다.

PDF 전체를 시각적으로 확인해서 실제 교과목 행만 추출하세요.
텍스트 레이어만 읽지 말고 표의 배치, 병합 셀,
이미지 형태로 들어간 표도 함께 확인하세요.

각 교과목에서 다음 필드를 추출하세요.

- source_page
  PDF 페이지 번호. 첫 페이지는 1입니다.

- year_level
  이수 학년. 1, 2, 3, 4 등의 정수입니다.

- semester
  학기. 예: "1", "2", "하계", "동계".

- completion_type
  교과영역 또는 이수구분입니다.

- course_code
  교과목 코드입니다.

- name
  교과목명입니다.

- credits
  학점입니다.

- theory_hours
  이론 시수입니다.

- practice_hours
  실습 시수입니다.

반드시 다음 규칙을 지키세요.

1. PDF에 실제로 존재하는 교과목만 반환하세요.
2. 보이지 않는 값은 추측하지 말고 null로 두세요.
3. 표 제목, 컬럼명, 안내문, 페이지 제목은 교과목으로 만들지 마세요.
4. 합계, 소계, 계, 이수학점, 졸업학점 등의 행은 제외하세요.
5. 병합 셀의 학년, 학기, 이수구분은 해당 묶음의 뒤 행에 이어서 적용하세요.
6. 여러 페이지에 존재하는 교과목을 모두 확인하세요.
7. 동일한 교과목 행을 중복 생성하지 마세요.
8. 교과목명은 PDF의 표기를 그대로 유지하세요.
9. 학점과 시수는 가능한 경우 숫자로 반환하세요.
10. source_page는 실제 PDF 페이지 기준으로 기록하세요.
11. 교과목 코드와 교과목명을 서로 다른 행에서 임의로 결합하지 마세요.
12. 설명문, 역량표, 교육목표 등에 등장하는 단어를 교과목으로 오인하지 마세요.
13. 교과목 코드가 실제 표에서 확인되지 않으면 임의로 생성하지 마세요.
14. 교과목 표가 아닌 페이지에서는 교과목을 생성하지 마세요.
15. "DYT_무대분장 외 33개", "선택적 교양필수" 같은
    분류/요약 항목은 개별 교과목으로 반환하지 마세요.

목적은 OCR 원문 복원이 아니라
CareerQuest PostgreSQL DB에 저장할
신뢰 가능한 교과목 데이터를 구조화하는 것입니다.
""".strip()


# ============================================================
# 문자열 유틸
# ============================================================

def clean_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()


def optional_text(
    value: Any,
) -> str | None:

    value = clean_text(
        value
    )

    return value or None


def normalize_name(
    value: str | None,
) -> str | None:

    value = optional_text(
        value
    )

    if not value:
        return None

    compact = value.replace(
        " ",
        "",
    )

    if compact in BLOCKED_NAMES:
        return None

    return value


def normalize_semester(
    value: str | None,
) -> str | None:

    value = optional_text(
        value
    )

    if not value:
        return None

    compact = (
        value
        .replace(
            "학기",
            "",
        )
        .strip()
    )

    if compact in {
        "1",
        "1.0",
    }:
        return "1"

    if compact in {
        "2",
        "2.0",
    }:
        return "2"

    if "하계" in compact:
        return "하계"

    if "동계" in compact:
        return "동계"

    return value


def normalize_number(
    value: float | None,
) -> float | None:

    if value is None:
        return None

    value = float(
        value
    )

    if value < 0:
        return None

    return value


# ============================================================
# Gemini Course -> CareerQuest Course dict
# ============================================================

def normalize_course(
    item: GeminiCourse,
    source: CurriculumSource,
    page_count: int,
) -> dict | None:

    name = normalize_name(
        item.name
    )

    if not name:
        return None


    source_page = (
        item.source_page
    )

    if (
        source_page is None
        or not (
            1 <= source_page <= page_count
        )
    ):
        source_page = 1


    year_level = (
        item.year_level
    )

    if (
        year_level is not None
        and not (
            1 <= year_level <= 6
        )
    ):
        year_level = None


    credits = normalize_number(
        item.credits
    )

    theory_hours = normalize_number(
        item.theory_hours
    )

    practice_hours = normalize_number(
        item.practice_hours
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
            "gemini-pdf",

        "source_url":
            source.pdf_url,
    }


# ============================================================
# 중복 제거
# ============================================================

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

        code = clean_text(
            course.get(
                "course_code"
            )
        ).upper()


        name = clean_text(
            course.get(
                "name"
            )
        ).lower()


        year_level = (
            course.get(
                "year_level"
            )
        )


        semester = clean_text(
            course.get(
                "semester"
            )
        ).lower()


        if code:

            key = (
                "code",
                code,
            )

        else:

            key = (
                "name",
                name,
                year_level,
                semester,
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

            "Accept-Language":
                "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )


    return session


# ============================================================
# PDF URL 추출
# ============================================================

def extract_pdf_from_link(
    link_value: str,
    *,
    base_url: str,
) -> str | None:

    if not link_value:
        return None


    decoded = unquote(
        link_value
    )


    absolute = urljoin(
        base_url,
        link_value,
    )


    parsed = urlparse(
        absolute
    )


    # 직접 PDF 링크
    if (
        parsed.path
        .lower()
        .endswith(
            ".pdf"
        )
    ):

        return absolute


    # PDF.js viewer
    query = parse_qs(
        parsed.query
    )


    if query.get(
        "file"
    ):

        pdf_path = unquote(
            query[
                "file"
            ][0]
        ).strip()


        if (
            pdf_path
            .lower()
            .endswith(
                ".pdf"
            )
        ):

            origin = (
                f"{parsed.scheme}://"
                f"{parsed.netloc}"
            )


            return urljoin(
                origin,
                pdf_path,
            )


    # onclick 등에 PDF가 들어간 경우
    match = re.search(
        r"(/[A-Za-z0-9_./-]+\.pdf)",
        decoded,
        flags=re.IGNORECASE,
    )


    if match:

        return urljoin(
            base_url,
            match.group(1),
        )


    return None


# ============================================================
# 건양대 공식 교육과정 PDF 탐색
# ============================================================

def discover_curriculum_pdf(
    *,
    curriculum_year: int,
    department_name: str,
    page_url: str = (
        KONYANG_AI_CURRICULUM_PAGE
    ),
) -> CurriculumSource:

    session = (
        create_http_session()
    )


    response = session.get(
        page_url,
        timeout=HTTP_TIMEOUT,
    )


    response.raise_for_status()


    if response.apparent_encoding:

        response.encoding = (
            response.apparent_encoding
        )


    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )


    target_phrase = (
        f"{curriculum_year}학년도 "
        f"{department_name} 교육과정"
    )


    pdf_candidates: list[
        str
    ] = []


    for anchor in soup.find_all(
        "a"
    ):

        anchor_text = clean_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        )


        if (
            target_phrase
            not in anchor_text
        ):

            continue


        href = clean_text(
            anchor.get(
                "href"
            )
        )


        onclick = clean_text(
            anchor.get(
                "onclick"
            )
        )


        for value in (
            href,
            onclick,
        ):

            pdf_url = (
                extract_pdf_from_link(
                    value,
                    base_url=page_url,
                )
            )


            if pdf_url:

                pdf_candidates.append(
                    pdf_url
                )


    pdf_candidates = list(
        dict.fromkeys(
            pdf_candidates
        )
    )


    if not pdf_candidates:

        raise RuntimeError(
            (
                "건양대 공식 페이지에서 "
                "교육과정 PDF를 찾지 못했습니다.\n"
                f"대상: {target_phrase}\n"
                f"페이지: {page_url}"
            )
        )


    pdf_url = (
        pdf_candidates[0]
    )


    file_name = Path(
        urlparse(
            pdf_url
        ).path
    ).name


    if not file_name:

        file_name = (
            f"{curriculum_year}_"
            f"{department_name}_"
            "교육과정.pdf"
        )


    return CurriculumSource(
        page_url=page_url,

        pdf_url=pdf_url,

        file_name=file_name,

        department_name=(
            department_name
        ),

        curriculum_year=(
            curriculum_year
        ),

        revision_type=(
            DEFAULT_REVISION_TYPE
        ),
    )


# ============================================================
# PDF 다운로드
# ============================================================

def download_pdf_bytes(
    source: CurriculumSource,
) -> bytes:

    session = (
        create_http_session()
    )


    response = session.get(
        source.pdf_url,

        timeout=HTTP_TIMEOUT,

        headers={
            "Referer":
                source.page_url,
        },
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
                "다운로드 결과가 PDF가 아닙니다.\n"
                f"URL: {source.pdf_url}\n"
                "Content-Type: "
                f"{response.headers.get('Content-Type')}"
            )
        )


    return data


# ============================================================
# 로컬 파서 감사(audit)
#
# Gemini 결과와 비교하기 위한 독립 검증용.
# 결과 확정용으로 사용하지 않는다.
# ============================================================

def local_audit(
    pdf_bytes: bytes,
) -> dict:

    document = pymupdf.open(
        stream=pdf_bytes,
        filetype="pdf",
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
                page_index + 1
            )


            page_codes: set[
                str
            ] = set()


            # ------------------------------------------------
            # 1. PyMuPDF table detector
            # ------------------------------------------------

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

                    cells = [
                        clean_text(
                            cell
                        )
                        for cell
                        in row
                    ]


                    for cell in cells:

                        if (
                            STRICT_COURSE_CODE_RE
                            .fullmatch(
                                cell
                            )
                        ):

                            page_codes.add(
                                cell.upper()
                            )


            # ------------------------------------------------
            # 2. 텍스트 단어에서도 코드 탐색
            # ------------------------------------------------

            words = (
                page.get_text(
                    "words"
                )
            )


            for word in words:

                token = clean_text(
                    word[4]
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
# Gemini PDF 전체 구조화
# ============================================================

def extract_with_gemini(
    pdf_bytes: bytes,
    source: CurriculumSource,
    page_count: int,
) -> list[dict]:

    if not (
        os.getenv(
            "GEMINI_API_KEY"
        )
        or
        os.getenv(
            "GOOGLE_API_KEY"
        )
    ):

        raise RuntimeError(
            (
                "Gemini API 키가 없습니다.\n\n"
                "PyCharm에서:\n"
                "Run -> Edit Configurations -> "
                "Environment variables\n\n"
                "GEMINI_API_KEY=발급받은키\n"
                "를 추가하세요."
            )
        )


    client = (
        genai.Client()
    )


    prompt = (
        GEMINI_PROMPT
        + "\n\n"
        + "대상 메타데이터:\n"
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
            f"{page_count}\n"
        )
    )


    response = (
        client.models.generate_content(
            model=(
                GEMINI_MODEL
            ),

            contents=[
                types.Part.from_bytes(
                    data=(
                        pdf_bytes
                    ),

                    mime_type=(
                        "application/pdf"
                    ),
                ),

                prompt,
            ],

            config=(
                types.GenerateContentConfig(
                    response_mime_type=(
                        "application/json"
                    ),

                    response_schema=(
                        GeminiCurriculum
                    ),

                    temperature=0,
                )
            ),
        )
    )


    parsed = getattr(
        response,
        "parsed",
        None,
    )


    if parsed is None:

        if not response.text:

            raise RuntimeError(
                (
                    "Gemini가 빈 응답을 "
                    "반환했습니다."
                )
            )


        parsed = (
            GeminiCurriculum
            .model_validate_json(
                response.text
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


    # --------------------------------------------------------
    # 실제 교과목이 아닌 분류/요약 행 제거
    #
    # 현재 건양대 교육과정표의 실제 교과목은
    # 모두 5자리 숫자 + 영문 1자의 코드 보유
    # --------------------------------------------------------

    filtered_courses: list[
        dict
    ] = []


    for course in courses:

        course_code = clean_text(
            course.get(
                "course_code"
            )
        ).upper()


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
        ] = course_code


        filtered_courses.append(
            course
        )


    return (
        deduplicate_courses(
            filtered_courses
        )
    )


# ============================================================
# Gemini 결과 검증
# ============================================================

def validate_gemini_result(
    courses: list[dict],
    audit: dict,
) -> dict:

    if not courses:

        raise RuntimeError(
            (
                "Gemini가 교과목을 "
                "하나도 추출하지 못했습니다."
            )
        )


    if len(
        courses
    ) > 300:

        raise RuntimeError(
            (
                "Gemini 추출량이 "
                "비정상적으로 큽니다: "
                f"{len(courses)}개"
            )
        )


    gemini_codes = {
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
    }


    local_codes = (
        audit[
            "course_codes"
        ]
    )


    overlap = (
        gemini_codes
        & local_codes
    )


    missing_in_gemini = sorted(
        local_codes
        - gemini_codes
    )


    gemini_only = sorted(
        gemini_codes
        - local_codes
    )


    no_code_courses = [
        course

        for course
        in courses

        if not clean_text(
            course.get(
                "course_code"
            )
        )
    ]


    local_overlap_ratio = (
        len(
            overlap
        )
        / len(
            local_codes
        )

        if local_codes

        else None
    )


    return {
        "gemini_course_count":
            len(
                courses
            ),

        "gemini_coded_count":
            len(
                gemini_codes
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
            local_overlap_ratio,

        "missing_in_gemini":
            missing_in_gemini,

        "gemini_only":
            gemini_only,

        "no_code_courses":
            no_code_courses,
    }


# ============================================================
# DB 적재 검증 게이트
# ============================================================

def assert_validation_passed(
    validation: dict,
) -> None:

    errors: list[
        str
    ] = []


    gemini_course_count = validation[
        "gemini_course_count"
    ]


    gemini_coded_count = validation[
        "gemini_coded_count"
    ]


    local_code_count = validation[
        "local_code_count"
    ]


    overlap_count = validation[
        "overlap_count"
    ]


    local_overlap_ratio = validation[
        "local_overlap_ratio"
    ]


    missing_in_gemini = validation[
        "missing_in_gemini"
    ]


    gemini_only = validation[
        "gemini_only"
    ]


    no_code_courses = validation[
        "no_code_courses"
    ]


    # Gemini 결과의 모든 교과목이 코드 보유
    if (
        gemini_course_count
        != gemini_coded_count
    ):

        errors.append(
            (
                "Gemini 교과목 수와 "
                "코드 보유 수가 다릅니다: "
                f"{gemini_course_count} / "
                f"{gemini_coded_count}"
            )
        )


    # 로컬 코드 수와 Gemini 코드 수 동일
    if (
        gemini_coded_count
        != local_code_count
    ):

        errors.append(
            (
                "Gemini 코드 수와 "
                "로컬 코드 수가 다릅니다: "
                f"{gemini_coded_count} / "
                f"{local_code_count}"
            )
        )


    # 코드 집합 100% 일치
    if (
        overlap_count
        != local_code_count
    ):

        errors.append(
            (
                "로컬 코드 전체가 "
                "Gemini와 일치하지 않습니다: "
                f"{overlap_count} / "
                f"{local_code_count}"
            )
        )


    if (
        local_overlap_ratio is None
        or local_overlap_ratio < 1.0
    ):

        errors.append(
            (
                "코드 일치율이 "
                "100%가 아닙니다: "
                f"{local_overlap_ratio}"
            )
        )


    if missing_in_gemini:

        errors.append(
            (
                "로컬에는 있지만 "
                "Gemini에는 없는 코드: "
                f"{missing_in_gemini}"
            )
        )


    if gemini_only:

        errors.append(
            (
                "Gemini에만 있는 코드: "
                f"{gemini_only}"
            )
        )


    if no_code_courses:

        errors.append(
            (
                "코드 없는 Gemini 교과목이 "
                "존재합니다."
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
            f"  Gemini 교과목: "
            f"{gemini_course_count}개"
        )
    )


    print(
        (
            f"  로컬 교과목 코드: "
            f"{local_code_count}개"
        )
    )


    print(
        (
            f"  코드 일치: "
            f"{overlap_count}개"
        )
    )


    print(
        "  일치율: 100.0%"
    )


    print(
        "  -> PostgreSQL 적재 허용"
    )


# ============================================================
# SourceDocument용 텍스트
# ============================================================

def build_source_content(
    courses: list[dict],
) -> str:

    lines = [
        (
            "페이지 | 학년 | 학기 | 이수구분 | "
            "교과목코드 | 교과목명 | 학점 | 이론 | 실습"
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

    content = build_source_content(
        courses
    )

    created_count = 0
    updated_count = 0

    # Session이 닫힌 뒤 ORM 객체를 읽지 않도록
    # ID만 일반 정수로 미리 저장
    parent_source_document_id = None


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
                            "gemini_pdf_with_local_audit",

                        "gemini_model":
                            GEMINI_MODEL,

                        "pdf_sha256":
                            hashlib.sha256(
                                pdf_bytes
                            ).hexdigest(),

                        "course_count":
                            len(
                                courses
                            ),

                        "validation": {
                            "gemini_course_count":
                                validation[
                                    "gemini_course_count"
                                ],

                            "gemini_coded_count":
                                validation[
                                    "gemini_coded_count"
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


            # 중요:
            # Session이 살아 있을 때 ID를 꺼내둔다.
            parent_source_document_id = (
                parent_document.source_document_id
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


                if result.get(
                    "created"
                ):

                    created_count += 1
                    status = "CREATED"

                else:

                    updated_count += 1
                    status = "UPDATED"


                print(
                    (
                        f"  "
                        f"[{index:02d}/"
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


    # 여기서는 ORM 객체 parent_document에
    # 다시 접근하지 않는다.
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
# 콘솔 미리보기
# ============================================================

def print_preview(
    courses: list[dict],
    limit: int = 40,
) -> None:

    print(
        "\n[Gemini 추출 미리보기]"
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
                f"p{course.get('source_page')} | "
                f"{course.get('year_level') or '-'}학년 | "
                f"{course.get('semester') or '-'}학기 | "
                f"{course.get('completion_type') or '-'} | "
                f"{course.get('course_code') or '-'} | "
                f"{course.get('name')} | "
                f"{course.get('credits') if course.get('credits') is not None else '-'}학점"
            )
        )


    if len(
        courses
    ) > limit:

        print(
            (
                "... 외 "
                f"{len(courses) - limit}개"
            )
        )


# ============================================================
# 상세 불일치 출력
# ============================================================

def print_mismatch_details(
    validation: dict,
) -> None:

    print(
        "\n[상세 불일치 검사]"
    )


    print(
        (
            "  로컬에는 있지만 "
            "Gemini에는 없는 코드: "
            f"{validation['missing_in_gemini']}"
        )
    )


    print(
        (
            "  Gemini에만 있는 코드: "
            f"{validation['gemini_only']}"
        )
    )


    print(
        "\n  코드 없는 Gemini 항목:"
    )


    no_code_courses = (
        validation[
            "no_code_courses"
        ]
    )


    if not no_code_courses:

        print(
            "    없음"
        )

        return


    for course in (
        no_code_courses
    ):

        print(
            (
                f"    "
                f"p{course.get('source_page')} | "
                f"{course.get('year_level') or '-'}학년 | "
                f"{course.get('semester') or '-'}학기 | "
                f"{course.get('completion_type') or '-'} | "
                f"{course.get('name')}"
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
            "수집/검증/적재"
        )
    )


    print(
        "=" * 72
    )


    # --------------------------------------------------------
    # 1. PDF 탐색
    # --------------------------------------------------------

    print(
        (
            "\n[1/5] "
            "공식 교육과정 PDF 탐색"
        )
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
    # 3. 로컬 감사
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
    ) in (
        audit[
            "page_counts"
        ].items()
    ):

        if count:

            print(
                (
                    f"    page "
                    f"{page_no}: "
                    f"{count}개 후보"
                )
            )


    # --------------------------------------------------------
    # 4. Gemini 전체 PDF 구조화
    # --------------------------------------------------------

    print(
        (
            f"\n[4/5] "
            f"{GEMINI_MODEL} "
            "PDF 전체 구조화"
        )
    )


    courses = (
        extract_with_gemini(
            pdf_bytes,

            source,

            audit[
                "page_count"
            ],
        )
    )


    validation = (
        validate_gemini_result(
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
            "  Gemini 교과목: "
            f"{validation['gemini_course_count']}개"
        )
    )


    print(
        (
            "  Gemini 코드 보유: "
            f"{validation['gemini_coded_count']}개"
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
                "Gemini와 일치: "
                f"{validation['local_overlap_ratio']:.1%}"
            )
        )


    # --------------------------------------------------------
    # DB 저장 허용 여부 최종 판정
    # --------------------------------------------------------

    assert_validation_passed(
        validation
    )


    print_preview(
        courses
    )


    # --------------------------------------------------------
    # 5. DB 저장
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
            "\n[5/5] PostgreSQL 적재 생략"
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
        "source":
            source,

        "audit":
            audit,

        "courses":
            courses,

        "validation":
            validation,

        "db":
            db_result,
    }


# ============================================================
# main
# ============================================================

if __name__ == "__main__":

    run(
        curriculum_year=2026,

        department_name="인공지능학과",

        # 66 / 66, 코드 일치 100% 검증 완료.
        # 이후에도 검증 게이트를 통과해야만 DB에 저장됨.
        save_db=True,
    )