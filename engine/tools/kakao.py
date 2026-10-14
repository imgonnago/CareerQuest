"""카카오(Daum) 검색 → 본문 수집 후보.

- blog: 결과의 절반 이상이 네이버 블로그라서 티스토리만 남긴다
- web: 도메인이 다양하므로 전부 남기고, 수집 가능 여부는 crawl.py가 판단
"""
from datetime import datetime

import requests

from engine.config import require
from engine.schemas import EvidenceDoc
from engine.tools.crawl import precheck_status
from engine.tools.text import clean, domain_of, make_doc_id, yyyymmdd

BASE_URL = "https://dapi.kakao.com/v2/search"


def search_kakao(
    query: str,
    kind: str = "blog",
    size: int = 50,
    page: int = 1,
    sort: str = "accuracy",
    tistory_only: bool = True,
) -> tuple[list[EvidenceDoc], bool]:
    """size: 1~50, page: 1~50, sort: accuracy | recency.

    반환: (근거 풀 항목들, 마지막 페이지 여부)
    tistory_only는 blog에만 적용된다.
    """
    resp = requests.get(
        f"{BASE_URL}/{kind}",
        params={"query": query, "size": size, "page": page, "sort": sort},
        headers={"Authorization": f"KakaoAK {require('KAKAO_REST_API_KEY')}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    now = datetime.now().astimezone()

    docs = []
    for i, item in enumerate(data.get("documents", []), start=(page - 1) * size + 1):
        url = item["url"]
        domain = domain_of(url)
        if kind == "blog" and tistory_only and not domain.endswith(".tistory.com"):
            continue
        status, note = precheck_status(domain)
        docs.append(
            EvidenceDoc(
                doc_id=make_doc_id(url),
                provider="kakao",
                kind=kind,
                query=query,
                rank=i,
                title=clean(item.get("title", "")),
                snippet=clean(item.get("contents", "")),
                url=url,
                domain=domain,
                source_name=item.get("blogname") or item.get("cafename"),
                published_at=yyyymmdd(item.get("datetime")),
                fetch_status=status,
                fetch_note=note,
                retrieved_at=now,
            )
        )
    return docs, data.get("meta", {}).get("is_end", True)
