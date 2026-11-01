from __future__ import annotations

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


SERVICE_KEY = os.getenv("DATA_GO_KR_API_KEY")

ATTENTION_URL = os.getenv(
    "DATA_GO_QNET_ATTENTION_URL"
)

ITEM_URL = os.getenv(
    "DATA_GO_QNET_ITEM_URL"
)


def xml_to_dict(item: ET.Element) -> dict:
    return {
        child.tag: child.text.strip()
        if child.text
        else None
        for child in item
    }


def request_xml(
    url: str,
    params: dict,
    api_name: str,
    daily_budget: int,
) -> ET.Element:

    if not SERVICE_KEY:
        raise RuntimeError(
            "DATA_GO_KR_API_KEY가 .env에 없습니다."
        )

    quota_manager.check_and_consume(
        api_name,
        daily_budget,
    )

    full_params = {
        "ServiceKey": SERVICE_KEY,
        **params,
    }

    response = requests.get(
        url,
        params=full_params,
        timeout=20,
    )

    response.raise_for_status()

    return ET.fromstring(response.content)


def collect_attention_qualifications(
    page_no: int = 1,
    num_rows: int = 50,
) -> list[dict]:

    budget = int(
        os.getenv(
            "QNET_ATTENTION_DAILY_BUDGET",
            "100",
        )
    )

    root = request_xml(
        ATTENTION_URL,
        {
            "pageNo": page_no,
            "numOfRows": num_rows,
        },
        "QNET_ATTENTION",
        budget,
    )

    result_code = root.findtext(
        ".//resultCode"
    )

    result_msg = root.findtext(
        ".//resultMsg"
    )

    if result_code not in ("00", "000"):
        raise RuntimeError(
            f"Q-Net 관심자격 오류 "
            f"{result_code}: {result_msg}"
        )

    records = []

    for item in root.findall(".//item"):

        raw = xml_to_dict(item)

        records.append(
            {
                "qualification_code":
                    raw.get("attenJmCd"),

                "qualification_name":
                    raw.get("jmNm"),

                "recommended_code_1":
                    raw.get("recomJmCd1"),

                "recommended_name_1":
                    raw.get("recomJmNm1"),

                "recommended_code_2":
                    raw.get("recomJmCd2"),

                "recommended_name_2":
                    raw.get("recomJmNm2"),

                "registered_at":
                    raw.get("regDttm"),

                "modified_at":
                    raw.get("modifyDttm"),

                "raw_metadata": raw,
            }
        )

    return records


def collect_qualification_detail(
    jm_cd: str,
) -> list[dict]:

    budget = int(
        os.getenv(
            "QNET_ITEM_DAILY_BUDGET",
            "100",
        )
    )

    root = request_xml(
        ITEM_URL,
        {
            "jmCd": jm_cd,
        },
        "QNET_ITEM",
        budget,
    )

    result_code = root.findtext(
        ".//resultCode"
    )

    result_msg = root.findtext(
        ".//resultMsg"
    )

    if (
        result_code
        and result_code not in ("00", "000")
    ):
        raise RuntimeError(
            f"Q-Net 종목정보 오류 "
            f"{result_code}: {result_msg}"
        )

    records = []

    for item in root.findall(".//item"):

        raw = xml_to_dict(item)

        records.append(
            {
                "qualification_code":
                    jm_cd,

                "qualification_name":
                    raw.get("jmfldnm"),

                "info_type":
                    raw.get("infogb"),

                "content":
                    raw.get("contents"),

                "major_field_code":
                    raw.get("obligfldcd"),

                "major_field_name":
                    raw.get("obligfldnm"),

                "middle_field_code":
                    raw.get("mdobligfldcd"),

                "middle_field_name":
                    raw.get("mdobligfldnm"),

                "raw_metadata": raw,
            }
        )

    return records


def save_jsonl(
    filename: str,
    records: list[dict],
):

    directory = Path(
        "data/raw/certificates"
    )

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = directory / filename

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

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%d_%H%M%S")

    print(
        "Q-Net 관심자격 목록 수집..."
    )

    try:
        attention = (
            collect_attention_qualifications(
                page_no=1,
                num_rows=50,
            )
        )

        path = save_jsonl(
            f"qnet_attention_{timestamp}.jsonl",
            attention,
        )

        print(
            f"{len(attention)}건 저장: {path}"
        )

    except (
        ApiQuotaExceeded,
        Exception,
    ) as error:

        print(
            f"관심자격 수집 실패: {error}"
        )

    # 테스트용 정보처리기사
    print()
    print(
        "정보처리기사 상세정보 수집..."
    )

    try:
        details = (
            collect_qualification_detail(
                "1320"
            )
        )

        path = save_jsonl(
            f"qnet_1320_{timestamp}.jsonl",
            details,
        )

        print(
            f"{len(details)}건 저장: {path}"
        )

    except (
        ApiQuotaExceeded,
        Exception,
    ) as error:

        print(
            f"종목정보 수집 실패: {error}"
        )


if __name__ == "__main__":
    main()
