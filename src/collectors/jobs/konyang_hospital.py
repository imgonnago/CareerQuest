from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
import trafilatura
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from src.db.database import engine
from src.loaders.direct_job_loader import upsert_official_job_record


SOURCE_NAME = "KONYANG_HOSPITAL_RECRUIT"
ORGANIZATION = "건양대학교병원"

BASE_URL = "https://www.kyuh.ac.kr"
LIST_URL = f"{BASE_URL}/prog/recruitNotice/list.do"

TIMEOUT = 20
REQUEST_DELAY = 1.0
MAX_PAGES = 20

SEOUL = ZoneInfo("Asia/Seoul")

JOB_CATEGORIES = (
    "의사직",
    "간호직",
    "의료기사직",
    "의료지원직",
)

POSTING_STATUSES = (
    "공고중",
    "접수중",
    "접수마감",
)

APPLICATION_METHODS = (
    "인터넷접수",
    "방문 및 우편접수",
    "이메일접수",
)


DATE_RANGE_RE = re.compile(
    r"접수시작/종료일\s*"
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})"
    r"\s*~\s*"
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})"
)


ENTRY_RE = re.compile(
    r"(?P<method>인터넷접수|방문 및 우편접수|이메일접수)\s+"
    r"(?P<status>공고중|접수중|접수마감)\s+"
    r"(?P<category>의사직|간호직|의료기사직|의료지원직)\s+"
    r"(?P<title>.+?)\s+"
    r"접수시작/종료일\s+"
    r"(?P<start>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*~\s*"
    r"(?P<end>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})",
    re.IGNORECASE,
)


TOTAL_PAGE_RE = re.compile(
    r"페이지\s*\d+\s*/\s*(\d+)"
)


NOTICE_ID_RE = re.compile(
    r"(?:noticeId=|noticeId['\"\s,:=(]+)([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


NOTICE_ID_FALLBACK_RE = re.compile(
    r"\bRT[0-9A-Za-z_-]{10,}\b"
)


@dataclass
class RecruitItem:
    notice_id: str
    title: str
    detail_url: str
    category: str | None
    posting_status: str | None
    application_method: str | None
    application_start: str | None
    deadline: str | None
    list_text: str


def compact(
    text: str | None,
) -> str:
    return re.sub(
        r"\s+",
        " ",
        text or "",
    ).strip()


def to_kst_iso(
    value: str | None,
) -> str | None:

    if not value:
        return None

    try:
        parsed = datetime.strptime(
            value.strip(),
            "%Y-%m-%d %H:%M",
        )

        return parsed.replace(
            tzinfo=SEOUL
        ).isoformat()

    except ValueError:
        return None


def build_session() -> requests.Session:

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "CareerQuestBot/1.0 "
                "(university project)"
            ),
            "Accept-Language": (
                "ko-KR,ko;q=0.9,en;q=0.5"
            ),
        }
    )

    return session


