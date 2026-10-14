"""네이버 검색 (NCP API HUB) → 요약 근거.

네이버 블로그·카페는 robots.txt에서 RAG 목적 수집을 금지하므로
본문은 수집하지 않고 API가 준 요약(description)만 근거로 쓴다.
"""
from datetime import datetime

import requests

from engine.config import require
from engine.schemas import EvidenceDoc
from engine.tools.text import clean, domain_of, make_doc_id, yyyymmdd

BASE_URL = "https://naverapihub.apigw.ntruss.com/search/v1"
# 우리 kind → 네이버 API 경로
_ENDPOINT = {"blog": "blog", "cafe": "cafearticle", "web": "webkr"}


def search_naver(
    query: str,
    kind: str = "blog",
    display: int = 100,
    start: int = 1,
    sort: str = "sim",
) -> list[EvidenceDoc]:
    """display: 1~100, start: 1~1000, sort: sim(정확도) | date(최신)."""
    resp = requests.get(
        f"{BASE_URL}/{_ENDPOINT[kind]}",
        params={"query": query, "display": display, "start": start, "sort": sort, "format": "json"},
        headers={
            "X-NCP-APIGW-API-KEY-ID": require("NAVER_CLIENT_ID"),
            "X-NCP-APIGW-API-KEY": require("NAVER_CLIENT_SECRET"),
        },
        timeout=10,
    )
    resp.raise_for_status()
    now = datetime.now().astimezone()

    docs = []
    for i, item in enumerate(resp.json().get("items", []), start=start):
        url = item["link"]
        docs.append(
            EvidenceDoc(
                doc_id=make_doc_id(url),
                provider="naver",
                kind=kind,
                query=query,
                rank=i,
                title=clean(item.get("title", "")),
                snippet=clean(item.get("description", "")),
                url=url,
                domain=domain_of(url),
                source_name=item.get("bloggername") or item.get("cafename"),
                published_at=yyyymmdd(item.get("postdate")),  # 블로그만 있음
                fetch_status="skipped",
                retrieved_at=now,
            )
        )
    return docs
