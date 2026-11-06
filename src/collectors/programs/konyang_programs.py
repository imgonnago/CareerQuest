
from __future__ import annotations

import argparse
import hashlib
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select

from src.collectors.jobs.konyang_jobs import (
    create_session,
    extract_article_content,
    parse_list_page,
)
from src.db.database import SessionLocal
from src.db.models import ProgramDetail, Resource
from src.loaders.common import upsert_source_document, utc_now


CATALOG_URL = (
    "https://www.konyang.ac.kr/kor/sub06_03_06_02.do"
)

EVENT_LIST_URL = (
    "https://job.konyang.ac.kr/cop/bbs/"
    "BBSMSTR_000000001401/selectBoardList.do"
)

PROGRAM_SIGNALS = (
    "프로그램", "교육", "특강", "멘토링",
    "경진대회", "공모전", "대회", "캠프",
    "워크숍", "세미나", "취업박람회",
    "설명회", "컨설팅", "부트캠프",
    "현장실습", "인턴십", "일경험",
    "연수", "해커톤", "진로", "직무",
    "취업지원", "자격증", "아카데미",
    "스터디", "서포터즈", "동아리",
    "현장탐방", "체험", "대외활동",
    "교육생",
)

JOB_ONLY_SIGNALS = (
    "직원 채용", "신입사원 채용",
    "경력직 채용", "공무원 임용",
    "채용공고", "채용 공고",
    "정규직 채용", "임용시험",
)


def clean(value: str | None) -> str:
    return re.sub(
        r"\s+", " ", value or ""
    ).strip()


def digest(*parts: str) -> str:
    key = "|".join(
        re.sub(r"\s+", "", x).casefold()
        for x in parts
    )
    return hashlib.sha256(
        key.encode("utf-8")
    ).hexdigest()


def official_get(
    session: requests.Session,
    url: str,
):
    response = session.get(url, timeout=30)
    response.raise_for_status()

    if (
        not response.url.startswith("https://")
        or not response.url.split("/")[2]
        .endswith("konyang.ac.kr")
    ):
        raise RuntimeError(
            f"예상하지 못한 URL 이동: {response.url}"
        )

    response.encoding = (
        response.apparent_encoding
        or response.encoding
    )
    return response


# =========================================================
# 공식 비교과 카탈로그
# =========================================================

def catalog_records(
    session: requests.Session,
) -> list[dict]:

    response = official_get(
        session, CATALOG_URL
    )

    soup = BeautifulSoup(
        response.text, "html.parser"
    )

    tables = []

    for table in soup.find_all("table"):

        header = clean(
            table.get_text(" ", strip=True)[:1200]
        )

        if not all(
            x in header
            for x in (
                "프로그램명",
                "운영시기",
                "핵심역량",
            )
        ):
            continue

        rows = []

        for tr in table.find_all("tr"):

            cells = tr.find_all(
                ["td", "th"],
                recursive=False,
            )

            if len(cells) != 6:
                continue

            (
                title,
                period,
                audience,
                description,
                department,
                competence,
            ) = [
                clean(
                    cell.get_text(
                        " ", strip=True
                    )
                )
                for cell in cells
            ]

            if (
                not title
                or title == "프로그램명"
                or not description
            ):
                continue

            rows.append((
                title,
                period,
                audience,
                description,
                department,
                competence,
            ))

        if rows:
            tables.append(rows)

    if not tables:
        raise RuntimeError(
            "건양대학교 비교과 표를 "
            "찾지 못했습니다."
        )

    # 전체 표는 계절별 표보다 많은 행을 포함.
    selected = max(tables, key=len)

    if len(selected) < 20:
        raise RuntimeError(
            "카탈로그 추출 수가 "
            f"비정상적으로 적습니다: {len(selected)}"
        )

    result = {}

    for (
        title,
        period,
        audience,
        description,
        department,
        competence,
    ) in selected:

        identity = digest(
            title, period, audience, department
        )

        if (
            identity in result
            and result[identity]["description"]
            != description
        ):
            identity = digest(
                title, period, audience,
                department, description,
            )

        result[identity] = {
            "source_kind": "catalog",
            "identity": identity,
            "title": title,
            "description": description,
            "url": response.url,
            "audience": audience,
            "published_at": None,
            "metadata": {
                "catalog_only": True,
                "catalog_year_confirmed": False,
                "application_open_confirmed": False,
                "operating_period": period,
                "department_contact": department,
                "core_competence": competence,
            },
            "content": "\n".join((
                f"프로그램명: {title}",
                f"운영시기 안내: {period}",
                f"참여대상: {audience}",
                f"프로그램 소개: {description}",
                f"운영부서: {department}",
                f"핵심역량: {competence}",
                "카탈로그 자료: 현재 모집 및 "
                "시행 연도는 별도 공고로 확인 필요",
            )),
        }

    return list(result.values())


# =========================================================
# 공식 취업행사 게시판
# =========================================================

def event_title_allowed(
    title: str,
) -> bool:

    if (
        any(x in title for x in JOB_ONLY_SIGNALS)
        and not any(
            x in title
            for x in (
                "설명회", "취업지원",
                "교육", "프로그램",
                "채용박람회",
            )
        )
    ):
        return False

    return any(
        x in title for x in PROGRAM_SIGNALS
    )