def fetch(
    session: requests.Session,
    url: str,
    *,
    params: dict | None = None,
):

    response = session.get(
        url,
        params=params,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    if not response.encoding:
        response.encoding = (
            response.apparent_encoding
        )

    time.sleep(
        REQUEST_DELAY
    )

    return response


def extract_notice_id(
    *values: str | None,
) -> str | None:

    joined = " ".join(
        value
        for value in values
        if value
    )

    match = NOTICE_ID_RE.search(
        joined
    )

    if match:
        return match.group(1)

    fallback = (
        NOTICE_ID_FALLBACK_RE.search(
            joined
        )
    )

    return (
        fallback.group(0)
        if fallback
        else None
    )


def build_detail_url(
    page_url: str,
    notice_id: str,
) -> str:

    parsed = urlparse(
        page_url
    )

    query = parse_qs(
        parsed.query
    )

    query["noticeId"] = [
        notice_id
    ]

    return urlunparse(
        parsed._replace(
            path=(
                "/prog/recruitNotice/"
                "view.do"
            ),
            query=urlencode(
                query,
                doseq=True,
            ),
        )
    )


def extract_visible_entries(
    soup: BeautifulSoup,
) -> list[dict]:
    """
    실제 화면에 표시되는 채용공고 블록을
    텍스트 순서 기준으로 파싱한다.

    예:
    인터넷접수
    공고중
    의료기사직
    건강증진팀 방사선사 모집
    접수시작/종료일
    2026-10-20 09:00 ~ 2026-11-05 12:00
    """

    page_text = compact(
        soup.get_text(
            " ",
            strip=True,
        )
    )

    entries = []

    for match in ENTRY_RE.finditer(
        page_text
    ):

        title = compact(
            match.group(
                "title"
            )
        )

        if not title:
            continue

        entries.append(
            {
                "title": (
                    title
                ),

                "category": (
                    match.group(
                        "category"
                    )
                ),

                "posting_status": (
                    match.group(
                        "status"
                    )
                ),

                "application_method": (
                    match.group(
                        "method"
                    )
                ),

                "application_start": (
                    to_kst_iso(
                        match.group(
                            "start"
                        )
                    )
                ),

                "deadline": (
                    to_kst_iso(
                        match.group(
                            "end"
                        )
                    )
                ),
            }
        )

    return entries


def extract_notice_links(
    soup: BeautifulSoup,
    page_url: str,
) -> list[tuple[str, str]]:
    """
    HTML에서 noticeId가 들어 있는
    상세 링크를 화면 순서대로 추출한다.
    """

    result = []

    seen = set()

    for tag in soup.find_all(
        True
    ):

        href = (
            tag.get(
                "href",
                "",
            )
            if tag.has_attr(
                "href"
            )
            else ""
        )

        onclick = (
            tag.get(
                "onclick",
                "",
            )
            if tag.has_attr(
                "onclick"
            )
            else ""
        )

        data_values = " ".join(
            str(value)
            for key, value
            in tag.attrs.items()
            if key.startswith(
                "data-"
            )
        )

        notice_id = (
            extract_notice_id(
                href,
                onclick,
                data_values,
            )
        )

        if (
            not notice_id
            or notice_id
            in seen
        ):
            continue

        if (
            href
            and not href.lower()
            .startswith(
                "javascript:"
            )
        ):

            detail_url = (
                urljoin(
                    page_url,
                    href,
                )
            )

        else:

            detail_url = (
                build_detail_url(
                    page_url,
                    notice_id,
                )
            )

        if (
            "recruitNotice/view.do"
            not in detail_url
        ):

            detail_url = (
                build_detail_url(
                    page_url,
                    notice_id,
                )
            )

        seen.add(
            notice_id
        )

        result.append(
            (
                notice_id,
                detail_url,
            )
        )

    return result


def parse_list_page(
    html: str,
    page_url: str,
) -> tuple[
    list[RecruitItem],
    int | None,
]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    page_text = compact(
        soup.get_text(
            " ",
            strip=True,
        )
    )

    page_match = (
        TOTAL_PAGE_RE.search(
            page_text
        )
    )

    total_pages = (
        int(
            page_match.group(1)
        )
        if page_match
        else None
    )

    entries = (
        extract_visible_entries(
            soup
        )
    )

    notice_links = (
        extract_notice_links(
            soup,
            page_url,
        )
    )

    print(
        "  [진단]",
        f"화면공고={len(entries)}",
        f"noticeId={len(notice_links)}",
    )

    # --------------------------------------------------------
    # 정상 케이스
    # 화면에 보이는 공고 개수와 noticeId 개수가 같은 경우
    # --------------------------------------------------------

    if (
        entries
        and len(entries)
        == len(notice_links)
    ):

        items = []

        for (
            entry,
            (
                notice_id,
                detail_url,
            ),
        ) in zip(
            entries,
            notice_links,
        ):

            items.append(
                RecruitItem(
                    notice_id=(
                        notice_id
                    ),

                    title=(
                        entry[
                            "title"
                        ]
                    ),

                    detail_url=(
                        detail_url
                    ),

                    category=(
                        entry[
                            "category"
                        ]
                    ),

                    posting_status=(
                        entry[
                            "posting_status"
                        ]
                    ),

                    application_method=(
                        entry[
                            "application_method"
                        ]
                    ),

                    application_start=(
                        entry[
                            "application_start"
                        ]
                    ),

                    deadline=(
                        entry[
                            "deadline"
                        ]
                    ),

                    list_text=(
                        f"{entry['application_method']} "
                        f"{entry['posting_status']} "
                        f"{entry['category']} "
                        f"{entry['title']} "
                        f"접수시작/종료일 "
                        f"{entry['application_start']} "
                        f"~ "
                        f"{entry['deadline']}"
                    ),
                )
            )

        return (
            items,
            total_pages,
        )

    # --------------------------------------------------------
    # 구조가 예상과 다른 경우
    # --------------------------------------------------------

    print(
        "[WARN] 화면 공고 수와 "
        "noticeId 수 불일치:",
        f"entries={len(entries)}",
        f"notice_ids="
        f"{len(notice_links)}",
    )

    # 디버깅용으로 실제 잡힌 제목 출력
    if entries:

        print(
            "[DEBUG] 화면에서 잡힌 제목:"
        )

        for index, entry in enumerate(
            entries,
            start=1,
        ):

            print(
                f"    {index}. "
                f"{entry['title']}"
            )

    items = []

    for index, (
        notice_id,
        detail_url,
    ) in enumerate(
        notice_links
    ):

        entry = (
            entries[index]
            if index
            < len(entries)
            else None
        )

        items.append(
            RecruitItem(
                notice_id=(
                    notice_id
                ),

                title=(
                    entry[
                        "title"
                    ]
                    if entry
                    else (
                        "건양대학교병원 "
                        "채용공고"
                    )
                ),

                detail_url=(
                    detail_url
                ),

                category=(
                    entry[
                        "category"
                    ]
                    if entry
                    else None
                ),

                posting_status=(
                    entry[
                        "posting_status"
                    ]
                    if entry
                    else None
                ),

                application_method=(
                    entry[
                        "application_method"
                    ]
                    if entry
                    else None
                ),

                application_start=(
                    entry[
                        "application_start"
                    ]
                    if entry
                    else None
                ),

                deadline=(
                    entry[
                        "deadline"
                    ]
                    if entry
                    else None
                ),

                list_text=(
                    page_text
                ),
            )
        )

    return (
        items,
        total_pages,
    )


def crawl_list(
    session: requests.Session,
) -> list[RecruitItem]:

    all_items: dict[
        str,
        RecruitItem,
    ] = {}

    total_pages: (
        int | None
    ) = None

    for page_index in range(
        1,
        MAX_PAGES + 1,
    ):

        response = fetch(
            session,
            LIST_URL,
            params={
                "pageIndex": (
                    page_index
                ),
            },
        )

        items, (
            detected_total_pages
        ) = parse_list_page(
            response.text,
            response.url,
        )

        if total_pages is None:

            total_pages = (
                detected_total_pages
            )

        print(
            f"[목록] "
            f"page={page_index} "
            f"→ {len(items)}건"
        )

        new_count = 0

        for item in items:

            if (
                item.notice_id
                not in all_items
            ):
                new_count += 1

            all_items[
                item.notice_id
            ] = item

        if (
            total_pages
            is not None
            and page_index
            >= total_pages
        ):
            break

        if (
            total_pages
            is None
            and page_index > 1
            and new_count == 0
        ):
            break

    return list(
        all_items.values()
    )


def extract_fulltext(
    html: str,
) -> str:

    text = trafilatura.extract(
        html,

        include_comments=False,

        include_tables=True,

        include_links=False,

        favor_precision=True,
    )

    if text:
        return text.strip()

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    for tag in soup(
        [
            "script",
            "style",
            "noscript",
        ]
    ):
        tag.decompose()

    return "\n".join(
        compact(value)

        for value
        in soup.stripped_strings

        if compact(
            value
        )
    )


def extract_page_title(
    html: str,
) -> str | None:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    if soup.title:

        return (
            compact(
                soup.title.get_text(
                    " ",
                    strip=True,
                )
            )
            or None
        )

    return None


def fetch_detail_record(
    session: requests.Session,
    item: RecruitItem,
) -> dict:

    response = fetch(
        session,
        item.detail_url,
    )

    raw_text = (
        extract_fulltext(
            response.text
        )
    )

    metadata_lines = [
        (
            f"채용구분: "
            f"{item.category}"
            if item.category
            else None
        ),

        (
            f"공고상태: "
            f"{item.posting_status}"
            if item.posting_status
            else None
        ),

        (
            f"접수방법: "
            f"{item.application_method}"
            if item.application_method
            else None
        ),

        (
            f"접수시작: "
            f"{item.application_start}"
            if item.application_start
            else None
        ),

        (
            f"접수마감: "
            f"{item.deadline}"
            if item.deadline
            else None
        ),
    ]

    metadata_text = "\n".join(
        value
        for value
        in metadata_lines
        if value
    )

    return {
        "source_record_key": (
            f"KYUH_RECRUIT:"
            f"{item.notice_id}"
        ),

        "source_url": (
            item.detail_url
        ),

        "final_url": (
            response.url
        ),

        "title": (
            item.title
        ),

        "page_title": (
            extract_page_title(
                response.text
            )
        ),

        "snippet": (
            item.list_text[:1000]
            if item.list_text
            else None
        ),

        "organization": (
            ORGANIZATION
        ),

        "application_start": (
            item.application_start
        ),

        "deadline": (
            item.deadline
        ),

        "location": (
            "대전광역시 서구 "
            "관저동로 158"
        ),

        "raw_text": (
            f"{metadata_text}\n\n"
            f"{raw_text}".strip()
            if metadata_text
            else raw_text
        ),

        "http_status": (
            response.status_code
        ),

        "raw_metadata": {
            "notice_id": (
                item.notice_id
            ),

            "job_category": (
                item.category
            ),

            "posting_status": (
                item.posting_status
            ),

            "application_method": (
                item.application_method
            ),

            "list_text": (
                item.list_text
            ),
        },
    }


def main() -> None:

    print(
        "=" * 70
    )

    print(
        "CareerQuest - "
        "건양대학교병원 채용 수집"
    )

    print(
        "=" * 70
    )

    http = (
        build_session()
    )

    items = (
        crawl_list(
            http
        )
    )

    if not items:

        raise RuntimeError(
            "채용 목록을 찾지 "
            "못했습니다. "
            "건양대병원 사이트 구조가 "
            "바뀌었는지 확인하세요."
        )

    print()

    print(
        f"목록 총 "
        f"{len(items)}건 발견"
    )

    print()

    created = 0
    updated = 0
    failed = 0

    with Session(
        engine
    ) as db:

        try:

            for (
                index,
                item,
            ) in enumerate(
                items,
                start=1,
            ):

                print(
                    f"[{index}/"
                    f"{len(items)}] "
                    f"{item.title}"
                )

                try:

                    record = (
                        fetch_detail_record(
                            http,
                            item,
                        )
                    )

                    result = (
                        upsert_official_job_record(
                            db,
                            record,

                            source_name=(
                                SOURCE_NAME
                            ),
                        )
                    )

                    if result[
                        "created"
                    ]:

                        created += 1

                        print(
                            "  → CREATE "
                            f"resource_id="
                            f"{result['resource_id']}"
                        )

                    else:

                        updated += 1

                        print(
                            "  → UPDATE "
                            f"resource_id="
                            f"{result['resource_id']}"
                        )

                except Exception as error:

                    failed += 1

                    print(
                        "  → FAIL "
                        f"{type(error).__name__}: "
                        f"{error}"
                    )

            db.commit()

        except Exception:

            db.rollback()

            raise

    print()

    print(
        "=" * 70
    )

    print(
        "건양대학교병원 "
        "채용 수집 완료"
    )

    print(
        "=" * 70
    )

    print(
        "신규:",
        created,
    )

    print(
        "갱신:",
        updated,
    )

    print(
        "실패:",
        failed,
    )


if __name__ == "__main__":
    main()