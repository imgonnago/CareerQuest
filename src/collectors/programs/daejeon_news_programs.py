from __future__ import annotations

import argparse
import hashlib
import json
import re
import time

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from sqlalchemy import text
from urllib3.util.retry import Retry

from src.db.database import engine


# ============================================================
# 기본 설정
# ============================================================

SEOUL = ZoneInfo("Asia/Seoul")

BASE_URL = "https://www.daejeonyouthportal.kr"

LIST_URL = (
    BASE_URL
    + "/board/BBSMSTR_000000000251/articleList.do"
)

DETAIL_URL = (
    BASE_URL
    + "/board/BBSMSTR_000000000251/articleView.do"
)

HTTP_TIMEOUT = 30
REQUEST_DELAY = 0.15

MAX_CONTENT_LENGTH = 30000

# 한 카드에서 searchSeq 후보를 너무 많이 검사하지 않도록 제한
MAX_SEQ_CANDIDATES = 20


# ============================================================
# CareerQuest 관련 기술 키워드
# ============================================================

TECH_TERMS = {
    "AI",
    "인공지능",
    "AX",

    "IT",
    "ICT",
    "SW",
    "소프트웨어",

    "Python",
    "Java",
    "SQL",

    "개발",
    "개발자",
    "프로그래밍",
    "코딩",

    "데이터",
    "빅데이터",
    "데이터분석",
    "데이터 분석",

    "머신러닝",
    "딥러닝",

    "LLM",

    "Agent",
    "에이전트",

    "클라우드",

    "정보보호",
    "정보보안",
    "보안",

    "네트워크",
    "인프라",

    "API",

    "Unity",
    "게임개발",
    "게임 개발",

    "디지털",
}


# ============================================================
# 취업 / 직무 교육 키워드
# ============================================================

CAREER_TERMS = {
    "취업",
    "취업과정",
    "취업훈련",

    "직무",
    "직무교육",
    "직무 개발",
    "직무개발",

    "부트캠프",

    "아카데미",

    "인재양성",

    "교육생",
    "수강생",
    "훈련생",

    "훈련과정",
    "직업훈련",

    "포트폴리오",

    "현직자",

    "프로젝트",

    "취업연계",
    "취업 연계",

    "커리어",
    "진로",
}


# ============================================================
# 실제 PROGRAM 성격을 나타내는 신호
# ============================================================

PROGRAM_TERMS = {
    "교육",
    "교육과정",

    "훈련",
    "훈련과정",

    "부트캠프",
    "아카데미",

    "교육생",
    "수강생",
    "훈련생",

    "프로그램",

    "프로젝트",

    "과정",

    "모집",
}


# ============================================================
# 명백한 제외 대상
# ============================================================

BLOCK_TERMS = {
    "주택",
    "월세",
    "전세",
    "대출",
    "이자지원",

    "결혼",
    "신혼",

    "금융교육",

    "농촌체험",
    "농촌 체험",

    "봉사단",

    "정책평가단",

    "공론장",

    "장학금",

    "보험료",

    "생활비",

    "주거지원",
}


# ============================================================
# 단순 채용공고는 JOB 수집기로 보내고
# PROGRAM에서는 제외
# ============================================================

JOB_NOTICE_TERMS = {
    "[채용]",

    "채용공고",
    "채용 공고",

    "신입사원 채용",
    "경력직 채용",

    "직원 채용",
    "사원 채용",
}


STATUS_TERMS = (
    "상시",
    "모집중",
    "모집예정",
    "모집마감",
    "접수중",
    "접수마감",
)


# ============================================================
# 데이터 구조
# ============================================================

@dataclass
class NewsCandidate:
    search_seq_candidates: list[str]

    title: str

    card_text: str

    status: str | None

    application_text: str | None

    program_text: str | None

    page: int


@dataclass
class ParsedProgram:
    search_seq: str

    title: str

    detail_url: str

    organization: str | None

    content: str

    target_audience: str | None

    application_start: datetime | None

    application_end: datetime | None

    program_start: datetime | None

    program_end: datetime | None

    location: str | None

    cost_text: str | None

    capacity: int | None

    application_method: str | None

    published_at: datetime | None

    is_active: bool

    score: int

    matched_terms: list[str]

    raw_metadata: dict


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

    result: list[str] = []

    previous = None

    for line in value.splitlines():

        line = clean_text(
            line
        )

        if not line:
            continue

        if line == previous:
            continue

        result.append(
            line
        )

        previous = line

    return "\n".join(
        result
    )


def normalize_title(
    value: str,
) -> str:

    value = (
        clean_text(
            value
        ).lower()
    )

    return re.sub(
        r"[^0-9a-z가-힣]+",
        "",
        value,
    )


# ============================================================
# HTTP
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
    params: dict | None = None,
) -> str:

    response = (
        session.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT,
        )
    )

    response.raise_for_status()

    if response.apparent_encoding:

        response.encoding = (
            response.apparent_encoding
        )

    return response.text


# ============================================================
# 키워드 매칭
# ============================================================

def contains_term(
    value: str,
    term: str,
) -> bool:

    if not value:
        return False

    value_lower = (
        value.lower()
    )

    term_lower = (
        term.lower()
    )

    # 영문/숫자 계열은 단어 경계를 확인
    if re.fullmatch(
        r"[a-z0-9+#.\-]+",
        term_lower,
    ):

        return (
            re.search(
                (
                    r"(?<![a-z0-9])"
                    + re.escape(
                        term_lower
                    )
                    + r"(?![a-z0-9])"
                ),
                value_lower,
            )
            is not None
        )

    return (
        term_lower
        in value_lower
    )


def find_terms(
    value: str,
    terms: set[str],
) -> list[str]:

    return sorted(
        {
            term

            for term
            in terms

            if contains_term(
                value,
                term,
            )
        },

        key=lambda x:
            (
                -len(x),
                x.lower(),
            ),
    )


