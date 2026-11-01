from __future__ import annotations

import hashlib
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)

import requests
from dotenv import load_dotenv

from src.collectors.common.api_quota import (
    ApiQuotaExceeded,
    quota_manager,
)


load_dotenv()


SEARCH_QUERIES = [
    "AI 엔지니어 채용",
    "생성형 AI 개발자 채용",
    "머신러닝 엔지니어 채용",
    "데이터 사이언티스트 채용",
    "데이터 분석가 채용",
    "데이터 엔지니어 채용",
    "백엔드 개발자 채용",
]


BLOCKED_HOSTS = {
    "blog.naver.com",
    "cafe.naver.com",
    "namu.wiki",
    "wikipedia.org",
    "tistory.com",
    "youtube.com",
}


JOB_BOARD_HOSTS = {
    "jobkorea.co.kr",
    "saramin.co.kr",
    "wanted.co.kr",
    "jumpit.saramin.co.kr",
    "jobplanet.co.kr",
    "catch.co.kr",
}


JOB_KEYWORDS = (
    "채용",
    "모집",
    "개발자",
    "엔지니어",
    "신입",
    "경력",
    "지원자격",
    "자격요건",
    "우대사항",
)

TRUSTED_JOB_HOSTS = {
    "wanted.co.kr",
    "saramin.co.kr",
    "jobkorea.co.kr",
    "career.rememberapp.co.kr",
    "work24.go.kr",
    "worldjob.or.kr",
    "work.mma.go.kr",
}

BLOCKED_JOB_HOSTS = {
    "gall.dcinside.com",
    "okky.kr",
    "mk.co.kr",
    "zdnet.co.kr",
    "etnews.com",
    "spri.kr",
    "nsp.nanet.go.kr",
    "banmalnews.com",
    "news.hada.io",
    "inven.co.kr",
}

JOB_TITLE_KEYWORDS = (
    "채용",
    "채용공고",
    "모집",
    "recruit",
    "career",
)

NOISE_KEYWORDS = (
    "교육",
    "과정",
    "부트캠프",
    "뉴스",
    "전망",
    "보고서",
    "양성과정",
    "커뮤니티",
)

RESULTS_PER_QUERY = int(
    os.getenv(
        "JOB_SEARCH_RESULTS_PER_QUERY",
        "10",
    )
)

MAX_CANDIDATES = int(
    os.getenv(
        "JOB_SEARCH_MAX_CANDIDATES",
        "50",
    )
)


def clean_text(value: str | None) -> str:
    if not value:
        return ""

    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def get_host(url: str) -> str:
    return (
        urlsplit(url)
        .netloc
        .lower()
        .removeprefix("www.")
    )


def normalize_url(url: str) -> str:
    """
    광고/추적 파라미터를 제거해서
    동일 공고 중복을 줄인다.
    """

    parts = urlsplit(url)

    filtered_query = []

    for key, value in parse_qsl(
        parts.query,
        keep_blank_values=True,
    ):

        lower_key = key.lower()

        if lower_key.startswith("utm_"):
            continue

        if lower_key in {
            "ref",
            "refer",
            "tracking",
            "trackingcode",
            "source",
        }:
            continue

        filtered_query.append(
            (key, value)
        )

    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            urlencode(filtered_query),
            "",
        )
    )


def is_blocked(url: str) -> bool:
    host = get_host(url)

    return any(
        host == blocked
        or host.endswith("." + blocked)
        for blocked in BLOCKED_HOSTS
    )


def looks_like_job(
    title: str,
    snippet: str,
) -> bool:

    text = (
        f"{title} {snippet}"
    ).lower()

    return any(
        keyword.lower() in text
        for keyword in JOB_KEYWORDS
    )


def classify_source(
    url: str,
) -> str:

    host = get_host(url)

    for board in JOB_BOARD_HOSTS:
        if (
            host == board
            or host.endswith("." + board)
        ):
            return "JOB_BOARD"

    return "WEB"


def make_candidate_id(
    normalized_url: str,
) -> str:

    return hashlib.sha256(
        normalized_url.encode("utf-8")
    ).hexdigest()[:20]

def calculate_candidate_score(record: dict) -> int:
    score = 0

    host = record["source_host"]
    title = record["title"].lower()

    if host in TRUSTED_JOB_HOSTS:
        score += 100

    if any(
        keyword.lower() in title
        for keyword in JOB_TITLE_KEYWORDS
    ):
        score += 30

    if any(
        keyword.lower() in title
        for keyword in NOISE_KEYWORDS
    ):
        score -= 80

    if host in BLOCKED_JOB_HOSTS:
        score -= 200

    return score

# =========================================================
# NAVER API HUB
# =========================================================

def search_naver(
    query: str,
) -> list[dict]:

    client_id = os.getenv(
        "NAVER_CLIENT_ID"
    )

    client_secret = os.getenv(
        "NAVER_CLIENT_SECRET"
    )

    if not client_id or not client_secret:
        print(
            "  NAVER: API 키 없음 - 건너뜀"
        )
        return []

    budget = int(
        os.getenv(
            "NAVER_SEARCH_DAILY_BUDGET",
            "100",
        )
    )

    quota_manager.check_and_consume(
        "NAVER_SEARCH",
        budget,
    )

    response = requests.get(
        (
            "https://naverapihub.apigw.ntruss.com"
            "/search/v1/webkr"
        ),
        headers={
            "X-NCP-APIGW-API-KEY-ID":
                client_id,

            "X-NCP-APIGW-API-KEY":
                client_secret,
        },
        params={
            "query": query,
            "display": min(
                RESULTS_PER_QUERY,
                100,
            ),
            "start": 1,
            "format": "json",
        },
        timeout=15,
    )

    response.raise_for_status()

    return response.json().get(
        "items",
        [],
    )


