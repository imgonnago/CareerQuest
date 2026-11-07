
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


SEOUL = ZoneInfo("Asia/Seoul")

SOURCE_NAME = "KONYANG_DEPARTMENT_PROGRAM_NOTICES"

PROGRESS_DIR = Path(
    "data/raw/programs/department_notices"
)


# ============================================================
# 학과별 공식 게시판 Registry
# ============================================================

@dataclass(frozen=True)
class DepartmentBoard:

    key: str
    name: str
    host: str
    board_id: str
    pages: int

    @property
    def base_url(self) -> str:
        return (
            f"https://{self.host}/cop/bbs/"
            f"{self.board_id}/"
        )

    @property
    def list_url(self) -> str:
        return self.base_url + "selectBoardList.do"

    @property
    def article_path(self) -> str:
        return (
            f"/cop/bbs/{self.board_id}/"
            "selectBoardArticle.do"
        )


BOARDS = {

    "medical_it": DepartmentBoard(
        "medical_it",
        "의료IT공학과",
        "gymnasium.konyang.ac.kr",
        "BBSMSTR_000000000062",
        24,
    ),

    "biomedical": DepartmentBoard(
        "biomedical",
        "의공학과",
        "sec.konyang.ac.kr",
        "BBSMSTR_000000000276",
        16,
    ),

    "materials": DepartmentBoard(
        "materials",
        "의료신소재학과",
        "insung.konyang.ac.kr",
        "BBSMSTR_000000000350",
        25,
    ),
}


# ============================================================
# PROGRAM 선별
# ============================================================

PROGRAM_TERMS = (
    "비교과",
    "프로그램",
    "교육생",
    "수강생",
    "자격취득",
    "자격증 교육",
    "양성교육",
    "직무교육",
    "기초과정",
    "전문가 과정",
    "전문가 양성",
    "특강",
    "워크숍",
    "세미나",
    "캠프",
    "해커톤",
    "경진대회",
    "공모전",
    "멘토링",
    "튜터링",
    "부트캠프",
    "아카데미",
    "연수",
    "인턴십",
    "현장실습",
    "교육 지원",
    "교육과정 모집",
    "교육과정 안내",
    "설계프로젝트",
    "토익몰입",
    "취업역량",
    "취업교육",
)

EXCLUDE_TERMS = (
    "[축]",
    "[수상]",
    "수상 소식",
    "수상 결과",
    "금상 수상",
    "교육과정 적용년도",
    "교육과정 경과조치",
    "교과목 이수구분",
    "수강신청",
    "졸업사정",
    "장학금",
    "등록금",
    "학사일정",
    "공결",
    "병결",
    "휴학",
    "복학",
    "성적 정정",
    "학군사관",
    "연구활동종사자 정기 안전교육",
    "대학원 신입생",
    "사회봉사 관련",
)


def clean(value: str | None) -> str:
    return re.sub(
        r"\s+", " ", value or ""
    ).strip()


def program_title(title: str) -> bool:

    return (
        any(t in title for t in PROGRAM_TERMS)
        and not any(t in title for t in EXCLUDE_TERMS)
    )


# ============================================================
# HTTP
# ============================================================

def fetch(
    session: requests.Session,
    url: str,
    host: str,
    params=None,
) -> str:

    response = session.get(
        url,
        params=params,
        timeout=(10, 30),
    )

    response.raise_for_status()

    actual = urlsplit(response.url)

    if (
        actual.scheme != "https"
        or actual.hostname != host
    ):
        raise RuntimeError(
            f"예상 밖의 URL 이동: {response.url}"
        )

    response.encoding = (
        response.apparent_encoding
        or response.encoding
    )

    return response.text


# ============================================================
# 목록 파싱
# ============================================================

@dataclass(frozen=True)
class Notice:

    notice_id: str
    title: str
    url: str
    page: int
    published_at: datetime | None


def extract_notices(
    html: str,
    board: DepartmentBoard,
    page: int,
) -> list[Notice]:

    soup = BeautifulSoup(
        html, "html.parser"
    )

    result: dict[str, Notice] = {}

    for anchor in soup.find_all(
        "a", href=True
    ):

        url = urljoin(
            board.list_url,
            anchor.get("href", ""),
        )

        parsed = urlsplit(url)

        if (
            parsed.hostname != board.host
            or parsed.path != board.article_path
        ):
            continue

        notice_id = parse_qs(
            parsed.query
        ).get("nttId", [""])[0]

        title = clean(
            anchor.get_text(" ", strip=True)
        )

        if (
            not notice_id.isdigit()
            or len(title) < 3
        ):
            continue

        parent = anchor.find_parent("tr")

        row_text = (
            clean(parent.get_text(" ", strip=True))
            if parent
            else ""
        )

        match = re.search(
            r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})",
            row_text,
        )

        published_at = None

        if match:
            try:
                published_at = datetime(
                    *map(int, match.groups()),
                    tzinfo=SEOUL,
                )
            except ValueError:
                pass

        result[notice_id] = Notice(
            notice_id,
            title,
            url,
            page,
            published_at,
        )

    return list(result.values())


# ============================================================
# 상세 본문
# ============================================================

def sanitize(text: str) -> str:

    text = re.sub(
        r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
        "[이메일 생략]",
        text,
    )

    return re.sub(
        r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)",
        "[휴대전화 생략]",
        text,
    )


