
from __future__ import annotations

import argparse
import json
import time

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import requests

from bs4 import BeautifulSoup
from sqlalchemy import select

from src.db.database import SessionLocal
from src.db.models import ProgramDetail, Resource
from src.loaders.common import (
    upsert_source_document,
    utc_now,
)


BOARD_ID = "BBSMSTR_000000000563"

BASE_URL = (
    f"https://mie.konyang.ac.kr/cop/bbs/"
    f"{BOARD_ID}/"
)

LIST_URL = BASE_URL + "selectBoardList.do"

PROGRESS_FILE = Path(
    "data/raw/programs/"
    "konyang_medical_notice_progress.json"
)

SIGNALS = (
    "비교과",
    "프로그램",
    "교육",
    "특강",
    "워크숍",
    "세미나",
    "캠프",
    "해커톤",
    "경진대회",
    "공모전",
    "멘토링",
    "취업",
    "창업",
    "인턴십",
    "연수",
    "현장실습",
    "학술대회",
    "심포지엄",
    "역량 강화",
    "해외프로그램",
)

EXCLUDE = (
    "장학금",
    "주차 안내",
    "퇴직교원",
    "부정행위",
    "등록금",
)


@dataclass(frozen=True)
class Notice:
    notice_id: str
    title: str
    url: str
    page: int


def get_html(
    session: requests.Session,
    url: str,
    params: dict | None = None,
) -> str:

    response = session.get(
        url,
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    actual = urlsplit(response.url)

    if (
        actual.scheme != "https"
        or actual.hostname != "mie.konyang.ac.kr"
    ):
        raise RuntimeError(
            f"예상 외 사이트로 이동: {response.url}"
        )

    response.encoding = (
        response.apparent_encoding
        or response.encoding
    )

    return response.text


def list_notices(
    html: str,
    page: int,
) -> list[Notice]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    records: dict[str, Notice] = {}

    for anchor in soup.find_all(
        "a",
        href=True,
    ):

        url = urljoin(
            LIST_URL,
            anchor["href"],
        )

        parsed = urlsplit(url)

        if parsed.hostname != "mie.konyang.ac.kr":
            continue

        expected_path = (
            f"/cop/bbs/{BOARD_ID}/"
            "selectBoardArticle.do"
        )

        if expected_path not in parsed.path:
            continue

        notice_id = parse_qs(
            parsed.query
        ).get(
            "nttId",
            [""],
        )[0]

        title = " ".join(
            anchor.get_text(
                " ",
                strip=True,
            ).split()
        )

        if not notice_id.isdigit() or not title:
            continue

        records[notice_id] = Notice(
            notice_id=notice_id,
            title=title,
            url=url,
            page=page,
        )

    return list(records.values())


def is_program(
    title: str,
) -> bool:

    has_signal = any(
        word in title
        for word in SIGNALS
    )

    has_exclusion = any(
        word in title
        for word in EXCLUDE
    )

    return has_signal and not has_exclusion


def article_text(html: str, title: str) -> str:
    from src.collectors.programs.repair_konyang_notice_content import parse_notice
    return parse_notice(html, title).body


def save_page(
    rows: list[tuple[Notice, str]],
) -> tuple[int, int]:

    created = 0
    updated = 0

    with SessionLocal() as db:

        with db.begin():

            for notice, content in rows:

                key = (
                    "PROGRAM:KY_MED_NOTICE:"
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
                            f"중복 키 충돌: {key}"
                        )

                    updated += 1

                resource.name = notice.title
                resource.organization = (
                    "건양대학교 의과대학 (게시)"
                )

                resource.description = (
                    content[:1500]
                    if content
                    else notice.title
                )

                resource.canonical_url = notice.url

                # 모집 상태가 확인되지 않은 공지는
                # 현재 모집 가능으로 표시하지 않는다.
                resource.is_active = False
                resource.last_seen_at = utc_now()

                db.flush()

                metadata = {
                    "board_id": BOARD_ID,
                    "notice_id": notice.notice_id,
                    "list_page": notice.page,
                    "audience_confirmed": False,
                    "recruitment_status": "UNKNOWN",
                    "source_scope": (
                        "MEDICAL_COLLEGE_NOTICE"
                    ),
                    "content_extracted": bool(content),
                }

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
                    source_name=(
                        "KONYANG_MEDICAL_COLLEGE_NOTICES"
                    ),
                    source_type="UNIVERSITY_WEB",
                    document_type="NOTICE",
                    source_record_key=(
                        f"{BOARD_ID}:{notice.notice_id}"
                    ),
                    source_url=notice.url,
                    title=notice.title,
                    content=content or notice.title,
                    raw_metadata=metadata,
                    discovery_channel=(
                        "OFFICIAL_MEDICAL_COLLEGE_BOARD"
                    ),
                    fetch_status="SUCCESS",
                    validation_status=(
                        "ACCEPTED"
                        if len(content) >= 80
                        else "PENDING"
                    ),
                    trust_level="B",
                    evidence_eligible=(
                        len(content) >= 80
                    ),
                )

    return created, updated