# =========================================================
# Kakao
# =========================================================

def search_kakao(
    query: str,
) -> list[dict]:

    api_key = os.getenv(
        "KAKAO_REST_API_KEY"
    )

    if not api_key:
        print(
            "  Kakao: API 키 없음 - 건너뜀"
        )
        return []

    budget = int(
        os.getenv(
            "KAKAO_SEARCH_DAILY_BUDGET",
            "100",
        )
    )

    quota_manager.check_and_consume(
        "KAKAO_SEARCH",
        budget,
    )

    response = requests.get(
        "https://dapi.kakao.com/v2/search/web",
        headers={
            "Authorization":
                f"KakaoAK {api_key}",
        },
        params={
            "query": query,
            "sort": "recency",
            "page": 1,
            "size": min(
                RESULTS_PER_QUERY,
                50,
            ),
        },
        timeout=15,
    )

    response.raise_for_status()

    return response.json().get(
        "documents",
        [],
    )


# =========================================================
# Normalize
# =========================================================

def normalize_naver(
    item: dict,
    query: str,
) -> dict | None:

    url = item.get("link")

    if not url:
        return None

    title = clean_text(
        item.get("title")
    )

    snippet = clean_text(
        item.get("description")
    )

    if is_blocked(url):
        return None

    if not looks_like_job(
        title,
        snippet,
    ):
        return None

    normalized_url = normalize_url(url)

    return {
        "candidate_id":
            make_candidate_id(
                normalized_url
            ),

        "resource_type": "JOB",
        "collection_stage": "DISCOVERY",

        "search_engine": "NAVER",
        "search_query": query,

        "title": title,
        "snippet": snippet,

        "source_url": url,
        "normalized_url": normalized_url,
        "source_host": get_host(url),
        "source_class":
            classify_source(url),

        "published_at": None,

        "fetched_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "raw_metadata": item,
    }


def normalize_kakao(
    item: dict,
    query: str,
) -> dict | None:

    url = item.get("url")

    if not url:
        return None

    title = clean_text(
        item.get("title")
    )

    snippet = clean_text(
        item.get("contents")
    )

    if is_blocked(url):
        return None

    if not looks_like_job(
        title,
        snippet,
    ):
        return None

    normalized_url = normalize_url(url)

    return {
        "candidate_id":
            make_candidate_id(
                normalized_url
            ),

        "resource_type": "JOB",
        "collection_stage": "DISCOVERY",

        "search_engine": "KAKAO",
        "search_query": query,

        "title": title,
        "snippet": snippet,

        "source_url": url,
        "normalized_url": normalized_url,
        "source_host": get_host(url),
        "source_class":
            classify_source(url),

        "published_at":
            item.get("datetime"),

        "fetched_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "raw_metadata": item,
    }


# =========================================================
# Collect
# =========================================================

def collect_candidates() -> list[dict]:

    records: dict[str, dict] = {}

    for query in SEARCH_QUERIES:

        print()
        print(
            f"[검색어] {query}"
        )

        # NAVER
        try:
            results = search_naver(
                query
            )

            print(
                f"  NAVER: "
                f"{len(results)}건"
            )

            for item in results:

                record = normalize_naver(
                    item,
                    query,
                )

                if not record:
                    continue

                records.setdefault(
                    record["normalized_url"],
                    record,
                )

        except (
            requests.RequestException,
            ApiQuotaExceeded,
        ) as error:

            print(
                f"  NAVER 오류: {error}"
            )

        # Kakao
        try:
            results = search_kakao(
                query
            )

            print(
                f"  Kakao: "
                f"{len(results)}건"
            )

            for item in results:

                record = normalize_kakao(
                    item,
                    query,
                )

                if not record:
                    continue

                records.setdefault(
                    record["normalized_url"],
                    record,
                )

        except (
            requests.RequestException,
            ApiQuotaExceeded,
        ) as error:

            print(
                f"  Kakao 오류: {error}"
            )

        if len(records) >= MAX_CANDIDATES:
            break

    results = list(records.values())

    for record in results:
        record["candidate_score"] = (
            calculate_candidate_score(record)
        )

    results.sort(
        key=lambda x: x["candidate_score"],
        reverse=True,
    )

    return results[:MAX_CANDIDATES]


def save_jsonl(
    records: list[dict],
) -> Path:

    output_dir = Path(
        "data/raw/jobs"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    path = (
        output_dir
        / f"job_candidates_{timestamp}.jsonl"
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

    print(
        "채용공고 후보 검색 시작..."
    )

    records = collect_candidates()

    path = save_jsonl(
        records
    )

    print()
    print("=" * 60)

    print(
        f"최종 후보 수: "
        f"{len(records)}건"
    )

    print(
        f"저장 위치: {path}"
    )

    print("=" * 60)


if __name__ == "__main__":
    main()