# ============================================================
# 제목 필터
# ============================================================

def title_block_reason(
    title: str,
) -> str | None:

    for term in BLOCK_TERMS:

        if contains_term(
            title,
            term,
        ):

            return (
                f"BLOCK:{term}"
            )

    for term in JOB_NOTICE_TERMS:

        if contains_term(
            title,
            term,
        ):

            return (
                "JOB_NOTICE"
            )

    return None


def title_allowed(
    title: str,
) -> bool:

    if title_block_reason(
        title
    ):

        return False

    tech_hits = (
        find_terms(
            title,
            TECH_TERMS,
        )
    )

    career_hits = (
        find_terms(
            title,
            CAREER_TERMS,
        )
    )

    program_hits = (
        find_terms(
            title,
            PROGRAM_TERMS,
        )
    )

    # 기술 + 프로그램 신호
    if (
        tech_hits
        and program_hits
    ):

        return True

    # 취업/직무 + 프로그램 신호
    if (
        career_hits
        and program_hits
    ):

        return True

    return False


# ============================================================
# 목록 카드 찾기
# ============================================================

def find_card_from_signal(
    signal_tag: Tag,
) -> Tag | None:

    node: Tag | None = (
        signal_tag
    )

    for _ in range(
        9
    ):

        if node is None:
            return None

        text_value = (
            clean_text(
                node.get_text(
                    " ",
                    strip=True,
                )
            )
        )

        if (
            40
            <= len(
                text_value
            )
            <= 3000

            and "신청기간"
            in text_value

            and "진행일정"
            in text_value
        ):

            return node

        parent = (
            node.parent
        )

        if not isinstance(
            parent,
            Tag,
        ):

            return None

        node = (
            parent
        )

    return None


# ============================================================
# 카드 제목 추출
# ============================================================

def infer_title(
    card: Tag,
) -> str:

    # --------------------------------------------------------
    # 일반적인 제목 태그
    # --------------------------------------------------------

    for selector in (
        "h3",
        "h4",
        "h5",
        "strong",
        ".title",
        ".tit",
        ".subject",
    ):

        node = (
            card.select_one(
                selector
            )
        )

        if not node:
            continue

        value = (
            clean_text(
                node.get_text(
                    " ",
                    strip=True,
                )
            )
        )

        if (
            value
            and len(
                value
            )
            >= 5

            and "신청기간"
            not in value
        ):

            return value


    # --------------------------------------------------------
    # 이미지 ALT
    # --------------------------------------------------------

    image_titles: list[str] = []

    for image in card.find_all(
        "img"
    ):

        alt = (
            clean_text(
                image.get(
                    "alt"
                )
            )
        )

        if (
            alt
            and len(
                alt
            )
            >= 5

            and alt.lower()
            != "image"
        ):

            image_titles.append(
                alt
            )

    if image_titles:

        return max(
            image_titles,
            key=len,
        )


    # --------------------------------------------------------
    # 링크 텍스트
    # --------------------------------------------------------

    anchor_values: list[str] = []

    for anchor in card.find_all(
        "a"
    ):

        value = (
            clean_text(
                anchor.get_text(
                    " ",
                    strip=True,
                )
            )
        )

        if (
            value

            and value not in {
                "상세보기",
                "신청하기",
            }

            and len(
                value
            )
            >= 5
        ):

            anchor_values.append(
                value
            )

    if anchor_values:

        return max(
            anchor_values,
            key=len,
        )


    # --------------------------------------------------------
    # 전체 카드 fallback
    # --------------------------------------------------------

    value = (
        clean_text(
            card.get_text(
                " ",
                strip=True,
            )
        )
    )

    before = re.split(
        (
            r"\s(?:상시|모집중|모집예정|모집마감)\s"
            r"|신청기간"
        ),
        value,
        maxsplit=1,
    )[0]

    before = re.sub(
        r"\bImage\b",
        "",
        before,
        flags=re.IGNORECASE,
    )

    return clean_text(
        before
    )


# ============================================================
# 카드 필드
# ============================================================

def extract_card_field(
    value: str,
    label: str,
    next_labels: tuple[str, ...],
) -> str | None:

    if label not in value:
        return None

    end_pattern = (
        "|".join(
            re.escape(
                x
            )

            for x
            in next_labels
        )
    )

    match = re.search(
        (
            re.escape(
                label
            )
            + r"\s*[:：]?\s*"
            + r"(.+?)"
            + r"(?=(?:"
            + end_pattern
            + r")|$)"
        ),
        value,
    )

    if not match:
        return None

    result = (
        clean_text(
            match.group(1)
        )
    )

    return (
        result
        or None
    )


def extract_status(
    value: str,
) -> str | None:

    for status in (
        STATUS_TERMS
    ):

        if status in value:

            return status

    return None


# ============================================================
# searchSeq 후보 추출
# ============================================================

def valid_seq(
    value: str,
) -> bool:

    if not value:
        return False

    if not value.isdigit():
        return False

    number = int(
        value
    )

    # 2026, 180 같은 연도/메뉴번호 제외
    return (
        10000
        <= number
        <= 99999999
    )


