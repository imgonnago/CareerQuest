from __future__ import annotations

import json
import math
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


SERVICE_KEY = os.getenv("DATA_GO_KR_API_KEY")

MAX_PAGES = int(
    os.getenv(
        "COLLECTOR_MAX_PAGES",
        "5",
    )
)

ROWS_PER_PAGE = int(
    os.getenv(
        "COLLECTOR_ROWS_PER_PAGE",
        "100",
    )
)


def xml_item_to_dict(
    item: ET.Element,
) -> dict:

    return {
        child.tag: (
            child.text.strip()
            if child.text
            else None
        )
        for child in item
    }


def save_jsonl(
    source_name: str,
    records: list[dict],
) -> Path:

    output_dir = Path(
        "data/raw/public_data"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    output_path = (
        output_dir
        / f"{source_name}_{timestamp}.jsonl"
    )

    with output_path.open(
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

    return output_path


def request_xml_page(
    *,
    api_name: str,
    url: str,
    daily_budget: int,
    page_no: int,
    rows_per_page: int,
    extra_params: dict | None = None,
) -> tuple[list[dict], int]:

    if not SERVICE_KEY:
        raise RuntimeError(
            "DATA_GO_KR_API_KEY가 설정되지 않았습니다."
        )

    if not url:
        raise RuntimeError(
            f"{api_name} URL이 설정되지 않았습니다."
        )

    quota_manager.check_and_consume(
        api_name,
        daily_budget,
    )

    params = {
        "ServiceKey": SERVICE_KEY,
        "pageNo": page_no,
        "numOfRows": rows_per_page,
    }

    if extra_params:
        params.update(extra_params)

    response = requests.get(
        url,
        params=params,
        timeout=20,
    )

    response.raise_for_status()

    root = ET.fromstring(
        response.content
    )

    result_code = root.findtext(
        ".//resultCode"
    )

    result_message = root.findtext(
        ".//resultMsg"
    )

    if result_code not in (
        None,
        "00",
        "0",
    ):
        raise RuntimeError(
            f"{api_name} 오류: "
            f"{result_code} "
            f"{result_message}"
        )

    items = [
        xml_item_to_dict(item)
        for item in root.findall(
            ".//item"
        )
    ]

    total_count_text = (
        root.findtext(".//totalCount")
        or "0"
    )

    try:
        total_count = int(
            total_count_text
        )
    except ValueError:
        total_count = len(items)

    return items, total_count


def collect_public_api(
    *,
    source_name: str,
    api_name: str,
    url: str,
    daily_budget: int,
    extra_params: dict | None = None,
) -> list[dict]:

    all_records: list[dict] = []

    page = 1

    while page <= MAX_PAGES:

        remaining = quota_manager.remaining(
            api_name,
            daily_budget,
        )

        print(
            f"[{source_name}] "
            f"page={page}, "
            f"남은 자체 호출량={remaining}"
        )

        try:
            items, total_count = (
                request_xml_page(
                    api_name=api_name,
                    url=url,
                    daily_budget=daily_budget,
                    page_no=page,
                    rows_per_page=ROWS_PER_PAGE,
                    extra_params=extra_params,
                )
            )

        except ApiQuotaExceeded as error:
            print(error)
            break

        if not items:
            break

        fetched_at = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        for item in items:

            all_records.append(
                {
                    "source_name":
                        source_name,

                    "source_type":
                        "PUBLIC_DATA",

                    "fetched_at":
                        fetched_at,

                    "raw_metadata":
                        item,
                }
            )

        print(
            f"  {len(items)}건 수집 "
            f"(누적 {len(all_records)}건 / "
            f"전체 {total_count}건)"
        )

        expected_pages = max(
            math.ceil(
                total_count
                / ROWS_PER_PAGE
            ),
            1,
        )

        if page >= expected_pages:
            break

        page += 1

    return all_records


def collect_qnet_attention():

    url = os.getenv(
        "DATA_GO_QNET_ATTENTION_URL",
        "",
    )

    budget = int(
        os.getenv(
            "QNET_ATTENTION_DAILY_BUDGET",
            "500",
        )
    )

    return collect_public_api(
        source_name="qnet_attention",
        api_name="QNET_ATTENTION",
        url=url,
        daily_budget=budget,
    )


def collect_qnet_items():

    url = os.getenv(
        "DATA_GO_QNET_ITEM_URL",
        "",
    )

    budget = int(
        os.getenv(
            "QNET_ITEM_DAILY_BUDGET",
            "500",
        )
    )

    return collect_public_api(
        source_name="qnet_item",
        api_name="QNET_ITEM",
        url=url,
        daily_budget=budget,
    )


def collect_university_departments():

    url = os.getenv(
        "DATA_GO_UNIV_DEPT_URL",
        "",
    )

    budget = int(
        os.getenv(
            "UNIV_DEPT_DAILY_BUDGET",
            "500",
        )
    )

    return collect_public_api(
        source_name="university_department",
        api_name="UNIV_DEPT",
        url=url,
        daily_budget=budget,
    )


def main():

    collectors = [
        (
            "Q-Net 관심자격",
            collect_qnet_attention,
        ),
        (
            "Q-Net 종목별 자격정보",
            collect_qnet_items,
        ),
        (
            "대학 학과정보",
            collect_university_departments,
        ),
    ]

    for display_name, collector in collectors:

        print()
        print("=" * 60)
        print(display_name)
        print("=" * 60)

        try:
            records = collector()

        except Exception as error:
            print(
                f"수집 실패: {error}"
            )
            continue

        if not records:
            print("수집 결과 없음")
            continue

        source_name = (
            records[0]["source_name"]
        )

        path = save_jsonl(
            source_name,
            records,
        )

        print(
            f"저장 완료: {path}"
        )


if __name__ == "__main__":
    main()