from __future__ import annotations

import argparse
import hashlib
import json
import re
import time

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from typing import Any
from urllib.parse import (
    parse_qs,
    urljoin,
    urlparse,
)
from zoneinfo import ZoneInfo

import requests

from bs4 import BeautifulSoup

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from sqlalchemy import text

from src.db.database import engine


# ============================================================
# 기본 설정
# ============================================================

BASE_URL = (
    "https://job.konyang.ac.kr"
)

LIST_URL = (
    "https://job.konyang.ac.kr/"
    "cop/bbs/BBSMSTR_000000001381/"
    "selectBoardList.do"
)

SOURCE_NAME = "KONYANG_JOB_BOARD"

SEOUL = ZoneInfo(
    "Asia/Seoul"
)

HTTP_TIMEOUT = 30

REQUEST_DELAY = 0.20

MAX_CONTENT_LENGTH = 30000


# ============================================================
# 기술 관련 보조 키워드
#
# DB Skill/JobRole 목록도 자동으로 읽고,
# 아래 목록은 탐지 보완용으로만 사용.
# ============================================================

TECH_SEED_TERMS = {
    "AI",
    "인공지능",
    "IT",
    "SW",
    "소프트웨어",

    "개발자",
    "개발직",
    "엔지니어",
    "engineer",
    "developer",

    "백엔드",
    "backend",
    "프론트엔드",
    "frontend",

    "웹개발",
    "웹 개발",
    "앱개발",
    "앱 개발",
    "모바일",

    "데이터",
    "data",
    "데이터분석",
    "데이터 분석",
    "데이터엔지니어",
    "데이터 엔지니어",

    "머신러닝",
    "machine learning",

    "딥러닝",
    "deep learning",

    "LLM",
    "RAG",
    "NLP",
    "자연어처리",

    "컴퓨터비전",
    "컴퓨터 비전",
    "영상처리",

    "클라우드",
    "cloud",

    "DevOps",
    "MLOps",

    "서버",
    "server",

    "DB",
    "database",
    "데이터베이스",

    "네트워크",
    "network",

    "정보보호",
    "보안",
    "security",

    "전산",
    "전산직",

    "Python",
    "Java",
    "JavaScript",
    "TypeScript",
    "Spring",
    "React",
    "Node.js",
    "FastAPI",

    "Docker",
    "AWS",

    "SQL",
}


SHORT_ALLOWED = {
    "ai",
    "it",
    "sw",
    "db",
    "ml",
    "dl",
    "cv",
    "nlp",
}


# ============================================================
# 데이터 구조
# ============================================================

@dataclass
class JobCandidate:
    ntt_id: str

    title: str

    detail_url: str

    published_date: (
        date | None
    )

    list_page: int


@dataclass
class ParsedJob:
    ntt_id: str

    title: str

    organization: (
        str | None
    )

    detail_url: str

    published_at: (
        datetime | None
    )

    deadline: (
        datetime | None
    )

    content: str

    responsibilities: (
        str | None
    )

    requirements: (
        str | None
    )

    preferences: (
        str | None
    )

    career_type: (
        str | None
    )

    education_requirement: (
        str | None
    )

    employment_type: (
        str | None
    )

    parse_status: str

    tech_score: int

    title_hits: list[str]

    content_hits: list[str]

    list_page: int


# ============================================================
# 문자열 처리
# ============================================================

def clean_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()


def clean_multiline(
    value: str,
) -> str:

    lines = []

    for line in value.splitlines():

        line = clean_text(
            line
        )

        if not line:
            continue

        lines.append(
            line
        )

    return "\n".join(
        lines
    )


# ============================================================
# HTTP Session
# ============================================================