def extract_body(
    html: str,
) -> tuple[str, bool]:

    from src.collectors.programs.repair_konyang_notice_content import (
        parse_notice,
    )

    result = parse_notice(html)

    return result.body, result.attachment


# ============================================================
# 진행 기록
# ============================================================

def progress_path(
    board: DepartmentBoard,
) -> Path:

    return (
        PROGRESS_DIR
        / f"{board.key}.json"
    )


def save_progress(
    board: DepartmentBoard,
    next_page: int,
) -> None:

    path = progress_path(board)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = path.with_suffix(
        ".json.tmp"
    )

    temp.write_text(
        json.dumps(
            {"next_page": next_page},
            indent=2,
        ),
        encoding="utf-8",
    )

    temp.replace(path)


# ============================================================
# PostgreSQL 저장
# ============================================================

def save_rows(
    board: DepartmentBoard,
    rows: list[tuple[Notice, str, bool]],
) -> tuple[int, int]:

    created = 0
    updated = 0

    with SessionLocal() as db:

        with db.begin():

            for notice, content, attachment in rows:

                key = (
                    "PROGRAM:KY_DEPT:"
                    f"{board.board_id}:{notice.notice_id}"
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
                        raise ValueError(
                            f"리소스 중복키 충돌: {key}"
                        )

                    updated += 1

                metadata = {
                    "board_id": board.board_id,
                    "department_posted": board.name,
                    "notice_id": notice.notice_id,
                    "list_page": notice.page,
                    "target_department_confirmed": False,
                    "application_status": "UNKNOWN",
                    "attachment_present": attachment,
                    "attachment_content_parsed": False,
                    "body_text_available": (
                        len(content) >= 80
                    ),
                }

                resource.name = notice.title

                resource.organization = (
                    f"건양대학교 {board.name} (게시)"
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
                        f"{board.board_id}:{notice.notice_id}"
                    ),
                    source_url=notice.url,
                    title=notice.title,
                    content=content or notice.title,
                    raw_metadata=metadata,
                    published_at=notice.published_at,
                    discovery_channel=(
                        "KY_MEDICAL_DEPARTMENT_NOTICES"
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


# ============================================================
# 한 학과 수집
# ============================================================

def collect_board(
    board: DepartmentBoard,
    session: requests.Session,
    start_page: int,
    end_page: int,
    delay: float,
    apply: bool,
) -> None:

    start = start_page
    path = progress_path(board)

    if apply and path.exists():

        state = json.loads(
            path.read_text(encoding="utf-8")
        )

        start = max(
            start,
            int(state["next_page"]),
        )

    print(
        f"\n[{board.name}] "
        f"pages={start}~{end_page} "
        f"/ {'APPLY' if apply else 'DRY-RUN'}"
    )

    for page in range(
        start,
        end_page + 1,
    ):

        try:

            html = fetch(
                session,
                board.list_url,
                board.host,
                {"pageIndex": page},
            )

            notices = extract_notices(
                html, board, page
            )

            if not notices:

                print(
                    f"[중단] {page}페이지에서 "
                    "게시물 링크 0건 — "
                    "구조·끝페이지 확인 필요"
                )

                break

            candidates = [
                notice
                for notice in notices
                if program_title(notice.title)
            ]

            print(
                f"[page {page}] "
                f"전체={len(notices)} "
                f"PROGRAM 후보={len(candidates)}"
            )

            rows: list[
                tuple[Notice, str, bool]
            ] = []

            for notice in candidates:

                time.sleep(delay)

                body, attachment = extract_body(
                    fetch(
                        session,
                        notice.url,
                        board.host,
                    )
                )

                rows.append(
                    (notice, body, attachment)
                )

                print(
                    f"  {notice.title[:65]} "
                    f"| 본문={len(body)}자 "
                    f"| 첨부={'Y' if attachment else 'N'}"
                )

            if apply:

                created, updated = save_rows(
                    board, rows
                )

                save_progress(
                    board,
                    page + 1,
                )

                print(
                    f"  [DB] created={created} "
                    f"updated={updated} "
                    f"next={page + 1}"
                )

            time.sleep(delay)

        except (
            requests.RequestException,
            RuntimeError,
            ValueError,
            OSError,
        ) as exc:

            print(
                f"[중단] {board.name} "
                f"page={page}: {exc}"
            )

            break


# ============================================================
# CLI
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "건양대 메디컬 학과별 PROGRAM 수집"
        )
    )

    parser.add_argument(
        "--department",
        choices=["all", *BOARDS],
        default="all",
    )

    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--end-page",
        type=int,
        default=None,
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
        or args.delay < 1.0
        or (
            args.end_page is not None
            and args.end_page < args.start_page
        )
    ):
        parser.error(
            "페이지 범위와 delay를 확인하세요"
        )

    if args.apply and not args.policy_reviewed:
        parser.error(
            "자동 수집 및 원문 저장 허용 범위를 확인한 뒤 "
            "--policy-reviewed를 사용하세요"
        )

    selected = (
        list(BOARDS.values())
        if args.department == "all"
        else [BOARDS[args.department]]
    )

    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "CareerQuest-AcademicProject/0.1 "
            "(low-frequency university notices)"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9",
    })

    try:

        for board in selected:

            end = (
                min(args.end_page, board.pages)
                if args.end_page is not None
                else board.pages
            )

            if end >= args.start_page:

                collect_board(
                    board,
                    session,
                    args.start_page,
                    end,
                    args.delay,
                    args.apply,
                )

    finally:
        session.close()


if __name__ == "__main__":
    main()