def extract_seq_candidates_from_text(
    value: str,
) -> list[str]:

    if not value:
        return []

    result: list[str] = []


    patterns = [
        # searchSeq=17089
        (
            r"searchSeq"
            r"\s*(?:=|:|%3D)\s*"
            r"['\"]?"
            r"(\d{4,})"
        ),

        # articleView.do ... searchSeq ... 17089
        (
            r"articleView\.do"
            r".{0,500}?"
            r"searchSeq"
            r".{0,40}?"
            r"(\d{4,})"
        ),

        # data-search-seq="17089"
        (
            r"(?:data[-_])?"
            r"search[-_]?seq"
            r"\s*=\s*"
            r"['\"]"
            r"(\d{4,})"
        ),

        # fnView('17089')
        # fn_select('...', '17089')
        (
            r"(?:view|detail|article|board|select)"
            r"[A-Za-z0-9_]*"
            r"\s*\("
            r"[^)]{0,400}?"
            r"['\"]"
            r"(\d{4,})"
            r"['\"]"
        ),
    ]


    for pattern in patterns:

        for match in re.finditer(
            pattern,
            value,
            flags=(
                re.IGNORECASE
                | re.DOTALL
            ),
        ):

            seq = (
                match.group(1)
            )

            if (
                valid_seq(
                    seq
                )

                and seq
                not in result
            ):

                result.append(
                    seq
                )

    return result


def extract_numeric_fallback(
    value: str,
) -> list[str]:

    result: list[str] = []

    for match in re.finditer(
        r"(?<!\d)(\d{5,8})(?!\d)",
        value,
    ):

        seq = (
            match.group(1)
        )

        if (
            valid_seq(
                seq
            )

            and seq
            not in result
        ):

            result.append(
                seq
            )

    return result


def find_seq_candidates(
    *,
    html: str,
    card: Tag,
    title: str,
) -> list[str]:

    result: list[str] = []


    def add(
        values: list[str],
    ) -> None:

        for value in values:

            if (
                valid_seq(
                    value
                )

                and value
                not in result
            ):

                result.append(
                    value
                )

                if (
                    len(
                        result
                    )
                    >= MAX_SEQ_CANDIDATES
                ):

                    return


    # --------------------------------------------------------
    # 1. 카드 자체 HTML
    # --------------------------------------------------------

    add(
        extract_seq_candidates_from_text(
            str(
                card
            )
        )
    )

    if (
        len(
            result
        )
        >= MAX_SEQ_CANDIDATES
    ):

        return result


    # --------------------------------------------------------
    # 2. 카드 내부 + 부모 attribute
    # --------------------------------------------------------

    node: Tag | None = (
        card
    )

    for _ in range(
        6
    ):

        if node is None:
            break

        tags_to_check = [
            node,
            *node.find_all(
                True
            ),
        ]

        for tag in tags_to_check:

            if not isinstance(
                tag,
                Tag,
            ):

                continue

            for key, attr_value in (
                tag.attrs.items()
            ):

                if isinstance(
                    attr_value,
                    list,
                ):

                    attr_value = (
                        " ".join(
                            str(x)
                            for x
                            in attr_value
                        )
                    )

                raw = (
                    f"{key}="
                    f"{attr_value}"
                )

                add(
                    extract_seq_candidates_from_text(
                        raw
                    )
                )

                if (
                    len(
                        result
                    )
                    >= MAX_SEQ_CANDIDATES
                ):

                    return result

        parent = (
            node.parent
        )

        if not isinstance(
            parent,
            Tag,
        ):

            break

        node = (
            parent
        )


    # --------------------------------------------------------
    # 3. 원본 HTML에서 제목 주변 검색
    # --------------------------------------------------------

    raw_title_pos = (
        html.find(
            title
        )
    )

    if raw_title_pos >= 0:

        start = max(
            0,
            raw_title_pos - 8000,
        )

        end = min(
            len(
                html
            ),
            raw_title_pos + 8000,
        )

        window = (
            html[
                start:end
            ]
        )

        add(
            extract_seq_candidates_from_text(
                window
            )
        )

        if (
            len(
                result
            )
            < MAX_SEQ_CANDIDATES
        ):

            add(
                extract_numeric_fallback(
                    window
                )
            )


    # --------------------------------------------------------
    # 4. 페이지 전체에서 explicit searchSeq 탐색
    # --------------------------------------------------------

    if (
        len(
            result
        )
        < MAX_SEQ_CANDIDATES
    ):

        add(
            extract_seq_candidates_from_text(
                html
            )
        )

    return result[
        :MAX_SEQ_CANDIDATES
    ]


# ============================================================
# 목록 페이지 파싱
# ============================================================

def parse_list_page(
    html: str,
    *,
    page: int,
    verbose: bool,
) -> list[
    NewsCandidate
]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    cards: list[Tag] = []

    seen_card_ids: set[int] = set()


    # --------------------------------------------------------
    # "신청기간"을 기준으로 실제 프로그램 카드 역추적
    # --------------------------------------------------------

    for text_node in soup.find_all(
        string=re.compile(
            "신청기간"
        )
    ):

        parent = (
            text_node.parent
        )

        if not isinstance(
            parent,
            Tag,
        ):

            continue

        card = (
            find_card_from_signal(
                parent
            )
        )

        if card is None:
            continue

        identity = id(
            card
        )

        if identity in seen_card_ids:
            continue

        seen_card_ids.add(
            identity
        )

        cards.append(
            card
        )


    print(
        (
            "  감지된 실제 카드: "
            f"{len(cards)}개"
        )
    )


    results: list[
        NewsCandidate
    ] = []

    seen_titles: set[
        str
    ] = set()


    for card in cards:

        card_text = (
            clean_text(
                card.get_text(
                    " ",
                    strip=True,
                )
            )
        )

        title = (
            infer_title(
                card
            )
        )

        if not title:
            continue

        normalized_title = (
            normalize_title(
                title
            )
        )

        if (
            normalized_title
            in seen_titles
        ):

            continue


        # ----------------------------------------------------
        # 명백한 제외
        # ----------------------------------------------------

        reason = (
            title_block_reason(
                title
            )
        )

        if reason:

            if verbose:

                print(
                    (
                        "    [REJECT] "
                        f"{reason} "
                        f"| {title}"
                    )
                )

            continue


        # ----------------------------------------------------
        # 제목 1차 관련성
        # ----------------------------------------------------

        if not title_allowed(
            title
        ):

            if verbose:

                print(
                    (
                        "    [REJECT] "
                        "TITLE_FILTER "
                        f"| {title}"
                    )
                )

            continue


        # ----------------------------------------------------
        # searchSeq 후보 수집
        # ----------------------------------------------------

        search_seq_candidates = (
            find_seq_candidates(
                html=html,
                card=card,
                title=title,
            )
        )


        if not search_seq_candidates:

            if verbose:

                print(
                    (
                        "    [SEQ MISS] "
                        f"{title}"
                    )
                )

            continue


        seen_titles.add(
            normalized_title
        )


        if verbose:

            print(
                (
                    "    [SEQ CANDIDATES] "
                    f"{title} -> "
                    f"{search_seq_candidates}"
                )
            )


        results.append(
            NewsCandidate(
                search_seq_candidates=(
                    search_seq_candidates
                ),

                title=(
                    title
                ),

                card_text=(
                    card_text[
                        :2500
                    ]
                ),

                status=(
                    extract_status(
                        card_text
                    )
                ),

                application_text=(
                    extract_card_field(
                        card_text,
                        "신청기간",
                        (
                            "진행일정",
                            "장소",
                            "대상",
                            "담당기관",
                        ),
                    )
                ),

                program_text=(
                    extract_card_field(
                        card_text,
                        "진행일정",
                        (
                            "장소",
                            "대상",
                            "담당기관",
                        ),
                    )
                ),

                page=(
                    page
                ),
            )
        )

    return results


