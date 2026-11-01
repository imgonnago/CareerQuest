from __future__ import annotations

import base64
import os
import time
from typing import Optional

import pymupdf
from openai import OpenAI
from pydantic import BaseModel, Field


# ============================================================
# Vision 출력 스키마
# ============================================================

class VisionCourse(BaseModel):
    year_level: Optional[int] = None
    semester: Optional[str] = None
    completion_type: Optional[str] = None
    course_code: Optional[str] = None
    name: str
    credits: Optional[float] = None
    theory_hours: Optional[float] = None
    practice_hours: Optional[float] = None


class VisionCurriculumPage(BaseModel):
    courses: list[VisionCourse] = Field(default_factory=list)


# ============================================================
# Prompt
# ============================================================

VISION_SYSTEM_PROMPT = """
당신은 대한민국 대학의 교육과정표 이미지를 구조화하는 데이터 추출기입니다.

이미지에 실제로 존재하는 교과목 행만 추출하세요.

각 교과목에서 다음 정보를 추출합니다.

- year_level: 학년. 1, 2, 3, 4 등의 정수.
- semester: 학기. 예: "1", "2", "하계", "동계".
- completion_type: 이수구분. 예: "전필", "전선", "교필", "교선".
- course_code: 교과목 코드.
- name: 교과목명.
- credits: 학점.
- theory_hours: 이론 시수.
- practice_hours: 실습 시수.

반드시 다음 규칙을 지키세요.

1. 표에 실제로 보이는 정보만 사용합니다.
2. 읽을 수 없는 값을 추측하지 말고 null로 반환합니다.
3. 교과목명이 읽히지 않는 행은 추출하지 않습니다.
4. 표 제목, 헤더, 설명 문구는 교과목으로 추출하지 않습니다.
5. "합계", "소계", "계", "이수학점", "졸업학점" 등의 합계 행은 제외합니다.
6. 학년/학기가 셀 병합으로 첫 행에만 표시된 경우,
   같은 영역의 뒤쪽 교과목에도 해당 학년/학기를 적용합니다.
7. 한 페이지에 여러 학년 또는 여러 학기의 표가 있으면 모두 추출합니다.
8. 교과목 코드가 없거나 읽히지 않는 경우 null로 둡니다.
9. 학점/시수는 가능한 경우 숫자로 반환합니다.
10. 교육과정 교과목 표가 없는 페이지라면 courses를 빈 배열로 반환합니다.

OCR처럼 문자를 읽는 것보다 데이터베이스에 저장할 정확한 교과목 행을
구조화하는 것이 목적입니다.
""".strip()


# ============================================================
# OpenAI Client
# ============================================================

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client

    if _client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY 환경변수가 없습니다. "
                "PyCharm 실행 설정 또는 시스템 환경변수에 API Key를 등록하세요."
            )

        _client = OpenAI()

    return _client


# ============================================================
# 페이지 -> PNG
# ============================================================

def render_page_to_png(
    page: pymupdf.Page,
    zoom: float = 2.0,
) -> bytes:
    """
    PDF 페이지를 파일로 저장하지 않고 메모리에서 PNG bytes로 변환.
    """

    matrix = pymupdf.Matrix(zoom, zoom)

    pixmap = page.get_pixmap(
        matrix=matrix,
        alpha=False,
    )

    return pixmap.tobytes("png")


def _png_to_data_url(png_bytes: bytes) -> str:
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"


# ============================================================
# 값 정리
# ============================================================

def _clean_string(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    value = value.strip()

    if not value:
        return None

    return value


def _clean_number(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None

    if value < 0:
        return None

    return value


def _normalize_course(course: VisionCourse) -> dict | None:
    name = _clean_string(course.name)

    if not name:
        return None

    # 혹시 Vision이 합계행을 교과목으로 판단했을 경우
    normalized_name = name.replace(" ", "")

    blocked_names = {
        "합계",
        "소계",
        "계",
        "이수학점",
        "졸업학점",
        "총학점",
        "학점계",
    }

    if normalized_name in blocked_names:
        return None

    year_level = course.year_level

    if year_level is not None and not (1 <= year_level <= 6):
        year_level = None

    return {
        "year_level": year_level,
        "semester": _clean_string(course.semester),
        "completion_type": _clean_string(course.completion_type),
        "course_code": _clean_string(course.course_code),
        "name": name,
        "credits": _clean_number(course.credits),
        "theory_hours": _clean_number(course.theory_hours),
        "practice_hours": _clean_number(course.practice_hours),
    }


# ============================================================
# Vision 호출
# ============================================================

def extract_courses_with_vision(
    page: pymupdf.Page,
    *,
    department_name: str,
    curriculum_year: int,
    revision_type: Optional[str],
    source_file_name: str,
    source_page: int,
    source_url: str,
    max_retries: int = 3,
) -> list[dict]:
    """
    이미지형 교육과정 PDF 페이지를 Vision으로 분석.

    반환값은 기존 parse_page()와 같은 Course dict 형식.
    """

    client = _get_client()

    png_bytes = render_page_to_png(page)

    image_data_url = _png_to_data_url(png_bytes)

    # 나중에 모델 교체할 때 코드 수정하지 않도록 환경변수화
    model_name = os.getenv(
        "CAREERQUEST_VISION_MODEL",
        "gpt-5.6",
    )

    last_error: Exception | None = None

    for attempt in range(max_retries):

        try:
            response = client.responses.parse(
                model=model_name,
                input=[
                    {
                        "role": "system",
                        "content": VISION_SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    f"다음 이미지는 {department_name}의 "
                                    f"{curriculum_year}학년도 교육과정표 "
                                    f"{source_page}페이지입니다. "
                                    "교과목 행을 구조화해서 추출하세요."
                                ),
                            },
                            {
                                "type": "input_image",
                                "image_url": image_data_url,
                                "detail": "high",
                            },
                        ],
                    },
                ],
                text_format=VisionCurriculumPage,
            )

            parsed = response.output_parsed

            if parsed is None:
                raise RuntimeError(
                    "Vision 응답은 성공했지만 구조화된 결과가 없습니다."
                )

            result: list[dict] = []

            for course in parsed.courses:

                normalized = _normalize_course(course)

                if normalized is None:
                    continue

                # 기존 konyang_curriculum.py에서 쓰던
                # source / department metadata 그대로 결합
                normalized.update(
                    {
                        "department_name": department_name,
                        "curriculum_year": curriculum_year,
                        "revision_type": revision_type,
                        "source_file_name": source_file_name,
                        "source_page": source_page,
                        "source_table": "vision",
                        "source_url": source_url,
                    }
                )

                result.append(normalized)

            return _deduplicate_courses(result)

        except Exception as exc:
            last_error = exc

            if attempt == max_retries - 1:
                break

            # 1초 -> 2초 -> ...
            time.sleep(2 ** attempt)

    raise RuntimeError(
        f"Vision 교육과정 추출 실패: {source_file_name} "
        f"page={source_page}"
    ) from last_error


# ============================================================
# 중복 제거
# ============================================================

def _deduplicate_courses(courses: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple] = set()

    for course in courses:

        key = (
            course.get("course_code") or "",
            course.get("name") or "",
            course.get("year_level"),
            course.get("semester"),
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(course)

    return result