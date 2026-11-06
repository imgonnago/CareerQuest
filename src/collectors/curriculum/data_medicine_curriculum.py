from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag


DATA_MEDICINE_CURRICULUM_URL = (
    "https://ems.konyang.ac.kr/dm/sub01_04.do"
)

HTTP_TIMEOUT = 30

USER_AGENT = (
    "CareerQuestAI-AcademicProject/0.1 "
    "(university project; low-frequency official curriculum fetch)"
)


@dataclass(frozen=True)
class DataMedicineCurriculumResult:
    target_year: int
    source_url: str
    courses: list[dict]
    years: tuple[int, ...]
    semesters: tuple[str, ...]


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


def compact_text(
    value: Any,
) -> str:

    return re.sub(
        r"\s+",
        "",
        clean_text(value),
    )


def create_http_session(
) -> requests.Session:

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent":
                USER_AGENT,

            "Accept-Language":
                "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )

    return session


def find_curriculum_table(
    soup: BeautifulSoup,
) -> Tag:

    """
    데이터의학과 공식 페이지에서

    학년 | 1학기 | 2학기

    구조의 교육과정 표를 찾는다.
    """

    for table in soup.find_all(
        "table"
    ):

        text = compact_text(
            table.get_text(
                " ",
                strip=True,
            )
        )

        if (
            "학년"
            in text

            and "1학기"
            in text

            and "2학기"
            in text
        ):

            return table

    raise RuntimeError(
        (
            "데이터의학과 공식 페이지에서 "
            "교육과정 표를 찾지 못했습니다."
        )
    )


def parse_year_level(
    value: Any,
) -> int | None:

    match = re.search(
        r"([1-6])\s*학년",
        clean_text(value),
    )

    if not match:
        return None

    return int(
        match.group(1)
    )


def extract_course_names(
    cell: Tag,
) -> list[str]:

    """
    현재 공식 페이지는 각 학기 cell 안에
    li 목록으로 교과목을 제공한다.

    HTML 구조가 조금 변경되는 경우를 대비해
    일반 줄바꿈 텍스트도 fallback으로 지원한다.
    """

    li_items = [
        clean_text(
            li.get_text(
                " ",
                strip=True,
            )
        )

        for li
        in cell.find_all(
            "li"
        )
    ]

    li_items = [
        item
        for item
        in li_items
        if item
    ]

    if li_items:

        return list(
            dict.fromkeys(
                li_items
            )
        )

    raw_lines = [
        clean_text(
            line
        )

        for line
        in cell.get_text(
            "\n",
            strip=True,
        ).splitlines()
    ]

    raw_lines = [
        line

        for line
        in raw_lines

        if line
        not in {
            "",
            "-",
            "•",
            "·",
        }
    ]

    return list(
        dict.fromkeys(
            raw_lines
        )
    )


def build_course_record(
    *,
    target_year: int,
    year_level: int,
    semester: str,
    name: str,
    row_index: int,
    source_url: str,
) -> dict:

    """
    공식 HTML에는 현재
    교과목 코드 / 학점 / 이수구분이 표시되지 않는다.

    없는 값을 임의 생성하지 않는다.
    """

    return {
        "department_name":
            "데이터의학과",

        "curriculum_year":
            target_year,

        "revision_type":
            "BASE",

        "course_code":
            None,

        "name":
            name,

        "year_level":
            year_level,

        "semester":
            semester,

        "completion_type":
            None,

        "credits":
            None,

        "theory_hours":
            None,

        "practice_hours":
            None,

        "source_file_name":
            "data_medicine_curriculum.html",

        "source_page":
            1,

        "source_table":
            "official-html-table",

        "source_row":
            row_index,

        "source_url":
            source_url,

        "source_text":
            (
                f"{year_level}학년 | "
                f"{semester}학기 | "
                f"{name}"
            ),

        "raw_metadata": {
            "parser":
                "data_medicine_official_html_v1",

            "course_code_absent_in_source":
                True,

            "credits_absent_in_source":
                True,

            "completion_type_absent_in_source":
                True,

            "target_year_context":
                target_year,

            "year_note":
                (
                    "공식 HTML 표 자체에는 "
                    "학년도 표기가 없어 "
                    "수집 실행 target_year를 "
                    "curriculum_year로 사용"
                ),
        },
    }


def parse_data_medicine_html(
    html: str,
    *,
    target_year: int,
    source_url: str = (
        DATA_MEDICINE_CURRICULUM_URL
    ),
) -> DataMedicineCurriculumResult:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    table = find_curriculum_table(
        soup
    )

    courses: list[
        dict
    ] = []

    seen: set[
        tuple[
            int,
            str,
            str,
        ]
    ] = set()

    for (
        row_index,
        row,
    ) in enumerate(
        table.find_all(
            "tr"
        ),
        start=1,
    ):

        cells = row.find_all(
            [
                "th",
                "td",
            ],
            recursive=False,
        )

        if len(
            cells
        ) < 3:

            continue

        year_level = (
            parse_year_level(
                cells[
                    0
                ].get_text(
                    " ",
                    strip=True,
                )
            )
        )

        if (
            year_level
            is None
        ):

            continue

        semester_cells = (
            (
                "1",
                cells[
                    1
                ],
            ),

            (
                "2",
                cells[
                    2
                ],
            ),
        )

        for (
            semester,
            cell,
        ) in semester_cells:

            for name in (
                extract_course_names(
                    cell
                )
            ):

                key = (
                    year_level,
                    semester,
                    compact_text(
                        name
                    ),
                )

                if key in seen:

                    continue

                seen.add(
                    key
                )

                courses.append(
                    build_course_record(
                        target_year=(
                            target_year
                        ),

                        year_level=(
                            year_level
                        ),

                        semester=(
                            semester
                        ),

                        name=(
                            name
                        ),

                        row_index=(
                            row_index
                        ),

                        source_url=(
                            source_url
                        ),
                    )
                )

    if not courses:

        raise RuntimeError(
            (
                "데이터의학과 교육과정 과목을 "
                "한 건도 추출하지 못했습니다."
            )
        )

    years = tuple(
        sorted(
            {
                int(
                    item[
                        "year_level"
                    ]
                )

                for item
                in courses
            }
        )
    )

    semesters = tuple(
        sorted(
            {
                str(
                    item[
                        "semester"
                    ]
                )

                for item
                in courses
            }
        )
    )

    if years != (
        1,
        2,
        3,
        4,
    ):

        raise RuntimeError(
            (
                "데이터의학과 학년 구조가 "
                "예상과 다릅니다: "
                f"{years}"
            )
        )

    if semesters != (
        "1",
        "2",
    ):

        raise RuntimeError(
            (
                "데이터의학과 학기 구조가 "
                "예상과 다릅니다: "
                f"{semesters}"
            )
        )

    return (
        DataMedicineCurriculumResult(
            target_year=(
                target_year
            ),

            source_url=(
                source_url
            ),

            courses=(
                courses
            ),

            years=(
                years
            ),

            semesters=(
                semesters
            ),
        )
    )


def fetch_data_medicine_curriculum(
    *,
    target_year: int,
) -> DataMedicineCurriculumResult:

    session = (
        create_http_session()
    )

    response = session.get(
        DATA_MEDICINE_CURRICULUM_URL,
        timeout=(
            HTTP_TIMEOUT
        ),
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
        parse_data_medicine_html(
            response.text,

            target_year=(
                target_year
            ),

            source_url=(
                response.url
            ),
        )
    )