# ============================================================
# searchSeq를 실제 상세 페이지로 검증
#
# 후보 번호로 실제 상세페이지를 열어본 후
# 상세페이지에 해당 제목이 있는 경우에만 채택.
# ============================================================

def resolve_candidate_detail(
    session: requests.Session,
    candidate: NewsCandidate,
    *,
    verbose: bool,
) -> tuple[
    str,
    str,
    str,
] | None:

    target_title = (
        normalize_title(
            candidate.title
        )
    )


    for seq in (
        candidate
        .search_seq_candidates
    ):

        detail_url = (
            f"{DETAIL_URL}"
            f"?commonMenuNo=180_35"
            f"&searchSeq={seq}"
        )

        try:

            html = (
                get_html(
                    session,
                    detail_url,
                )
            )

        except Exception as exc:

            if verbose:

                print(
                    (
                        "      [SEQ ERROR] "
                        f"{seq} | "
                        f"{exc}"
                    )
                )

            continue


        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        page_text = (
            clean_text(
                soup.get_text(
                    " ",
                    strip=True,
                )
            )
        )

        normalized_page = (
            normalize_title(
                page_text
            )
        )


        if (
            target_title

            and target_title
            in normalized_page
        ):

            if verbose:

                print(
                    (
                        "    [SEQ OK] "
                        f"{seq} "
                        f"| {candidate.title}"
                    )
                )

            return (
                seq,
                detail_url,
                html,
            )


        if verbose:

            print(
                (
                    "      [SEQ NO] "
                    f"{seq}"
                )
            )

    return None


# ============================================================
# 날짜 처리
# ============================================================

def safe_date(
    year: int,
    month: int,
    day: int,
) -> date | None:

    try:

        return date(
            year,
            month,
            day,
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

    selected_time = (
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
        selected_time,
        tzinfo=SEOUL,
    )


def parse_date_range(
    value: str,
) -> tuple[
    datetime | None,
    datetime | None,
]:

    value = (
        clean_text(
            value
        )
    )

    if not value:

        return (
            None,
            None,
        )


    if any(
        token in value

        for token in (
            "상시",
            "연중",
            "선착순",
            "예산 소진",
        )
    ):

        return (
            None,
            None,
        )


    # --------------------------------------------------------
    # YYYY-MM-DD
    # --------------------------------------------------------

    full_matches = re.findall(
        (
            r"(20\d{2})"
            r"[.\-/년]\s*"
            r"(\d{1,2})"
            r"[.\-/월]\s*"
            r"(\d{1,2})"
        ),
        value,
    )


    if full_matches:

        first = (
            safe_date(
                int(
                    full_matches[0][0]
                ),
                int(
                    full_matches[0][1]
                ),
                int(
                    full_matches[0][2]
                ),
            )
        )

        second = None

        if (
            len(
                full_matches
            )
            >= 2
        ):

            second = (
                safe_date(
                    int(
                        full_matches[1][0]
                    ),
                    int(
                        full_matches[1][1]
                    ),
                    int(
                        full_matches[1][2]
                    ),
                )
            )

        return (
            to_datetime(
                first
            ),

            to_datetime(
                second,
                end_of_day=True,
            ),
        )


    # --------------------------------------------------------
    # YY-MM-DD
    # --------------------------------------------------------

    short_matches = re.findall(
        (
            r"(?<!\d)"
            r"(\d{2})"
            r"[.\-/]"
            r"(\d{1,2})"
            r"[.\-/]"
            r"(\d{1,2})"
        ),
        value,
    )


    if short_matches:

        first = (
            safe_date(
                2000
                + int(
                    short_matches[0][0]
                ),
                int(
                    short_matches[0][1]
                ),
                int(
                    short_matches[0][2]
                ),
            )
        )

        second = None

        if (
            len(
                short_matches
            )
            >= 2
        ):

            second = (
                safe_date(
                    2000
                    + int(
                        short_matches[1][0]
                    ),
                    int(
                        short_matches[1][1]
                    ),
                    int(
                        short_matches[1][2]
                    ),
                )
            )

        return (
            to_datetime(
                first
            ),

            to_datetime(
                second,
                end_of_day=True,
            ),
        )

    return (
        None,
        None,
    )


# ============================================================
# 상세 본문 추출
# ============================================================

def extract_detail_content(
    html: str,
    title: str,
) -> str:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )


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


    candidates: list[
        tuple[
            int,
            str,
        ]
    ] = []


    for selector in (
        ".board_view",
        ".boardView",

        ".view_cont",
        ".viewContent",
        ".view-content",

        ".board-content",

        "#content",
        "#contents",

        "main",
        "article",
    ):

        for node in soup.select(
            selector
        ):

            value = (
                clean_multiline(
                    node.get_text(
                        "\n",
                        strip=True,
                    )
                )
            )

            if (
                len(
                    value
                )
                < 100
            ):

                continue

            score = 0

            if (
                normalize_title(
                    title
                )
                in normalize_title(
                    value
                )
            ):

                score += 50

            for signal in (
                "신청기간",
                "진행일정",
                "장소",
                "대상",
                "담당기관",
            ):

                if signal in value:

                    score += 10

            candidates.append(
                (
                    score,
                    value,
                )
            )


    if not candidates:
        return ""


    candidates.sort(
        key=lambda x:
            (
                -x[0],
                len(
                    x[1]
                ),
            )
    )


    content = (
        candidates[
            0
        ][1]
    )

    lines = (
        content.splitlines()
    )

    title_norm = (
        normalize_title(
            title
        )
    )

    start = 0

    for index, line in enumerate(
        lines
    ):

        if (
            title_norm

            and title_norm
            in normalize_title(
                line
            )
        ):

            start = max(
                0,
                index - 2,
            )

            break


    selected = (
        lines[
            start:
        ]
    )

    return (
        "\n".join(
            selected
        )[
            :MAX_CONTENT_LENGTH
        ]
    )


