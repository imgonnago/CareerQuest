from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
import trafilatura
from bs4 import BeautifulSoup
from dotenv import load_dotenv


load_dotenv()


MAX_PAGES = int(
    os.getenv(
        "JOB_DETAIL_MAX_PAGES",
        "20",
    )
)

TIMEOUT = int(
    os.getenv(
        "JOB_DETAIL_TIMEOUT_SECONDS",
        "15",
    )
)

MAX_CHARS = int(
    os.getenv(
        "JOB_DETAIL_MAX_CHARS",
        "80000",
    )
)


USER_AGENT = (
    "CareerQuestAI-AcademicProject/0.1"
)


def find_latest_candidate_file() -> Path:

    directory = Path(
        "data/raw/jobs"
    )

    files = sorted(
        directory.glob(
            "job_candidates_*.jsonl"
        ),
        reverse=True,
    )

    if not files:
        raise FileNotFoundError(
            "job_candidates JSONL 파일이 없습니다."
        )

    return files[0]


def read_jsonl(
    path: Path,
) -> list[dict]:

    records = []

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:

        for line in file:

            line = line.strip()

            if not line:
                continue

            records.append(
                json.loads(line)
            )

    return records


def extract_text(
    html_text: str,
) -> str:

    # 1순위: trafilatura
    text = trafilatura.extract(
        html_text,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )

    if text:
        return text.strip()

    # 2순위 fallback
    soup = BeautifulSoup(
        html_text,
        "html.parser",
    )

    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
        ]
    ):
        tag.decompose()

    return soup.get_text(
        "\n",
        strip=True,
    )


def content_hash(
    text: str,
) -> str:

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def fetch_detail(
    candidate: dict,
) -> dict:

    url = candidate[
        "source_url"
    ]

    result = {
        **candidate,

        "collection_stage":
            "DETAIL",

        "detail_fetched_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "http_status": None,
        "content_type": None,

        "page_title": None,

        "raw_text": None,
        "text_length": 0,

        "content_hash": None,

        "fetch_status": "UNKNOWN",
        "fetch_error": None,
    }

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent":
                    USER_AGENT,

                "Accept":
                    (
                        "text/html,"
                        "application/xhtml+xml"
                    ),
            },
            timeout=TIMEOUT,
            allow_redirects=True,
        )

        result["http_status"] = (
            response.status_code
        )

        result["final_url"] = (
            response.url
        )

        result["content_type"] = (
            response.headers.get(
                "Content-Type",
                ""
            )
        )

        response.raise_for_status()

        content_type = (
            result["content_type"]
            .lower()
        )

        if (
            "text/html"
            not in content_type

            and
            "application/xhtml+xml"
            not in content_type
        ):
            result["fetch_status"] = (
                "UNSUPPORTED_CONTENT_TYPE"
            )

            return result

        response.encoding = (
            response.apparent_encoding
            or response.encoding
        )

        html_text = response.text

        soup = BeautifulSoup(
            html_text,
            "html.parser",
        )

        if soup.title:
            result["page_title"] = (
                soup.title.get_text(
                    " ",
                    strip=True,
                )
            )

        text = extract_text(
            html_text
        )

        if not text:
            result["fetch_status"] = (
                "NO_TEXT"
            )

            return result

        text = text[:MAX_CHARS]

        result["raw_text"] = text

        result["text_length"] = (
            len(text)
        )

        result["content_hash"] = (
            content_hash(text)
        )

        result["fetch_status"] = (
            "SUCCESS"
        )

        return result

    except requests.RequestException as error:

        result["fetch_status"] = (
            "HTTP_ERROR"
        )

        result["fetch_error"] = (
            str(error)
        )

        return result

    except Exception as error:

        result["fetch_status"] = (
            "PARSE_ERROR"
        )

        result["fetch_error"] = (
            str(error)
        )

        return result


def save_jsonl(
    records: list[dict],
) -> Path:

    directory = Path(
        "data/raw/jobs"
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    path = (
        directory
        / f"job_details_{timestamp}.jsonl"
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

    candidate_file = (
        find_latest_candidate_file()
    )

    print(
        f"후보 파일: {candidate_file}"
    )

    candidates = read_jsonl(
        candidate_file
    )

    candidates = (
        candidates[:MAX_PAGES]
    )

    print(
        f"상세페이지 최대 "
        f"{len(candidates)}건 확인"
    )

    results = []

    success_count = 0

    for index, candidate in enumerate(
        candidates,
        start=1,
    ):

        print()
        print(
            f"[{index}/{len(candidates)}]"
        )

        print(
            candidate.get(
                "title",
                "(제목 없음)",
            )
        )

        print(
            candidate.get(
                "source_url"
            )
        )

        result = fetch_detail(
            candidate
        )

        results.append(result)

        print(
            " → "
            f"{result['fetch_status']}"
            f" / "
            f"{result['text_length']}자"
        )

        if (
            result["fetch_status"]
            == "SUCCESS"
        ):
            success_count += 1

        # 사이트에 과도한 연속 요청 방지
        time.sleep(1.0)

    path = save_jsonl(
        results
    )

    print()
    print("=" * 60)

    print(
        f"성공: "
        f"{success_count}"
        f"/{len(results)}건"
    )

    print(
        f"저장 위치: {path}"
    )

    print("=" * 60)


if __name__ == "__main__":
    main()