def create_session(
) -> requests.Session:

    session = (
        requests.Session()
    )

    retry = Retry(
        total=4,

        connect=4,

        read=4,

        status=4,

        backoff_factor=0.8,

        status_forcelist=[
            429,
            500,
            502,
            503,
            504,
        ],

        allowed_methods=[
            "GET",
        ],
    )

    adapter = HTTPAdapter(
        max_retries=retry
    )

    session.mount(
        "https://",
        adapter,
    )

    session.mount(
        "http://",
        adapter,
    )

    session.headers.update(
        {
            "User-Agent":
                (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/150.0 Safari/537.36"
                ),

            "Accept-Language":
                "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )

    return session


def get_html(
    session: requests.Session,
    url: str,
) -> str:

    response = session.get(
        url,
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()

    if response.apparent_encoding:

        response.encoding = (
            response.apparent_encoding
        )

    return response.text


# ============================================================
# DB Skill + JobRole 기반 필터 용어
# ============================================================

def load_filter_terms(
) -> list[str]:

    sql = text(
        """
        SELECT term
        FROM (
            SELECT name AS term
            FROM skill
            WHERE is_active = TRUE

            UNION

            SELECT alias AS term
            FROM skill_alias

            UNION

            SELECT name AS term
            FROM job_role
            WHERE is_active = TRUE
        ) t
        """
    )

    terms = set(
        TECH_SEED_TERMS
    )

    with engine.connect() as conn:

        rows = conn.execute(
            sql
        ).scalars().all()

    for row in rows:

        term = clean_text(
            row
        )

        if not term:
            continue

        normalized = (
            term.lower()
        )

        # C, R 같이 너무 짧은 일반 문자는 제외
        if (
            len(normalized) < 3
            and normalized
            not in SHORT_ALLOWED
        ):
            continue

        terms.add(
            term
        )

    return sorted(
        terms,

        key=lambda x:
            (
                -len(x),
                x.lower(),
            ),
    )


# ============================================================
# 검색어 포함 여부
# ============================================================

def contains_term(
    text_value: str,
    term: str,
) -> bool:

    if not text_value:
        return False

    text_lower = (
        text_value.lower()
    )

    term_lower = (
        term.lower()
    )

    # 영문/숫자로만 이루어진 경우
    # 단어 경계 검사
    if re.fullmatch(
        r"[a-z0-9]+",
        term_lower,
    ):

        pattern = (
            r"(?<![a-z0-9])"
            + re.escape(
                term_lower
            )
            + r"(?![a-z0-9])"
        )

        return (
            re.search(
                pattern,
                text_lower,
            )
            is not None
        )

    return (
        term_lower
        in text_lower
    )


def find_hits(
    text_value: str,
    terms: list[str],
) -> list[str]:

    hits = []

    for term in terms:

        if contains_term(
            text_value,
            term,
        ):

            hits.append(
                term
            )

    return hits


# ============================================================
# 날짜
# ============================================================

def parse_iso_date(
    value: str,
) -> date | None:

    match = re.search(
        r"(20\d{2})-(\d{2})-(\d{2})",
        value,
    )

    if not match:
        return None

    try:

        return date(
            int(
                match.group(1)
            ),
            int(
                match.group(2)
            ),
            int(
                match.group(3)
            ),
        )

    except ValueError:

        return None


def to_datetime(
    value: date | None,
    *,
    end_of_day: bool = False,
) -> datetime | None:

    if value is None:
        return None

    target_time = (
        dt_time(
            23,
            59,
            59,
        )
        if end_of_day
        else dt_time(
            0,
            0,
            0,
        )
    )

    return datetime.combine(
        value,
        target_time,
        tzinfo=SEOUL,
    )


# ============================================================
# 제목에서 마감일 추출
# ============================================================

def parse_deadline(
    title: str,
    published_date: date | None,
) -> datetime | None:

    if not title:
        return None

    if (
        "채용시" in title
        or "상시" in title
    ):
        return None

    base_year = (
        published_date.year
        if published_date
        else datetime.now(
            SEOUL
        ).year
    )


    # ----------------------------------------
    # ~2026-11-20 / ~2026.11.20
    # ----------------------------------------

    match = re.search(
        (
            r"[~～]"
            r"\s*"
            r"(20\d{2})"
            r"[.\-/]"
            r"(\d{1,2})"
            r"[.\-/]"
            r"(\d{1,2})"
        ),
        title,
    )

    if match:

        try:

            d = date(
                int(
                    match.group(1)
                ),
                int(
                    match.group(2)
                ),
                int(
                    match.group(3)
                ),
            )

            return to_datetime(
                d,
                end_of_day=True,
            )

        except ValueError:
            pass


    # ----------------------------------------
    # 6/22~26
    # ----------------------------------------

    match = re.search(
        (
            r"(\d{1,2})"
            r"[./]"
            r"(\d{1,2})"
            r"\s*[~～]\s*"
            r"(\d{1,2})"
        ),
        title,
    )

    if match:

        try:

            d = date(
                base_year,

                int(
                    match.group(1)
                ),

                int(
                    match.group(3)
                ),
            )

            return to_datetime(
                d,
                end_of_day=True,
            )

        except ValueError:
            pass


    # ----------------------------------------
    # ~11/20 / (~10.13)
    # ----------------------------------------

    match = re.search(
        (
            r"[~～]"
            r"\s*"
            r"(\d{1,2})"
            r"[./]"
            r"(\d{1,2})"
        ),
        title,
    )

    if match:

        try:

            d = date(
                base_year,

                int(
                    match.group(1)
                ),

                int(
                    match.group(2)
                ),
            )

            return to_datetime(
                d,
                end_of_day=True,
            )

        except ValueError:
            pass

    return None


# ============================================================
# 회사/기관명 추출
# ============================================================

def parse_organization(
    title: str,
) -> str | None:

    match = re.match(
        r"^\s*\[([^\]]+)\]",
        title,
    )

    if not match:
        return None

    organization = clean_text(
        match.group(1)
    )

    return (
        organization
        or None
    )


# ============================================================
# 목록 페이지 파싱
# ============================================================

def parse_list_page(
    html: str,
    page: int,
) -> list[JobCandidate]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    results: list[
        JobCandidate
    ] = []

    seen: set[str] = set()


    for anchor in soup.find_all(
        "a",
        href=True,
    ):

        href = clean_text(
            anchor.get(
                "href"
            )
        )

        if (
            "selectBoardArticle.do"
            not in href
        ):
            continue

        full_url = urljoin(
            BASE_URL,
            href,
        )

        parsed = urlparse(
            full_url
        )

        query = parse_qs(
            parsed.query
        )

        ntt_values = (
            query.get(
                "nttId"
            )
        )

        if not ntt_values:
            continue

        ntt_id = clean_text(
            ntt_values[0]
        )

        if (
            not ntt_id
            or ntt_id in seen
        ):
            continue

        title = clean_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        )

        if not title:
            continue

        row = anchor.find_parent(
            "tr"
        )

        row_text = (
            clean_text(
                row.get_text(
                    " ",
                    strip=True,
                )
            )
            if row
            else ""
        )

        published_date = (
            parse_iso_date(
                row_text
            )
        )

        seen.add(
            ntt_id
        )

        results.append(
            JobCandidate(
                ntt_id=(
                    ntt_id
                ),

                title=(
                    title
                ),

                detail_url=(
                    full_url
                ),

                published_date=(
                    published_date
                ),

                list_page=(
                    page
                ),
            )
        )

    return results


# ============================================================
# 상세 페이지 본문 추출
# ============================================================

def extract_article_content(
    html: str,
    title: str,
) -> str:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    # 불필요 영역 제거
    for tag in soup(
        [
            "script",
            "style",
            "svg",
            "noscript",
            "nav",
            "footer",
        ]
    ):

        tag.decompose()


    selectors = [
        ".board_view",
        ".boardView",
        ".bbs_view",
        ".bbsView",
        ".view_cont",
        ".view-content",
        ".viewContent",
        ".board-content",
        ".boardContent",
        ".bbsContent",
        ".view",
        "article",
    ]


    candidates: list[
        str
    ] = []


    for selector in selectors:

        for node in soup.select(
            selector
        ):

            text_value = (
                clean_multiline(
                    node.get_text(
                        "\n",
                        strip=True,
                    )
                )
            )

            if len(
                text_value
            ) >= 80:

                candidates.append(
                    text_value
                )


    # 명확한 view 영역을 못 찾은 경우
    if not candidates:

        fallback = (
            soup.select_one(
                "#content"
            )
            or
            soup.select_one(
                "#contents"
            )
            or
            soup.select_one(
                "main"
            )
            or
            soup.body
        )

        if fallback:

            candidates.append(
                clean_multiline(
                    fallback.get_text(
                        "\n",
                        strip=True,
                    )
                )
            )


    if not candidates:
        return ""


    # 너무 작은 후보보다는
    # 내용이 가장 많은 후보 사용
    content = max(
        candidates,
        key=len,
    )


    # 반복적인 UI 문구 제거
    blocked_lines = {
        "본문 바로가기",
        "대메뉴 바로가기",
        "사이트맵",
        "개인정보처리방침",
        "TOP",
        "목록",
        "이전글",
        "다음글",
    }


    cleaned_lines = []


    for line in content.splitlines():

        line = clean_text(
            line
        )

        if not line:
            continue

        if line in blocked_lines:
            continue

        cleaned_lines.append(
            line
        )


    content = "\n".join(
        cleaned_lines
    )


    # 제목이 본문에 없다면
    # 팀 모델 evidence용으로 앞에 붙임
    if (
        title
        and title not in content
    ):

        content = (
            title
            + "\n"
            + content
        )


    return content[
        :MAX_CONTENT_LENGTH
    ]


# ============================================================
# 섹션 추출
# ============================================================

RESPONSIBILITY_HEADINGS = {
    "담당업무",
    "주요업무",
    "수행업무",
    "업무내용",
    "직무내용",
    "담당 직무",
}

REQUIREMENT_HEADINGS = {
    "자격요건",
    "지원자격",
    "필수요건",
    "필수조건",
    "지원 요건",
    "응시자격",
}

PREFERENCE_HEADINGS = {
    "우대사항",
    "우대조건",
    "우대 요건",
    "우대자격",
}


ALL_SECTION_HEADINGS = (
    RESPONSIBILITY_HEADINGS
    | REQUIREMENT_HEADINGS
    | PREFERENCE_HEADINGS
)


def extract_section(
    content: str,
    headings: set[str],
) -> str | None:

    lines = [
        clean_text(
            line
        )

        for line in (
            content.splitlines()
        )

        if clean_text(
            line
        )
    ]


    result_lines: list[
        str
    ] = []

    capturing = False


    for line in lines:

        normalized = (
            line
            .replace(
                " ",
                ""
            )
            .replace(
                ":",
                ""
            )
        )


        heading_match = False


        for heading in headings:

            heading_normalized = (
                heading.replace(
                    " ",
                    ""
                )
            )

            if (
                normalized
                == heading_normalized
                or normalized.startswith(
                    heading_normalized
                )
            ):

                heading_match = True
                break


        if heading_match:

            capturing = True

            # "담당업무: Python 개발"처럼
            # 같은 줄에 값이 있을 수도 있음
            if ":" in line:

                value = clean_text(
                    line.split(
                        ":",
                        1,
                    )[1]
                )

                if value:
                    result_lines.append(
                        value
                    )

            continue


        if capturing:

            # 다른 섹션 시작 시 종료
            is_other_heading = False

            for other in (
                ALL_SECTION_HEADINGS
            ):

                other_normalized = (
                    other.replace(
                        " ",
                        ""
                    )
                )

                if (
                    normalized
                    == other_normalized
                    or normalized.startswith(
                        other_normalized
                    )
                ):

                    is_other_heading = True
                    break


            if is_other_heading:
                break


            result_lines.append(
                line
            )


            # 과도한 본문 전체 흡수 방지
            if (
                len(
                    "\n".join(
                        result_lines
                    )
                )
                >= 4000
            ):

                break


    if not result_lines:
        return None


    value = "\n".join(
        result_lines
    ).strip()


    return (
        value
        or None
    )


# ============================================================
# 기타 JOB 필드
# ============================================================

def detect_career_type(
    content: str,
    title: str,
) -> str | None:

    combined = (
        title
        + "\n"
        + content
    )


    has_new = (
        "신입" in combined
    )

    has_career = (
        "경력" in combined
    )


    if (
        has_new
        and has_career
    ):

        return "신입/경력"


    if has_new:
        return "신입"


    if has_career:
        return "경력"


    return None


def detect_employment_type(
    content: str,
    title: str,
) -> str | None:

    combined = (
        title
        + "\n"
        + content
    )


    for value in (
        "정규직",
        "계약직",
        "인턴",
        "인턴십",
        "기간제",
        "프리랜서",
    ):

        if value in combined:
            return value


    return None


def detect_education_requirement(
    content: str,
) -> str | None:

    patterns = [
        r"(?:학력|지원자격)\s*[:：]?\s*([^\n]{1,100})",

        r"(고졸\s*이상)",

        r"(초대졸\s*이상)",

        r"(전문대졸\s*이상)",

        r"(대졸\s*이상)",

        r"(학사\s*이상)",

        r"(석사\s*이상)",

        r"(박사\s*이상)",
    ]


    for pattern in patterns:

        match = re.search(
            pattern,
            content,
            flags=re.IGNORECASE,
        )

        if match:

            return clean_text(
                match.group(1)
            )


    return None


# ============================================================
# 한 JOB 상세 파싱
# ============================================================

def parse_job(
    candidate: JobCandidate,
    html: str,
    filter_terms: list[str],
    all_jobs: bool,
) -> ParsedJob | None:

    content = (
        extract_article_content(
            html,
            candidate.title,
        )
    )


    title_hits = (
        find_hits(
            candidate.title,
            filter_terms,
        )
    )


    content_hits = (
        find_hits(
            content,
            filter_terms,
        )
    )


    # 제목 매치는 강하게,
    # 본문 매치는 보조적으로 반영
    tech_score = (
        3
        * min(
            len(
                title_hits
            ),
            5,
        )

        + min(
            len(
                content_hits
            ),
            8,
        )
    )


    if (
        not all_jobs
        and tech_score < 2
    ):

        return None


    responsibilities = (
        extract_section(
            content,
            RESPONSIBILITY_HEADINGS,
        )
    )


    requirements = (
        extract_section(
            content,
            REQUIREMENT_HEADINGS,
        )
    )


    preferences = (
        extract_section(
            content,
            PREFERENCE_HEADINGS,
        )
    )


    if (
        responsibilities
        or requirements
        or preferences
    ):

        parse_status = (
            "PARSED"
        )


    elif len(
        content
    ) >= 200:

        parse_status = (
            "REVIEW_REQUIRED"
        )


    else:

        parse_status = (
            "UNPARSED"
        )


    return ParsedJob(
        ntt_id=(
            candidate.ntt_id
        ),

        title=(
            candidate.title
        ),

        organization=(
            parse_organization(
                candidate.title
            )
        ),

        detail_url=(
            candidate.detail_url
        ),

        published_at=(
            to_datetime(
                candidate
                .published_date
            )
        ),

        deadline=(
            parse_deadline(
                candidate.title,
                candidate.published_date,
            )
        ),

        content=(
            content
        ),

        responsibilities=(
            responsibilities
        ),

        requirements=(
            requirements
        ),

        preferences=(
            preferences
        ),

        career_type=(
            detect_career_type(
                content,
                candidate.title,
            )
        ),

        education_requirement=(
            detect_education_requirement(
                content
            )
        ),

        employment_type=(
            detect_employment_type(
                content,
                candidate.title,
            )
        ),

        parse_status=(
            parse_status
        ),

        tech_score=(
            tech_score
        ),

        title_hits=(
            title_hits
        ),

        content_hits=(
            content_hits
        ),

        list_page=(
            candidate.list_page
        ),
    )


# ============================================================
# 크롤링
# ============================================================

def collect_jobs(
    *,
    start_page: int,
    pages: int,
    max_items: int | None,
    all_jobs: bool,
) -> list[ParsedJob]:

    session = (
        create_session()
    )


    filter_terms = (
        load_filter_terms()
    )


    print(
        (
            f"  필터 용어: "
            f"{len(filter_terms)}개"
        )
    )


    results: list[
        ParsedJob
    ] = []


    seen_ntt_ids: set[
        str
    ] = set()


    end_page = (
        start_page
        + pages
    )


    for page in range(
        start_page,
        end_page,
    ):

        list_url = (
            f"{LIST_URL}"
            f"?pageIndex={page}"
        )


        print(
            (
                f"\n[목록 page {page}]"
            )
        )


        try:

            html = (
                get_html(
                    session,
                    list_url,
                )
            )

        except Exception as exc:

            print(
                (
                    f"  목록 요청 실패: "
                    f"{exc}"
                )
            )

            continue


        candidates = (
            parse_list_page(
                html,
                page,
            )
        )


        print(
            (
                f"  게시물 후보: "
                f"{len(candidates)}개"
            )
        )


        if not candidates:

            print(
                "  게시물 없음 -> 종료"
            )

            break


        for candidate in candidates:

            if (
                candidate.ntt_id
                in seen_ntt_ids
            ):

                continue


            seen_ntt_ids.add(
                candidate.ntt_id
            )


            try:

                detail_html = (
                    get_html(
                        session,
                        candidate.detail_url,
                    )
                )


                parsed = (
                    parse_job(
                        candidate,
                        detail_html,
                        filter_terms,
                        all_jobs,
                    )
                )


            except Exception as exc:

                print(
                    (
                        "  [ERROR] "
                        f"{candidate.title}"
                        f" -> {exc}"
                    )
                )

                continue


            if parsed is None:

                print(
                    (
                        "  [SKIP] "
                        f"{candidate.title}"
                    )
                )

                continue


            results.append(
                parsed
            )


            print(
                (
                    "  [KEEP] "
                    f"score={parsed.tech_score:02d} "
                    f"| {parsed.title}"
                )
            )


            if parsed.title_hits:

                print(
                    (
                        "         title hits: "
                        + ", ".join(
                            parsed.title_hits[
                                :8
                            ]
                        )
                    )
                )


            if (
                max_items is not None
                and len(
                    results
                )
                >= max_items
            ):

                return results


            time.sleep(
                REQUEST_DELAY
            )


    return results


# ============================================================
# DB 저장 SQL
# ============================================================

RESOURCE_EXISTS_SQL = text(
    """
    SELECT resource_id

    FROM resource

    WHERE dedup_key = :dedup_key
    """
)


RESOURCE_INSERT_SQL = text(
    """
    INSERT INTO resource (
        resource_type,
        dedup_key,
        name,
        organization,
        description,
        canonical_url,
        published_at,
        expires_at,
        is_active
    )

    VALUES (
        'JOB',
        :dedup_key,
        :name,
        :organization,
        :description,
        :canonical_url,
        :published_at,
        :expires_at,
        :is_active
    )

    RETURNING resource_id
    """
)


RESOURCE_UPDATE_SQL = text(
    """
    UPDATE resource

    SET
        name =
            :name,

        organization =
            :organization,

        description =
            :description,

        canonical_url =
            :canonical_url,

        published_at =
            COALESCE(
                :published_at,
                published_at
            ),

        expires_at =
            :expires_at,

        is_active =
            :is_active,

        last_seen_at =
            NOW(),

        updated_at =
            NOW()

    WHERE resource_id =
        :resource_id
    """
)


JOB_DETAIL_SQL = text(
    """
    INSERT INTO job_detail (
        resource_id,

        career_type,
        education_requirement,
        employment_type,

        responsibilities,
        requirements,
        preferences,

        deadline,

        parse_status,
        parsed_at
    )

    VALUES (
        :resource_id,

        :career_type,
        :education_requirement,
        :employment_type,

        :responsibilities,
        :requirements,
        :preferences,

        :deadline,

        :parse_status,

        CASE
            WHEN CAST(:parse_status AS VARCHAR(50)) = 'PARSED'
            THEN NOW()
            ELSE NULL
        END
    )

    ON CONFLICT (
        resource_id
    )

    DO UPDATE SET

        career_type =
            EXCLUDED.career_type,

        education_requirement =
            EXCLUDED.education_requirement,

        employment_type =
            EXCLUDED.employment_type,

        responsibilities =
            EXCLUDED.responsibilities,

        requirements =
            EXCLUDED.requirements,

        preferences =
            EXCLUDED.preferences,

        deadline =
            EXCLUDED.deadline,

        parse_status =
            EXCLUDED.parse_status,

        parsed_at =
            EXCLUDED.parsed_at,

        updated_at =
            NOW()
    """
)


SOURCE_DOCUMENT_SQL = text(
    """
    INSERT INTO source_document (
        resource_id,

        source_name,
        source_type,
        document_type,

        source_record_key,

        discovery_channel,

        source_url,
        normalized_url,
        source_host,

        title,
        content,

        raw_metadata,

        published_at,
        expires_at,

        content_hash,

        http_status,
        fetch_status,

        validation_status,
        trust_level,
        evidence_eligible
    )

    VALUES (
        :resource_id,

        :source_name,
        'JOB_BOARD',
        'NOTICE',

        :source_record_key,

        'KONYANG_JOB_BOARD',

        :source_url,
        :normalized_url,
        :source_host,

        :title,
        :content,

        CAST(
            :raw_metadata
            AS jsonb
        ),

        :published_at,
        :expires_at,

        :content_hash,

        200,
        'SUCCESS',

        'ACCEPTED',
        'B',
        :evidence_eligible
    )

    ON CONFLICT (
        source_name,
        source_record_key
    )

    DO UPDATE SET

        resource_id =
            EXCLUDED.resource_id,

        source_url =
            EXCLUDED.source_url,

        normalized_url =
            EXCLUDED.normalized_url,

        source_host =
            EXCLUDED.source_host,

        title =
            EXCLUDED.title,

        content =
            EXCLUDED.content,

        raw_metadata =
            EXCLUDED.raw_metadata,

        published_at =
            EXCLUDED.published_at,

        expires_at =
            EXCLUDED.expires_at,

        content_hash =
            EXCLUDED.content_hash,

        http_status =
            200,

        fetch_status =
            'SUCCESS',

        validation_status =
            'ACCEPTED',

        trust_level =
            'B',

        evidence_eligible =
            EXCLUDED.evidence_eligible,

        fetched_at =
            NOW(),

        updated_at =
            NOW()
    """
)


# ============================================================
# 한 건 저장
# ============================================================

def save_job(
    job: ParsedJob,
) -> str:

    dedup_key = (
        f"job:konyang:"
        f"{job.ntt_id}"
    )


    now = datetime.now(
        SEOUL
    )


    is_active = (
        job.deadline is None
        or job.deadline
        >= now
    )


    description = (
        clean_text(
            job.content
        )[
            :1500
        ]
        or None
    )


    content_hash = (
        hashlib.sha256(
            job.content.encode(
                "utf-8"
            )
        ).hexdigest()
    )


    evidence_eligible = (
        job.parse_status
        == "PARSED"

        and len(
            job.content
        ) >= 200
    )


    metadata = {
        "collector":
            "konyang_jobs",

        "board":
            "건양대학교 입학취업처 채용공고",

        "ntt_id":
            job.ntt_id,

        "list_page":
            job.list_page,

        "tech_score":
            job.tech_score,

        "title_hits":
            job.title_hits,

        "content_hits":
            job.content_hits[
                :30
            ],

        "parse_status":
            job.parse_status,
    }


    with engine.begin() as conn:

        existing_id = (
            conn.execute(
                RESOURCE_EXISTS_SQL,

                {
                    "dedup_key":
                        dedup_key,
                },
            ).scalar()
        )


        params = {
            "dedup_key":
                dedup_key,

            "name":
                job.title,

            "organization":
                job.organization,

            "description":
                description,

            "canonical_url":
                job.detail_url,

            "published_at":
                job.published_at,

            "expires_at":
                job.deadline,

            "is_active":
                is_active,
        }


        if existing_id is None:

            resource_id = (
                conn.execute(
                    RESOURCE_INSERT_SQL,
                    params,
                ).scalar_one()
            )

            action = (
                "CREATED"
            )


        else:

            resource_id = int(
                existing_id
            )


            conn.execute(
                RESOURCE_UPDATE_SQL,

                {
                    **params,

                    "resource_id":
                        resource_id,
                },
            )


            action = (
                "UPDATED"
            )


        conn.execute(
            JOB_DETAIL_SQL,

            {
                "resource_id":
                    resource_id,

                "career_type":
                    job.career_type,

                "education_requirement":
                    job.education_requirement,

                "employment_type":
                    job.employment_type,

                "responsibilities":
                    job.responsibilities,

                "requirements":
                    job.requirements,

                "preferences":
                    job.preferences,

                "deadline":
                    job.deadline,

                "parse_status":
                    job.parse_status,
            },
        )


        conn.execute(
            SOURCE_DOCUMENT_SQL,

            {
                "resource_id":
                    resource_id,

                "source_name":
                    SOURCE_NAME,

                "source_record_key":
                    job.ntt_id,

                "source_url":
                    job.detail_url,

                "normalized_url":
                    job.detail_url,

                "source_host":
                    "job.konyang.ac.kr",

                "title":
                    job.title,

                "content":
                    job.content,

                "raw_metadata":
                    json.dumps(
                        metadata,
                        ensure_ascii=False,
                    ),

                "published_at":
                    job.published_at,

                "expires_at":
                    job.deadline,

                "content_hash":
                    content_hash,

                "evidence_eligible":
                    evidence_eligible,
            },
        )


    return action


# ============================================================
# 여러 건 저장
# ============================================================

def save_jobs(
    jobs: list[
        ParsedJob
    ],
) -> dict:

    result = {
        "created":
            0,

        "updated":
            0,

        "failed":
            0,

        "parsed":
            0,

        "review_required":
            0,

        "unparsed":
            0,
    }


    for index, job in enumerate(
        jobs,
        start=1,
    ):

        try:

            action = (
                save_job(
                    job
                )
            )


            if action == "CREATED":

                result[
                    "created"
                ] += 1

            else:

                result[
                    "updated"
                ] += 1


            status_key = (
                job.parse_status
                .lower()
            )


            if (
                status_key
                in result
            ):

                result[
                    status_key
                ] += 1


            print(
                (
                    f"[{index:03d}/"
                    f"{len(jobs):03d}] "
                    f"{action} "
                    f"| {job.parse_status:<15} "
                    f"| {job.title}"
                )
            )


        except Exception as exc:

            result[
                "failed"
            ] += 1


            print(
                (
                    f"[{index:03d}/"
                    f"{len(jobs):03d}] "
                    f"FAILED "
                    f"| {job.title}"
                    f"\n    {exc}"
                )
            )


    return result


# ============================================================
# DB 통계
# ============================================================

def print_db_stats(
) -> None:

    sql = text(
        """
        SELECT
            COUNT(*) AS total,

            COUNT(*) FILTER (
                WHERE jd.parse_status = 'PARSED'
            ) AS parsed,

            COUNT(*) FILTER (
                WHERE jd.parse_status =
                    'REVIEW_REQUIRED'
            ) AS review_required,

            COUNT(*) FILTER (
                WHERE r.is_active = TRUE
            ) AS active

        FROM resource r

        JOIN job_detail jd
            ON jd.resource_id =
               r.resource_id

        WHERE r.resource_type = 'JOB'
        """
    )


    with engine.connect() as conn:

        row = conn.execute(
            sql
        ).mappings().one()


    print(
        "\n[현재 JOB DB]"
    )

    print(
        (
            f"  전체            : "
            f"{row['total']}개"
        )
    )

    print(
        (
            f"  PARSED          : "
            f"{row['parsed']}개"
        )
    )

    print(
        (
            f"  REVIEW_REQUIRED : "
            f"{row['review_required']}개"
        )
    )

    print(
        (
            f"  active          : "
            f"{row['active']}개"
        )
    )


# ============================================================
# 미리보기
# ============================================================

def print_preview(
    jobs: list[
        ParsedJob
    ],
) -> None:

    print(
        "\n"
        + "=" * 80
    )

    print(
        "수집 결과 미리보기"
    )

    print(
        "=" * 80
    )


    if not jobs:

        print(
            "관련 JOB 없음"
        )

        return


    for index, job in enumerate(
        jobs,
        start=1,
    ):

        print(
            (
                f"\n{index:03d}. "
                f"[score={job.tech_score}] "
                f"{job.title}"
            )
        )


        print(
            (
                f"     organization : "
                f"{job.organization or '-'}"
            )
        )


        print(
            (
                f"     published    : "
                f"{job.published_at or '-'}"
            )
        )


        print(
            (
                f"     deadline     : "
                f"{job.deadline or '-'}"
            )
        )


        print(
            (
                f"     parse_status : "
                f"{job.parse_status}"
            )
        )


        print(
            (
                "     title hits   : "
                f"{', '.join(job.title_hits[:10]) or '-'}"
            )
        )


        print(
            (
                "     body hits    : "
                f"{', '.join(job.content_hits[:10]) or '-'}"
            )
        )


# ============================================================
# 실행
# ============================================================

def run(
    *,
    start_page: int,
    pages: int,
    max_items: int | None,
    all_jobs: bool,
    save_db: bool,
) -> None:

    print(
        "=" * 80
    )

    print(
        (
            "CareerQuest - "
            "건양대학교 JOB 수집"
        )
    )

    print(
        "=" * 80
    )


    print(
        (
            f"시작 페이지 : "
            f"{start_page}"
        )
    )


    print(
        (
            f"탐색 페이지 : "
            f"{pages}"
        )
    )


    print(
        (
            "필터        : "
            f"{'전체 JOB' if all_jobs else 'AI/IT 관련 JOB'}"
        )
    )


    jobs = (
        collect_jobs(
            start_page=(
                start_page
            ),

            pages=(
                pages
            ),

            max_items=(
                max_items
            ),

            all_jobs=(
                all_jobs
            ),
        )
    )


    print(
        "\n"
        + "=" * 80
    )

    print(
        (
            f"최종 수집 JOB: "
            f"{len(jobs)}개"
        )
    )

    print(
        "=" * 80
    )


    print_preview(
        jobs
    )


    if not save_db:

        print(
            "\n[DRY RUN]"
        )

        print(
            (
                "DB를 수정하지 않았습니다. "
                "실제 적재하려면 --save 사용."
            )
        )

        return


    print(
        "\n[PostgreSQL 적재]"
    )


    result = (
        save_jobs(
            jobs
        )
    )


    print(
        "\n"
        + "=" * 80
    )

    print(
        "적재 결과"
    )

    print(
        "=" * 80
    )


    print(
        (
            f"CREATED         : "
            f"{result['created']}"
        )
    )


    print(
        (
            f"UPDATED         : "
            f"{result['updated']}"
        )
    )


    print(
        (
            f"FAILED          : "
            f"{result['failed']}"
        )
    )


    print(
        (
            f"PARSED          : "
            f"{result['parsed']}"
        )
    )


    print(
        (
            f"REVIEW_REQUIRED : "
            f"{result['review_required']}"
        )
    )


    print(
        (
            f"UNPARSED        : "
            f"{result['unparsed']}"
        )
    )


    print_db_stats()


# ============================================================
# CLI
# ============================================================

def parse_args(
) -> argparse.Namespace:

    parser = (
        argparse.ArgumentParser()
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
        "--max-items",

        type=int,

        default=None,
    )


    parser.add_argument(
        "--all-jobs",

        action="store_true",

        help=(
            "AI/IT 필터 없이 "
            "모든 채용공고 수집"
        ),
    )


    parser.add_argument(
        "--save",

        action="store_true",
    )


    return (
        parser.parse_args()
    )


if __name__ == "__main__":

    args = (
        parse_args()
    )


    run(
        start_page=(
            args.start_page
        ),

        pages=(
            args.pages
        ),

        max_items=(
            args.max_items
        ),

        all_jobs=(
            args.all_jobs
        ),

        save_db=(
            args.save
        ),
    )