def save_progress(
    page: int,
) -> None:

    PROGRESS_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = PROGRESS_FILE.with_suffix(
        ".json.tmp"
    )

    temp.write_text(
        json.dumps(
            {
                "next_page": page + 1,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp.replace(PROGRESS_FILE)


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--end-page",
        type=int,
        default=42,
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    parser.add_argument(
        "--policy-reviewed",
        action="store_true",
    )

    args = parser.parse_args()

    if (
        args.start_page < 1
        or args.end_page < args.start_page
        or args.delay < 1.0
    ):
        parser.error(
            "페이지 범위와 delay를 확인하세요."
        )

    if args.apply and not args.policy_reviewed:
        parser.error(
            "자동 수집 및 원문 저장 조건 확인 후 "
            "--policy-reviewed를 사용하세요."
        )

    first = args.start_page

    if args.apply and PROGRESS_FILE.exists():

        state = json.loads(
            PROGRESS_FILE.read_text(
                encoding="utf-8"
            )
        )

        first = max(
            first,
            int(state["next_page"]),
        )

    session = requests.Session()

    session.headers["User-Agent"] = (
        "CareerQuest-University-Research/0.1 "
        "(low-frequency public notice fetch)"
    )

    try:

        for page in range(
            first,
            args.end_page + 1,
        ):

            try:

                html = get_html(
                    session,
                    LIST_URL,
                    {"pageIndex": page},
                )

                all_notices = list_notices(
                    html,
                    page,
                )

                if not all_notices:

                    print(
                        f"[중단] {page}페이지에서 "
                        "게시물 링크 0개. "
                        "페이지 구조 확인 필요."
                    )

                    break

                candidates = [
                    notice
                    for notice in all_notices
                    if is_program(notice.title)
                ]

                print(
                    f"[page {page}] "
                    f"게시물={len(all_notices)}, "
                    f"PROGRAM 후보={len(candidates)}"
                )

                rows = []

                for notice in candidates:

                    time.sleep(args.delay)

                    body = article_text(
                        get_html(
                            session,
                            notice.url,
                        ),
                        notice.title,
                    )

                    rows.append(
                        (notice, body)
                    )

                    print(
                        f"  {notice.title[:65]} "
                        f"| 본문={len(body)}자"
                    )

                if args.apply:

                    created, updated = save_page(
                        rows
                    )

                    save_progress(
                        page
                    )

                    print(
                        f"  [DB] 신규={created}, "
                        f"갱신={updated}, "
                        f"다음={page + 1}"
                    )

            except (
                requests.RequestException,
                RuntimeError,
                ValueError,
            ) as exc:

                print(
                    f"[중단] page={page}: {exc}"
                )

                break

            time.sleep(args.delay)

    finally:
        session.close()


if __name__ == "__main__":
    main()