def event_records(
    session: requests.Session,
    start_page: int,
    pages: int,
    delay: float,
):

    for page in range(
        start_page,
        start_page + pages,
    ):

        response = official_get(
            session,
            f"{EVENT_LIST_URL}?pageIndex={page}",
        )

        candidates = parse_list_page(
            response.text, page
        )

        print(
            f"[건양대 취업행사] "
            f"page={page} 후보={len(candidates)}"
        )

        if not candidates:
            raise RuntimeError(
                f"{page}페이지에서 후보 0건. "
                "수집을 중단하고 해당 페이지를 확인하세요."
            )

        time.sleep(delay)
        records = []

        for candidate in candidates:

            if not event_title_allowed(
                candidate.title
            ):
                continue

            detail = official_get(
                session,
                candidate.detail_url,
            )

            content = extract_article_content(
                detail.text,
                candidate.title,
            )

            if len(content) < 60:
                print(
                    "  [보류] 본문 부족: "
                    f"{candidate.title}"
                )
                time.sleep(delay)
                continue

            records.append({
                "source_kind": "event",
                "identity": candidate.ntt_id,
                "title": candidate.title,
                "description": content[:1500],
                "content": content,
                "url": detail.url,
                "audience": None,
                "published_at": (
                    datetime.combine(
                        candidate.published_date,
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    )
                    if candidate.published_date
                    else None
                ),
                "metadata": {
                    "notice_id": candidate.ntt_id,
                    "list_page": page,
                    "original_publisher":
                        "건양대학교 입학취업처 게시판",
                    "actual_program_organizer":
                        "확인 필요",
                    "application_open_confirmed":
                        False,
                },
            })

            time.sleep(delay)

        yield page, records


# =========================================================
# PostgreSQL upsert
# =========================================================

def save_records(
    rows: list[dict],
) -> dict:

    counts = {
        "created": 0,
        "updated": 0,
    }

    with SessionLocal() as db:

        with db.begin():

            for row in rows:

                kind = row["source_kind"]
                identity = row["identity"]

                key = (
                    f"PROGRAM:KONYANG:"
                    f"{kind.upper()}:{identity}"
                )

                name = (
                    "KONYANG_EXTRACURRICULAR_CATALOG"
                    if kind == "catalog"
                    else "KONYANG_CAREER_EVENTS"
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
                    counts["created"] += 1

                else:

                    if resource.resource_type != "PROGRAM":
                        raise RuntimeError(
                            f"식별키 충돌: {key}"
                        )

                    counts["updated"] += 1

                resource.name = row["title"]

                resource.organization = (
                    "건양대학교"
                    if kind == "catalog"
                    else "건양대학교 입학취업처(게시)"
                )

                resource.description = row["description"]
                resource.canonical_url = row["url"]
                resource.published_at = row["published_at"]

                # 모집 상태 미확인 자료는
                # 현재 신청 가능으로 표시하지 않음.
                resource.is_active = False
                resource.last_seen_at = utc_now()

                db.flush()

                extra = db.get(
                    ProgramDetail,
                    resource.resource_id,
                )

                if extra is None:
                    extra = ProgramDetail(
                        resource_id=resource.resource_id
                    )
                    db.add(extra)

                extra.target_audience = row["audience"]
                extra.raw_metadata = row["metadata"]

                upsert_source_document(
                    db,
                    resource_id=resource.resource_id,
                    source_name=name,
                    source_type="UNIVERSITY_WEB",
                    document_type="WEB_PAGE",
                    source_record_key=(
                        f"KONYANG:{kind}:{identity}"
                    ),
                    source_url=row["url"],
                    title=row["title"],
                    content=row["content"],
                    raw_metadata=row["metadata"],
                    published_at=row["published_at"],
                    discovery_channel=(
                        "OFFICIAL_KONYANG_PROGRAM"
                    ),
                    fetch_status="SUCCESS",
                    validation_status="ACCEPTED",
                    trust_level=(
                        "A"
                        if kind == "catalog"
                        else "B"
                    ),
                    evidence_eligible=True,
                )

    return counts


# =========================================================
# CLI
# =========================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "CareerQuest 건양대학교 PROGRAM 수집"
        )
    )

    parser.add_argument(
        "--mode",
        choices=["catalog", "events", "both"],
        default="catalog",
    )

    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--pages",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
    )

    parser.add_argument(
        "--policy-reviewed",
        action="store_true",
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args = parser.parse_args()

    if (
        args.start_page < 1
        or args.pages < 1
        or args.delay < 1.0
    ):
        parser.error(
            "페이지는 1 이상, "
            "delay는 1초 이상이어야 합니다."
        )

    if (
        args.mode in ("events", "both")
        and not args.policy_reviewed
    ):
        parser.error(
            "게시판 수집 정책 확인 후 "
            "--policy-reviewed 지정"
        )

    session = create_session()

    try:

        if args.mode in ("catalog", "both"):

            catalog = catalog_records(session)

            print(
                f"[비교과 카탈로그] {len(catalog)}건"
            )

            for item in catalog[:12]:
                print(" ", item["title"])

            if args.apply:
                print(
                    "[DB]",
                    save_records(catalog),
                )
            else:
                print(
                    "DRY-RUN: "
                    "카탈로그 DB 변경 없음"
                )

        if args.mode in ("events", "both"):

            for page, events in event_records(
                session,
                args.start_page,
                args.pages,
                args.delay,
            ):

                print(
                    f"[취업행사 선별] "
                    f"page={page} "
                    f"PROGRAM={len(events)}"
                )

                if args.apply and events:
                    print(
                        "[DB]",
                        save_records(events),
                    )

            if not args.apply:
                print(
                    "DRY-RUN: "
                    "취업행사 DB 변경 없음"
                )

    finally:
        session.close()


if __name__ == "__main__":
    main()
