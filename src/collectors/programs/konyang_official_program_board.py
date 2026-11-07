
from __future__ import annotations

import argparse
import json
import re
import time

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select

from src.db.database import SessionLocal
from src.db.models import ProgramDetail, Resource
from src.loaders.common import (
    upsert_source_document,
    utc_now,
)


HOST = "www.konyang.ac.kr"
BOARD_ID = "BBSMSTR_000000000587"

LIST_URL = (
    f"https://{HOST}/cop/bbs/"
    f"{BOARD_ID}/selectBoardList.do"
)

ARTICLE_PATH = (
    f"/cop/bbs/{BOARD_ID}/selectBoardArticle.do"
)

SOURCE_NAME = "KONYANG_OFFICIAL_EXTRACURRICULAR"

PROGRESS_PATH = Path(
    "data/raw/programs/"
    "konyang_official_extra_progress.json"
)

SEOUL = ZoneInfo("Asia/Seoul")


PROGRAM_TERMS = (
    "비교과", "교육", "프로그램", "특강",
    "워크숍", "세미나", "캠프", "경진대회",
    "공모전", "해커톤", "멘토링",
    "부트캠프", "취업", "진로", "창업",
    "인턴", "연수", "실습", "훈련",
    "아카데미", "자격", "설명회",
    "컨설팅", "특별강좌", "현장탐방",
    "스터디", "교환·파견",
)

NON_PROGRAM_TERMS = (
    "장학생 선발", "장학금 지급",
    "등록금 납부", "공결", "병결",
    "휴학", "복학", "기숙사 입사",
    "수강신청", "졸업사정", "성적 정정",
    "시험 일정", "교직원 채용",
)


@dataclass(frozen=True)
class Notice:
    notice_id: str
    title: str
    url: str
    page: int
    published_at: datetime | None


def clean(value: str | None) -> str:
    return re.sub(
        r"\s+", " ", value or ""
    ).strip()


def fetch(
    session: requests.Session,
    url: str,
    params=None,
) -> str:

    response = session.get(
        url,
        params=params,
        timeout=(10, 30),
    )

    response.raise_for_status()

    parsed = urlsplit(response.url)

    if (
        parsed.scheme != "https"
        or parsed.hostname != HOST
    ):
        raise RuntimeError(
            f"예상하지 못한 이동: {response.url}"
        )

    response.encoding = (
        response.apparent_encoding
        or response.encoding
    )

    return response.text


def get_list(
    html: str,
    page: int,
) -> list[Notice]:

    soup = BeautifulSoup(
        html, "html.parser"
    )

    results: dict[str, Notice] = {}

    for anchor in soup.select("a[href]"):

        url = urljoin(
            LIST_URL,
            anchor.get("href", ""),
        )

        parsed = urlsplit(url)

        if (
            parsed.hostname != HOST
            or parsed.path != ARTICLE_PATH
        ):
            continue

        ntt_id = parse_qs(
            parsed.query
        ).get("nttId", [""])[0]

        if not ntt_id.isdigit():
            continue

        title = clean(
            anchor.get_text(" ", strip=True)
        )

        if len(title) < 3:
            continue

        row = anchor.find_parent("tr")

        match = re.search(
            r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})",
            (
                row.get_text(" ", strip=True)
                if row else ""
            ),
        )

        published = None

        if match:
            try:
                published = datetime(
                    *map(int, match.groups()),
                    tzinfo=SEOUL,
                )
            except ValueError:
                pass

        results[ntt_id] = Notice(
            ntt_id,
            title,
            url,
            page,
            published,
        )

    return list(results.values())


def is_program_title(title: str) -> bool:

    if any(
        term in title
        for term in NON_PROGRAM_TERMS
    ):
        return False

    return any(
        term in title
        for term in PROGRAM_TERMS
    )


def campus_scope(title: str) -> str:

    medical = "메디컬" in title
    glocal = "글로컬" in title

    if medical and glocal:
        return "BOTH"

    if medical:
        return "MEDICAL"

    if glocal:
        return "GLOCAL"

    return "UNKNOWN"


def detail_text(html: str, title: str) -> str:
    from src.collectors.programs.repair_konyang_notice_content import parse_notice
    return parse_notice(html, title).body


def remove_contact_details(text: str) -> str:

    text = re.sub(
        r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
        "[이메일 생략]",
        text,
    )

    return re.sub(
        r"(?<!\d)01[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)",
        "[휴대전화 생략]",
        text,
    )