# ============================================================
# 상세 라벨 값 추출
# ============================================================

def extract_labeled(
    content: str,
    labels: list[str],
    *,
    max_chars: int = 1000,
) -> str | None:

    lines = [
        clean_text(
            line
        )

        for line
        in content.splitlines()

        if clean_text(
            line
        )
    ]


    for index, line in enumerate(
        lines
    ):

        for label in labels:

            compact_line = (
                line.replace(
                    " ",
                    ""
                )
            )

            compact_label = (
                label.replace(
                    " ",
                    ""
                )
            )

            if not compact_line.startswith(
                compact_label
            ):

                continue


            remainder = re.sub(
                (
                    r"^\s*"
                    + re.escape(
                        label
                    )
                    + r"\s*[:：]?\s*"
                ),
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()


            if (
                remainder

                and remainder
                != line
            ):

                return remainder[
                    :max_chars
                ]


            if (
                index + 1
                < len(
                    lines
                )
            ):

                next_line = (
                    lines[
                        index + 1
                    ]
                )

                if next_line:

                    return next_line[
                        :max_chars
                    ]

    return None


# ============================================================
# 모집 인원
# ============================================================

def extract_capacity(
    content: str,
) -> int | None:

    patterns = [
        (
            r"모집인원\s*[:：]?"
            r"\s*(?:총\s*)?"
            r"(\d+)\s*명"
        ),

        (
            r"정원\s*[:：]?"
            r"\s*(\d+)\s*명"
        ),

        (
            r"총\s*(\d+)\s*명"
        ),

        (
            r"(\d+)\s*명"
            r"\s*(?:모집|내외)"
        ),
    ]


    for pattern in patterns:

        match = re.search(
            pattern,
            content,
        )

        if match:

            try:

                return int(
                    match.group(1)
                )

            except ValueError:

                pass

    return None


# ============================================================
# 비용
# ============================================================

def extract_cost(
    content: str,
) -> str | None:

    patterns = [
        (
            r"(교육비\s*"
            r"[0-9,]+\s*만?\s*원\s*"
            r"전액\s*국비지원)"
        ),

        r"(교육비\s*전액\s*국비지원)",

        r"(교육비\s*전액\s*무료)",

        r"(교육비\s*무료)",

        r"(전\s*과정\s*무료)",

        r"(자부담금\s*없음)",

        r"(별도\s*자부담금\s*없음)",
    ]


    for pattern in patterns:

        match = re.search(
            pattern,
            content,
            flags=re.IGNORECASE,
        )

        if match:

            return (
                clean_text(
                    match.group(1)
                )
            )

    return None


# ============================================================
# 게시일
# ============================================================

def extract_published_at(
    content: str,
) -> datetime | None:

    patterns = [
        (
            r"작성일\s*[:：]?\s*"
            r"(20\d{2})"
            r"[.\-/]"
            r"(\d{1,2})"
            r"[.\-/]"
            r"(\d{1,2})"
        ),

        (
            r"등록일\s*[:：]?\s*"
            r"(20\d{2})"
            r"[.\-/]"
            r"(\d{1,2})"
            r"[.\-/]"
            r"(\d{1,2})"
        ),
    ]


    for pattern in patterns:

        match = re.search(
            pattern,
            content,
        )

        if match:

            parsed = (
                safe_date(
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
            )

            return (
                to_datetime(
                    parsed
                )
            )

    return None


# ============================================================
# 상세 2차 관련성 검증
# ============================================================

def validate_detail_relevance(
    title: str,
    content: str,
) -> tuple[
    bool,
    int,
    list[str],
]:

    tech_title = (
        find_terms(
            title,
            TECH_TERMS,
        )
    )

    career_title = (
        find_terms(
            title,
            CAREER_TERMS,
        )
    )

    tech_body = (
        find_terms(
            content,
            TECH_TERMS,
        )
    )

    career_body = (
        find_terms(
            content,
            CAREER_TERMS,
        )
    )

    program_hits = (
        find_terms(
            (
                title
                + "\n"
                + content
            ),
            PROGRAM_TERMS,
        )
    )


    if not (
        tech_title
        or career_title
        or tech_body
        or career_body
    ):

        return (
            False,
            0,
            [],
        )


    if not program_hits:

        return (
            False,
            0,
            [],
        )


    score = (
        len(
            tech_title
        )
        * 5

        + len(
            career_title
        )
        * 4

        + min(
            len(
                tech_body
            ),
            10,
        )
        * 2

        + min(
            len(
                career_body
            ),
            8,
        )

        + min(
            len(
                program_hits
            ),
            5,
        )
    )


    terms = sorted(
        set(
            tech_title
            + career_title
            + tech_body
            + career_body
            + program_hits
        )
    )


    return (
        score >= 7,
        score,
        terms,
    )


# ============================================================
# 상세 PROGRAM 파싱
# ============================================================

def parse_program(
    candidate: NewsCandidate,
    search_seq: str,
    detail_url: str,
    html: str,
    *,
    include_closed: bool,
) -> ParsedProgram | None:

    content = (
        extract_detail_content(
            html,
            candidate.title,
        )
    )


    if not content:
        return None


    (
        relevance_ok,
        score,
        hits,
    ) = validate_detail_relevance(
        candidate.title,
        content,
    )


    if not relevance_ok:
        return None


    # --------------------------------------------------------
    # 신청기간
    # --------------------------------------------------------

    application_text = (
        candidate.application_text

        or extract_labeled(
            content,

            [
                "신청기간",
                "모집기간",
                "접수기간",
            ],
        )
    )


    (
        application_start,
        application_end,
    ) = parse_date_range(
        application_text
        or ""
    )


    # --------------------------------------------------------
    # 프로그램 진행기간
    # --------------------------------------------------------

    program_text = (
        candidate.program_text

        or extract_labeled(
            content,

            [
                "진행일정",
                "교육기간",
                "교육일정",
                "운영기간",
                "활동기간",
            ],
        )
    )


    (
        program_start,
        program_end,
    ) = parse_date_range(
        program_text
        or ""
    )


    # --------------------------------------------------------
    # 대상
    # --------------------------------------------------------

    target_audience = (
        extract_labeled(
            content,

            [
                "모집대상",
                "지원대상",
                "지원자격",
                "대상",
            ],

            max_chars=1200,
        )
    )


    # --------------------------------------------------------
    # 장소
    # --------------------------------------------------------

    location = (
        extract_labeled(
            content,

            [
                "교육장소",
                "진행장소",
                "운영장소",
                "장소",
            ],

            max_chars=600,
        )
    )


    # --------------------------------------------------------
    # 기관
    # --------------------------------------------------------

    organization = (
        extract_labeled(
            content,

            [
                "담당기관",
                "주관기관",
                "운영기관",
            ],

            max_chars=400,
        )
    )


    # --------------------------------------------------------
    # 신청방법
    # --------------------------------------------------------

    application_method = (
        extract_labeled(
            content,

            [
                "신청방법",
                "지원방법",
                "접수방법",
            ],

            max_chars=1500,
        )
    )


    capacity = (
        extract_capacity(
            content
        )
    )


    cost_text = (
        extract_cost(
            content
        )
    )


    published_at = (
        extract_published_at(
            content
        )
    )


    # --------------------------------------------------------
    # 활성 여부
    # --------------------------------------------------------

    now = datetime.now(
        SEOUL
    )


    if candidate.status in {
        "모집마감",
        "접수마감",
    }:

        is_active = False


    elif candidate.status in {
        "상시",
        "모집중",
        "모집예정",
        "접수중",
    }:

        is_active = True


    else:

        is_active = (
            application_end
            is None

            or application_end
            >= now
        )


    if (
        not include_closed

        and not is_active
    ):

        return None


    return ParsedProgram(
        search_seq=(
            search_seq
        ),

        title=(
            candidate.title
        ),

        detail_url=(
            detail_url
        ),

        organization=(
            organization
        ),

        content=(
            content
        ),

        target_audience=(
            target_audience
        ),

        application_start=(
            application_start
        ),

        application_end=(
            application_end
        ),

        program_start=(
            program_start
        ),

        program_end=(
            program_end
        ),

        location=(
            location
        ),

        cost_text=(
            cost_text
        ),

        capacity=(
            capacity
        ),

        application_method=(
            application_method
        ),

        published_at=(
            published_at
        ),

        is_active=(
            is_active
        ),

        score=(
            score
        ),

        matched_terms=(
            hits
        ),

        raw_metadata={
            "collector":
                (
                    "daejeon_news_programs_v2"
                ),

            "search_seq":
                search_seq,

            "list_page":
                candidate.page,

            "status":
                candidate.status,

            "application_text":
                application_text,

            "program_text":
                program_text,

            "card_text":
                candidate.card_text,

            "seq_candidates":
                candidate
                .search_seq_candidates,

            "score":
                score,

            "matched_terms":
                hits,
        },
    )


# ============================================================
# 전체 수집
# ============================================================

def collect_programs(
    *,
    pages: int,
    max_items: int | None,
    include_closed: bool,
    verbose: bool,
) -> list[
    ParsedProgram
]:

    session = (
        create_session()
    )

    results: list[
        ParsedProgram
    ] = []

    resolved_seq: set[
        str
    ] = set()


    for page in range(
        1,
        pages + 1,
    ):

        print(
            (
                f"\n[청년소식 page {page}]"
            )
        )


        html = (
            get_html(
                session,
                LIST_URL,

                params={
                    "commonMenuNo":
                        "180_35",

                    "pageIndex":
                        page,
                },
            )
        )


        candidates = (
            parse_list_page(
                html,

                page=(
                    page
                ),

                verbose=(
                    verbose
                ),
            )
        )


        print(
            (
                "  1차 통과 후보: "
                f"{len(candidates)}개"
            )
        )


        for candidate in candidates:

            resolved = (
                resolve_candidate_detail(
                    session,
                    candidate,

                    verbose=(
                        verbose
                    ),
                )
            )


            if resolved is None:

                print(
                    (
                        "    [SEQ RESOLVE FAIL] "
                        f"{candidate.title}"
                    )
                )

                continue


            (
                search_seq,
                detail_url,
                detail_html,
            ) = resolved


            if search_seq in resolved_seq:
                continue


            resolved_seq.add(
                search_seq
            )


            try:

                parsed = (
                    parse_program(
                        candidate,
                        search_seq,
                        detail_url,
                        detail_html,

                        include_closed=(
                            include_closed
                        ),
                    )
                )


            except Exception as exc:

                print(
                    (
                        "    [ERROR] "
                        f"{candidate.title}"
                        f"\n      {exc}"
                    )
                )

                continue


            if parsed is None:

                if verbose:

                    print(
                        (
                            "    [DETAIL REJECT] "
                            f"{candidate.title}"
                        )
                    )

                continue


            results.append(
                parsed
            )


            print(
                (
                    "    [KEEP] "
                    f"seq={search_seq} "
                    f"| score="
                    f"{parsed.score:02d} "
                    f"| {parsed.title}"
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
# DB SQL
# ============================================================

FIND_RESOURCE_SQL = text(
    """
    SELECT
        resource_id

    FROM resource

    WHERE dedup_key =
        :dedup_key
    """
)


INSERT_RESOURCE_SQL = text(
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
        'PROGRAM',
        :dedup_key,

        :name,
        :organization,
        :description,

        :canonical_url,

        :published_at,
        :expires_at,

        :is_active
    )

    RETURNING
        resource_id
    """
)


UPDATE_RESOURCE_SQL = text(
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


UPSERT_PROGRAM_DETAIL_SQL = text(
    """
    INSERT INTO program_detail (
        resource_id,

        target_audience,

        application_start,
        application_end,

        program_start,
        program_end,

        location,
        cost_text,
        capacity,

        application_method,

        raw_metadata
    )

    VALUES (
        :resource_id,

        :target_audience,

        :application_start,
        :application_end,

        :program_start,
        :program_end,

        :location,
        :cost_text,
        :capacity,

        :application_method,

        CAST(
            :raw_metadata
            AS jsonb
        )
    )

    ON CONFLICT (
        resource_id
    )

    DO UPDATE SET

        target_audience =
            EXCLUDED.target_audience,

        application_start =
            EXCLUDED.application_start,

        application_end =
            EXCLUDED.application_end,

        program_start =
            EXCLUDED.program_start,

        program_end =
            EXCLUDED.program_end,

        location =
            EXCLUDED.location,

        cost_text =
            EXCLUDED.cost_text,

        capacity =
            EXCLUDED.capacity,

        application_method =
            EXCLUDED.application_method,

        raw_metadata =
            EXCLUDED.raw_metadata,

        updated_at =
            NOW()
    """
)


UPSERT_SOURCE_DOCUMENT_SQL = text(
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

        'DAEJEON_YOUTH_NEWS',
        'OFFICIAL_WEB',
        'WEB_PAGE',

        :source_record_key,

        'DAEJEON_YOUTH_NEWS_COLLECTOR_V2',

        :source_url,
        :normalized_url,
        'daejeonyouthportal.kr',

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
        'A',
        TRUE
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
            'A',

        evidence_eligible =
            TRUE,

        fetched_at =
            NOW(),

        updated_at =
            NOW()
    """
)


# ============================================================
# 한 PROGRAM 저장
# ============================================================

def save_program(
    program: ParsedProgram,
) -> str:

    dedup_key = (
        "program:daejeon:news:"
        + program.search_seq
    )


    content_hash = (
        hashlib.sha256(
            program
            .content
            .encode(
                "utf-8"
            )
        ).hexdigest()
    )


    description = (
        clean_text(
            program.content
        )[
            :1500
        ]
        or None
    )


    raw_metadata = {
        **program.raw_metadata,

        "fetched_at":
            datetime.now(
                SEOUL
            ).isoformat(),
    }


    with engine.begin() as conn:

        existing_id = (
            conn.execute(
                FIND_RESOURCE_SQL,

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
                program.title,

            "organization":
                program.organization,

            "description":
                description,

            "canonical_url":
                program.detail_url,

            "published_at":
                program.published_at,

            "expires_at":
                program.application_end,

            "is_active":
                program.is_active,
        }


        if existing_id is None:

            resource_id = int(
                conn.execute(
                    INSERT_RESOURCE_SQL,
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
                UPDATE_RESOURCE_SQL,

                {
                    **params,

                    "resource_id":
                        resource_id,
                },
            )

            action = (
                "UPDATED"
            )


        # ----------------------------------------------------
        # ProgramDetail
        # ----------------------------------------------------

        conn.execute(
            UPSERT_PROGRAM_DETAIL_SQL,

            {
                "resource_id":
                    resource_id,

                "target_audience":
                    program.target_audience,

                "application_start":
                    program.application_start,

                "application_end":
                    program.application_end,

                "program_start":
                    program.program_start,

                "program_end":
                    program.program_end,

                "location":
                    program.location,

                "cost_text":
                    program.cost_text,

                "capacity":
                    program.capacity,

                "application_method":
                    program.application_method,

                "raw_metadata":
                    json.dumps(
                        raw_metadata,
                        ensure_ascii=False,
                    ),
            },
        )


        # ----------------------------------------------------
        # SourceDocument
        # ----------------------------------------------------

        conn.execute(
            UPSERT_SOURCE_DOCUMENT_SQL,

            {
                "resource_id":
                    resource_id,

                "source_record_key":
                    (
                        "news:"
                        + program.search_seq
                    ),

                "source_url":
                    program.detail_url,

                "normalized_url":
                    program.detail_url,

                "title":
                    program.title,

                "content":
                    program.content,

                "raw_metadata":
                    json.dumps(
                        raw_metadata,
                        ensure_ascii=False,
                    ),

                "published_at":
                    program.published_at,

                "expires_at":
                    program.application_end,

                "content_hash":
                    content_hash,
            },
        )

    return action


# ============================================================
# 여러 PROGRAM 저장
# ============================================================

def save_programs(
    programs: list[
        ParsedProgram
    ],
) -> dict:

    result = {
        "created":
            0,

        "updated":
            0,

        "failed":
            0,
    }


    for index, program in enumerate(
        programs,
        start=1,
    ):

        try:

            action = (
                save_program(
                    program
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


            print(
                (
                    f"[{index:02d}/"
                    f"{len(programs):02d}] "
                    f"{action:<7} "
                    f"| {program.title}"
                )
            )


        except Exception as exc:

            result[
                "failed"
            ] += 1


            print(
                (
                    f"[{index:02d}/"
                    f"{len(programs):02d}] "
                    "FAILED  "
                    f"| {program.title}"
                    f"\n    {exc}"
                )
            )

    return result


# ============================================================
# 미리보기
# ============================================================

def print_preview(
    programs: list[
        ParsedProgram
    ],
) -> None:

    print(
        "\n"
        + "=" * 80
    )

    print(
        "대전 청년소식 PROGRAM 미리보기"
    )

    print(
        "=" * 80
    )


    if not programs:

        print(
            "CareerQuest 관련 PROGRAM 없음"
        )

        return


    for index, program in enumerate(
        programs,
        start=1,
    ):

        print(
            (
                f"\n{index:02d}. "
                f"[score="
                f"{program.score}] "
                f"{program.title}"
            )
        )


        print(
            (
                f"    searchSeq    : "
                f"{program.search_seq}"
            )
        )


        print(
            (
                f"    organization : "
                f"{program.organization or '-'}"
            )
        )


        print(
            (
                f"    application  : "
                f"{program.application_start or '-'} "
                f"~ "
                f"{program.application_end or '-'}"
            )
        )


        print(
            (
                f"    program      : "
                f"{program.program_start or '-'} "
                f"~ "
                f"{program.program_end or '-'}"
            )
        )


        print(
            (
                f"    target       : "
                f"{program.target_audience or '-'}"
            )
        )


        print(
            (
                f"    location     : "
                f"{program.location or '-'}"
            )
        )


        print(
            (
                f"    cost         : "
                f"{program.cost_text or '-'}"
            )
        )


        print(
            (
                f"    capacity     : "
                f"{program.capacity or '-'}"
            )
        )


        print(
            (
                f"    active       : "
                f"{program.is_active}"
            )
        )


        print(
            (
                "    matched      : "
                + (
                    ", ".join(
                        program
                        .matched_terms[
                            :15
                        ]
                    )
                    or "-"
                )
            )
        )


# ============================================================
# 실행
# ============================================================

def run(
    *,
    pages: int,
    max_items: int | None,
    include_closed: bool,
    save_db: bool,
    verbose: bool,
) -> None:

    print(
        "=" * 80
    )

    print(
        (
            "CareerQuest - "
            "대전 청년소식 PROGRAM 수집 v2"
        )
    )

    print(
        "=" * 80
    )


    print(
        (
            f"탐색 페이지 : "
            f"{pages}"
        )
    )


    print(
        (
            f"종료 포함   : "
            f"{include_closed}"
        )
    )


    print(
        (
            f"상세 출력   : "
            f"{verbose}"
        )
    )


    programs = (
        collect_programs(
            pages=(
                pages
            ),

            max_items=(
                max_items
            ),

            include_closed=(
                include_closed
            ),

            verbose=(
                verbose
            ),
        )
    )


    print(
        "\n"
        + "=" * 80
    )


    print(
        (
            f"최종 PROGRAM: "
            f"{len(programs)}개"
        )
    )


    print(
        "=" * 80
    )


    print_preview(
        programs
    )


    if not save_db:

        print(
            "\n[DRY RUN]"
        )

        print(
            "DB를 수정하지 않았습니다."
        )

        print(
            (
                "searchSeq와 제목이 정상적으로 "
                "매칭된 것을 확인한 뒤 "
                "--save를 사용하세요."
            )
        )

        return


    print(
        "\n[PostgreSQL 적재]"
    )


    result = (
        save_programs(
            programs
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
            f"CREATED : "
            f"{result['created']}"
        )
    )


    print(
        (
            f"UPDATED : "
            f"{result['updated']}"
        )
    )


    print(
        (
            f"FAILED  : "
            f"{result['failed']}"
        )
    )


# ============================================================
# CLI
# ============================================================

def parse_args(
) -> argparse.Namespace:

    parser = (
        argparse.ArgumentParser()
    )


    parser.add_argument(
        "--pages",

        type=int,

        default=3,
    )


    parser.add_argument(
        "--max-items",

        type=int,

        default=None,
    )


    parser.add_argument(
        "--include-closed",

        action="store_true",
    )


    parser.add_argument(
        "--save",

        action="store_true",
    )


    parser.add_argument(
        "--verbose",

        action="store_true",
    )


    return (
        parser.parse_args()
    )


# ============================================================
# main
# ============================================================

if __name__ == "__main__":

    args = (
        parse_args()
    )


    run(
        pages=(
            args.pages
        ),

        max_items=(
            args.max_items
        ),

        include_closed=(
            args.include_closed
        ),

        save_db=(
            args.save
        ),

        verbose=(
            args.verbose
        ),
    )