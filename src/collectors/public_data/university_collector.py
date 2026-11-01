from __future__ import annotations
from urllib.parse import unquote
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from src.collectors.common.api_quota import (
    ApiQuotaExceeded,
    quota_manager,
)

load_dotenv()


RAW_SERVICE_KEY = os.getenv("DATA_GO_KR_API_KEY")

SERVICE_KEY = (
    unquote(RAW_SERVICE_KEY.strip())
    if RAW_SERVICE_KEY
    else None
)
UNIV_URL = os.getenv("DATA_GO_UNIV_DEPT_URL")

DAILY_BUDGET = int(
    os.getenv(
        "UNIV_DEPT_DAILY_BUDGET",
        "100",
    )
)


def element_to_dict(element: ET.Element) -> dict:
    """
    XML의 item 한 건을 dict로 변환.
    첫 단계에서는 필드명을 임의로 해석하지 않고
    원본 그대로 저장한다.
    """
    result = {}

    for child in element:
        if list(child):
            result[child.tag] = element_to_dict(child)
        else:
            result[child.tag] = (
                child.text.strip()
                if child.text
                else None
            )

    return result


def collect_university_departments(
    school_name: str = "건양대학교",
    survey_year: str = "2026",
    page_no: int = 1,
    num_rows: int = 100,
) -> tuple[list[dict], str]:

    if not SERVICE_KEY:
        raise RuntimeError(
            "DATA_GO_KR_API_KEY가 .env에 없습니다."
        )

    if not UNIV_URL:
        raise RuntimeError(
            "DATA_GO_UNIV_DEPT_URL이 .env에 없습니다."
        )

    quota_manager.check_and_consume(
        "UNIV_DEPT",
        DAILY_BUDGET,
    )

    params = {
        "serviceKey": SERVICE_KEY,
        "pageNo": page_no,
        "numOfRows": num_rows,
        "svyYr": survey_year,
        "schlKrnNm": school_name,
    }

    response = requests.get(
        UNIV_URL,
        params=params,
        timeout=20,
    )

    print()
    print("[REQUEST URL]")
    print(response.url)

    print()
    print("[STATUS]")
    print(response.status_code)

    print()
    print("[RAW RESPONSE 일부]")
    print(response.text[:3000])

    response.raise_for_status()

    root = ET.fromstring(
        response.content
    )

    items = []

    for item in root.findall(".//item"):
        items.append(
            element_to_dict(item)
        )

    return items, response.text


def save_jsonl(
    records: list[dict],
) -> Path:

    output_dir = Path(
        "data/raw/university"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d_%H%M%S"
    )

    path = (
        output_dir
        / f"university_departments_{timestamp}.jsonl"
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:

        for record in records:
            file.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                )
                + "\n"
            )

    return path


def main():

    print("대학별 학과정보 수집 시작...")

    survey_years = [
        "2026",
        "2025",
        "2024",
    ]

    records = []
    found_year = None

    for year in survey_years:

        print()
        print(f"[조사년도 테스트] {year}")

        try:
            records, _ = collect_university_departments(
                school_name="건양대학교",
                survey_year=year,
                page_no=1,
                num_rows=100,
            )

        except ApiQuotaExceeded as error:
            print(f"호출 한도 초과: {error}")
            return

        except Exception as error:
            print(f"수집 실패: {error}")
            continue

        print(f"{year}년 결과: {len(records)}건")

        if records:
            found_year = year
            break

    if not records:
        print()
        print(
            "2024~2026 조사년도에서 "
            "건양대학교 데이터를 찾지 못했습니다."
        )
        return

    print()
    print(
        f"사용 가능한 최신 조사년도: {found_year}"
    )

    print()
    print("[첫 번째 데이터 필드]")

    print(
        json.dumps(
            records[0],
            ensure_ascii=False,
            indent=2,
        )
    )

    path = save_jsonl(records)

    print()
    print(f"저장 완료: {path}")

    ai_departments = [
        item
        for item in records
        if "인공지능" in (
                item.get("korMjrNm") or ""
        )
    ]

    print()
    print("[인공지능학과 검색 결과]")
    print(
        json.dumps(
            ai_departments,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()