def save_page(
    rows: list[tuple[Notice, str]],
) -> tuple[int, int]:

    created = updated = 0

    with SessionLocal() as db:

        with db.begin():

            for notice, content in rows:

                key = (
                    "PROGRAM:KONYANG:EXTRA:"
                    f"{BOARD_ID}:{notice.notice_id}"
                )

                resource = db.scalar(
                    select(Resource).where(
                        Resource.dedup_key == key
                    )
                )

                if resource is None:

                    resource = Resource(
                        resource_type="PROGRAM",
                        dedup_key=key,
                    )

                    db.add(resource)
                    created += 1

                else:

                    if resource.resource_type != "PROGRAM":
                        raise RuntimeError(
                            f"리소스 키 충돌: {key}"
                        )

                    updated += 1

                scope = campus_scope(
                    notice.title
                )

                metadata = {
                    "source_board": BOARD_ID,
                    "notice_id": notice.notice_id,
                    "list_page": notice.page,
                    "campus_scope_from_title": scope,
                    "campus_scope_confirmed": False,
                    "participation_eligibility": "UNKNOWN",
                    "application_status": "UNKNOWN",
                    "content_extracted": len(content) >= 80,
                    "attachment_not_parsed": True,
                }

                resource.name = notice.title
                resource.organization = (
                    "건양대학교 (공식 비교과 게시)"
                )

                resource.description = (
                    content or notice.title
                )[:1500]

                resource.canonical_url = notice.url
                resource.published_at = notice.published_at
                resource.is_active = False
                resource.last_seen_at = utc_now()

                db.flush()

                detail = db.get(
                    ProgramDetail,
                    resource.resource_id,
                )

                if detail is None:

                    detail = ProgramDetail(
                        resource_id=resource.resource_id
                    )

                    db.add(detail)

                detail.raw_metadata = metadata

                upsert_source_document(
                    db,
                    resource_id=resource.resource_id,
                    source_name=SOURCE_NAME,
                    source_type="UNIVERSITY_WEB",
                    document_type="NOTICE",
                    source_record_key=(
                        f"{BOARD_ID}:{notice.notice_id}"
                    ),
                    source_url=notice.url,
                    title=notice.title,
                    content=content or notice.title,
                    raw_metadata=metadata,
                    published_at=notice.published_at,
                    discovery_channel=(
                        "OFFICIAL_KONYANG_EXTRA_BOARD"
                    ),
                    fetch_status="SUCCESS",
                    validation_status=(
                        "ACCEPTED"
                        if len(content) >= 80
                        else "PENDING"
                    ),
                    trust_level="B",
                    evidence_eligible=len(content) >= 80,
                )

    return created, updated


def save_progress(next_page: int) -> None:

    PROGRESS_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = PROGRESS_PATH.with_suffix(
        ".json.tmp"
    )

    tmp.write_text(
        json.dumps(
            {"next_page": next_page},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    tmp.replace(PROGRESS_PATH)


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--start-page", type=int, default=1
    )

    parser.add_argument(
        "--end-page", type=int, default=144
    )

    parser.add_argument(
        "--delay", type=float, default=1.5
    )

    parser.add_argument(
        "--apply", action="store_true"
    )

    parser.add_argument(
        "--policy-reviewed",
        action="store_true",
    )

    args = parser.parse_args()

    if (
        args.start_page < 1
        or args.end_page < args.start_page
        or args.delay < 1
    ):
        parser.error(
            "페이지 범위 및 요청 간격을 확인하세요."
        )

    if args.apply and not args.policy_reviewed:
        parser.error(
            "자동 수집과 원문 저장 허용 범위 확인 후 "
            "--policy-reviewed를 지정하세요."
        )

    first = args.start_page

    if args.apply and PROGRESS_PATH.exists():

        state = json.loads(
            PROGRESS_PATH.read_text(
                encoding="utf-8"
            )
        )

        first = max(
            first,
            int(state["next_page"]),
        )

    session = requests.Session()

    session.headers["User-Agent"] = (
        "CareerQuest-AcademicProject/0.1 "
        "(low-frequency official notices)"
    )

    try:

        for page in range(
            first,
            args.end_page + 1,
        ):

            try:

                html = fetch(
                    session,
                    LIST_URL,
                    {"pageIndex": page},
                )

                notices = get_list(
                    html, page
                )

                if not notices:
                    raise RuntimeError(
                        "게시물 링크 0개: 빈 페이지 또는 "
                        "HTML 변경 가능성"
                    )

                candidates = [
                    n for n in notices
                    if is_program_title(n.title)
                ]

                print(
                    f"[page {page}] "
                    f"게시물={len(notices)}, "
                    f"프로그램 후보={len(candidates)}"
                )

                rows = []

                for notice in candidates:

                    time.sleep(args.delay)

                    content = remove_contact_details(
                        detail_text(
                            fetch(session, notice.url),
                            notice.title,
                        )
                    )

                    rows.append(
                        (notice, content)
                    )

                    print(
                        f"  {notice.title[:55]} "
                        f"| 본문={len(content)}자"
                    )

                if args.apply:

                    created, updated = save_page(
                        rows
                    )

                    save_progress(page + 1)

                    print(
                        f"  [DB] 신규={created}, "
                        f"갱신={updated}, "
                        f"다음={page + 1}"
                    )

                else:
                    print(
                        "  [DRY RUN] DB 저장 없음"
                    )

                time.sleep(args.delay)

            except (
                requests.RequestException,
                RuntimeError,
                ValueError,
            ) as exc:

                print(
                    f"[중단] page={page}: {exc}"
                )
                break

    except KeyboardInterrupt:
        print(
            "[중단] 마지막으로 완료한 "
            "페이지까지 저장됨"
        )

    finally:
        session.close()


if __name__ == "__main__":
    main()
