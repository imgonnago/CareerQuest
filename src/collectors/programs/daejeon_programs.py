from __future__ import annotations

import argparse
import hashlib
import json
import re
import time

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from typing import Any
from urllib.parse import urljoin, urlparse
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

SEOUL = ZoneInfo(
    "Asia/Seoul"
)

BASE_URL = (
    "https://www.daejeonyouthportal.kr"
)

NEWS_LIST_URL = (
    BASE_URL
    + "/board/BBSMSTR_000000000251/"
      "articleList.do"
)

POLICY_LIST_URL = (
    BASE_URL
    + "/biz/businessIntro.do"
)

HTTP_TIMEOUT = 30
REQUEST_DELAY = 0.15

MAX_CONTENT_LENGTH = 30000


# ============================================================
# 정책사업 허용 목록
#
# 정책 목록에는 주거/금융/결혼/복지 등이 섞여 있으므로
# CareerQuest에 실제로 활용 가치가 높은 사업만 허용.
#
# 제목은 normalize_title() 후 비교한다.
# ============================================================

POLICY_ALLOWLIST = {
    "청년도전지원사업",
    "청년인턴지원",
    "청년성장프로젝트",
    "대전형코업청년뉴리더",
    "대전일자리카페꿈터",
    "대전일자리지원센터",
    "ai모의면접시스템",
    "진로멘토링",
    "진로멘토링국내진로탐방",
}


# ============================================================
# 청년소식 1차 필터
# ============================================================

TITLE_TECH_TERMS = {
    "AI",
    "인공지능",
    "AX",
    "ICT",
    "IT",
    "SW",
    "소프트웨어",

    "개발",
    "개발자",

    "데이터",
    "빅데이터",

    "정보보호",
    "보안",
    "네트워크",

    "클라우드",

    "코딩",
    "프로그래밍",

    "Python",
    "Java",
    "SQL",

    "머신러닝",
    "딥러닝",

    "LLM",

    "Agent",
    "에이전트",

    "Unity",
    "게임개발",

    "디지털",

    "K-뉴딜",
    "K디지털",
    "K-Digital",
}


TITLE_CAREER_TERMS = {
    "취업",
    "취·창업",
    "취창업",

    "인턴",
    "인턴십",

    "부트캠프",
    "아카데미",

    "직무교육",
    "직무 개발",
    "직무개발",

    "직업훈련",
    "취업과정",
    "훈련과정",

    "교육생",

    "인재양성",

    "청년도전",
    "청년성장",

    "코업",

    "모의면접",

    "취업역량",

    "포트폴리오",

    "진로멘토링",
    "진로 멘토링",

    "취업컨설팅",
}


# ============================================================
# 상세본문 2차 필터
# ============================================================

CONTENT_TECH_TERMS = (
    TITLE_TECH_TERMS
    | {
        "API",

        "인프라",

        "서버",

        "backend",
        "frontend",

        "백엔드",
        "프론트엔드",

        "데이터분석",
        "데이터 분석",

        "자동화",

        "정보보안",

        "컴퓨터비전",
        "자연어처리",
    }
)


CONTENT_CAREER_TERMS = (
    TITLE_CAREER_TERMS
    | {
        "직무",
        "진로",

        "취업매칭",

        "기업탐방",

        "현장실습",

        "프로젝트",

        "교육과정",

        "훈련",

        "컨설팅",

        "멘토링",

        "커리어",

        "Career Development Plan",
        "CDP",
    }
)


# 프로그램/훈련이라는 성격 자체가 존재하는지 확인
PROGRAM_SIGNAL_TERMS = {
    "교육",
    "과정",
    "프로그램",

    "훈련",

    "부트캠프",
    "아카데미",

    "컨설팅",
    "멘토링",

    "인턴",
    "인턴십",

    "현장실습",

    "프로젝트",

    "모의면접",

    "교육생",
    "수강생",

    "참여자",
    "지원자",

    "모집",
}


# ============================================================
# 명백한 제외 대상
# ============================================================

TITLE_BLOCK_TERMS = {
    "이자지원",
    "이자 지원",

    "주택",
    "임차",
    "전세",
    "월세",
    "대출",

    "신혼",

    "결혼장려",
    "결혼 장려",

    "만남지원",
    "만남 지원",

    "연금보험료",
    "보험료 지원",

    "생활비",

    "주거지원",
    "주거 지원",

    "청년하우스",

    "주택임차보증금",
    "전세자금",

    "식생활",

    "체육대회",
    "봉사활동",

    "뮤지컬",
    "독서모임",

    "공론장",
    "토론회",

    "중독예방",

    "금융지식",

    "공예",
    "한복",
    "뷰티",
    "반려템",

    "결혼",
}


# ============================================================
# 이전 버전에서 실제로 잘못 잡힌 사이트 메뉴
# ============================================================

SITE_PAGE_TITLES = {
    "비전",

    "조직및직원안내",
    "조직및직원",

    "고객센터",

    "개인정보처리방침",

    "진로멘토링신청",

    "대전청년정책네트워크",

    "아카데미",
}


# ============================================================
# 채용공고는 JOB 데이터에 들어가야 하므로
# PROGRAM에서 제외
# ============================================================

JOB_NOTICE_TERMS = {
    "[채용]",

    "채용공고",
    "채용 공고",

    "직원 채용",
    "사원 채용",

    "추가 채용 안내",

    "신입사원 채용",
    "경력직 채용",
}


STATUS_TOKENS = (
    "모집중",
    "모집마감",
    "상시",
    "접수중",
    "접수마감",
)


FOOTER_MARKERS = (
    "개인정보처리방침",

    "대전광역시청",
    "대전청년내일재단",

    "문의전화",

    "패밀리사이트",
    "관련사이트",

    "TOP",
)


# ============================================================
# 데이터 구조
# ============================================================

@dataclass
class ProgramCandidate:

    source_kind: str

    source_record_key: str

    title: str

    detail_url: str

    list_status: (
        str | None
    ) = None

    list_application_text: (
        str | None
    ) = None

    list_program_text: (
        str | None
    ) = None

    card_text: (
        str | None
    ) = None


@dataclass
class ParsedProgram:

    source_kind: str

    source_record_key: str

    title: str

    organization: (
        str | None
    )

    detail_url: str

    content: str

    target_audience: (
        str | None
    )

    application_start: (
        datetime | None
    )

    application_end: (
        datetime | None
    )

    program_start: (
        datetime | None
    )

    program_end: (
        datetime | None
    )

    location: (
        str | None
    )

    cost_text: (
        str | None
    )

    capacity: (
        int | None
    )

    application_method: (
        str | None
    )

    published_at: (
        datetime | None
    )

    is_active: bool

    relevance_score: int

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

    lines: list[str] = []

    previous = None


    for line in (
        value.splitlines()
    ):

        line = clean_text(
            line
        )


        if not line:
            continue


        if (
            line
            == previous
        ):

            continue


        lines.append(
            line
        )


        previous = (
            line
        )


    return "\n".join(
        lines
    )


def normalize_title(
    value: str,
) -> str:

    value = (
        clean_text(
            value
        ).lower()
    )


    value = re.sub(
        r"\s+",
        "",
        value,
    )


    value = re.sub(
        r"[\[\](){}<>《》「」『』·ㆍ:：,._/\\\-]",
        "",
        value,
    )


    return value


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
# 날짜
# ============================================================

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


def parse_date_range(
    value: str,
) -> tuple[
    datetime | None,
    datetime | None,
]:

    value = clean_text(
        value
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
            "예산 소진",
            "선착순",
        )
    ):

        return (
            None,
            None,
        )


    # --------------------------------------------------------
    # 2026-10-20 ~ 2027-01-18
    # --------------------------------------------------------

    full = re.findall(
        (
            r"(20\d{2})"
            r"\s*[.\-/년]\s*"
            r"(\d{1,2})"
            r"\s*[.\-/월]\s*"
            r"(\d{1,2})"
        ),
        value,
    )


    if len(
        full
    ) >= 2:

        d1 = safe_date(
            int(
                full[0][0]
            ),

            int(
                full[0][1]
            ),

            int(
                full[0][2]
            ),
        )


        d2 = safe_date(
            int(
                full[1][0]
            ),

            int(
                full[1][1]
            ),

            int(
                full[1][2]
            ),
        )


        return (
            to_datetime(
                d1
            ),

            to_datetime(
                d2,
                end_of_day=True,
            ),
        )


    if len(
        full
    ) == 1:

        d1 = safe_date(
            int(
                full[0][0]
            ),

            int(
                full[0][1]
            ),

            int(
                full[0][2]
            ),
        )


        return (
            to_datetime(
                d1
            ),
            None,
        )


    # --------------------------------------------------------
    # 26-10-19 ~ 26-11-06
    # --------------------------------------------------------

    short = re.findall(
        (
            r"(?<!\d)"
            r"(\d{2})"
            r"\s*[.\-/]\s*"
            r"(\d{1,2})"
            r"\s*[.\-/]\s*"
            r"(\d{1,2})"
        ),
        value,
    )


    if len(
        short
    ) >= 2:

        d1 = safe_date(
            2000
            + int(
                short[0][0]
            ),

            int(
                short[0][1]
            ),

            int(
                short[0][2]
            ),
        )


        d2 = safe_date(
            2000
            + int(
                short[1][0]
            ),

            int(
                short[1][1]
            ),

            int(
                short[1][2]
            ),
        )


        return (
            to_datetime(
                d1
            ),

            to_datetime(
                d2,
                end_of_day=True,
            ),
        )


    if len(
        short
    ) == 1:

        d1 = safe_date(
            2000
            + int(
                short[0][0]
            ),

            int(
                short[0][1]
            ),

            int(
                short[0][2]
            ),
        )


        return (
            to_datetime(
                d1
            ),
            None,
        )


    return (
        None,
        None,
    )


# ============================================================
# 모집 인원
# ============================================================

def parse_capacity(
    value: str,
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
            value,
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
# 키워드 매칭
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


    # 영문 단어는 경계 검사
    if re.fullmatch(
        r"[a-z0-9+#.\-]+",
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


def matched_terms(
    text_value: str,
    terms: set[str],
) -> list[str]:

    return sorted(
        {
            term

            for term
            in terms

            if contains_term(
                text_value,
                term,
            )
        },

        key=lambda x: (
            -len(
                x
            ),
            x.lower(),
        ),
    )


# ============================================================
# 직접 채용공고 여부
# ============================================================

def is_job_notice(
    title: str,
) -> bool:

    lowered = (
        title.lower()
    )


    if any(
        term.lower()
        in lowered

        for term
        in JOB_NOTICE_TERMS
    ):

        return True


    if (
        title.strip()
        .startswith(
            "[채용]"
        )
    ):

        return True


    return False


# ============================================================
# 제목 차단
# ============================================================

def title_block_reason(
    title: str,
) -> str | None:

    normalized = (
        normalize_title(
            title
        )
    )


    if (
        normalized
        in SITE_PAGE_TITLES
    ):

        return (
            "SITE_PAGE"
        )


    for term in (
        TITLE_BLOCK_TERMS
    ):

        if contains_term(
            title,
            term,
        ):

            return (
                f"BLOCK:{term}"
            )


    if is_job_notice(
        title
    ):

        return (
            "JOB_NOTICE"
        )


    return None


# ============================================================
# 정책사업 허용 여부
# ============================================================

def policy_title_allowed(
    title: str,
) -> bool:

    normalized = (
        normalize_title(
            title
        )
    )


    return (
        normalized
        in POLICY_ALLOWLIST
    )


# ============================================================
# 청년소식 제목 1차 필터
# ============================================================

def news_title_allowed(
    title: str,
) -> bool:

    if title_block_reason(
        title
    ):

        return False


    tech_hits = (
        matched_terms(
            title,
            TITLE_TECH_TERMS,
        )
    )


    career_hits = (
        matched_terms(
            title,
            TITLE_CAREER_TERMS,
        )
    )


    # 기술 관련 제목이면 후보
    if tech_hits:

        return True


    # 일반 취업/커리어는
    # 실제 교육·훈련 성격이 제목에 함께 있어야 함
    if (
        career_hits
        and matched_terms(
            title,
            PROGRAM_SIGNAL_TERMS,
        )
    ):

        return True


    return False


# ============================================================
# 리스트에서 실제 카드 영역 찾기
#
# 사이트 메뉴/푸터 링크는 신청기간/진행일정이 없기 때문에 제거.
# ============================================================

def find_card_parent(
    tag: Tag,
    *,
    require_application: bool = True,
) -> Tag | None:

    node: Tag | None = (
        tag
    )


    for _ in range(
        8
    ):

        parent = (
            node.parent

            if node

            else None
        )


        if not isinstance(
            parent,
            Tag,
        ):

            break


        text_value = (
            clean_text(
                parent.get_text(
                    " ",
                    strip=True,
                )
            )
        )


        # body 전체 같은 너무 큰 영역 제외
        if (
            20
            <= len(
                text_value
            )
            <= 2500
        ):

            has_application = (
                "신청기간"
                in text_value
            )


            has_schedule = (
                "진행일정"
                in text_value
            )


            has_status = any(
                token
                in text_value

                for token
                in STATUS_TOKENS
            )


            if require_application:

                if (
                    has_application
                    and (
                        has_schedule
                        or has_status
                    )
                ):

                    return parent


            else:

                if (
                    has_application
                    or has_schedule
                    or has_status
                ):

                    return parent


        node = (
            parent
        )


    return None


# ============================================================
# 카드 상태
# ============================================================

def extract_status(
    card_text: str,
) -> str | None:

    for token in (
        STATUS_TOKENS
    ):

        if token in card_text:

            return token


    return None


# ============================================================
# 카드 라벨 값
# ============================================================

def extract_card_field(
    card_text: str,
    label: str,
    end_labels: tuple[str, ...],
) -> str | None:

    if label not in card_text:

        return None


    end_pattern = "|".join(
        re.escape(
            x
        )

        for x
        in end_labels
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
        card_text,
    )


    if not match:

        return None


    value = (
        clean_text(
            match.group(1)
        )
    )


    return (
        value
        or None
    )


# ============================================================
# 카드 제목 추출
# ============================================================

def infer_title_from_card(
    card: Tag,
    anchor: Tag,
) -> str:

    anchor_text = (
        clean_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        )
    )


    if (
        anchor_text
        and anchor_text
        not in {
            "상세보기",
            "자세히 보기",
            "Image",
            "이미지",
        }
    ):

        title = re.sub(
            r"\s*상세보기\s*$",
            "",
            anchor_text,
        ).strip()


        if len(
            title
        ) >= 3:

            return title


    # --------------------------------------------------------
    # 카드 내부 제목 태그 탐색
    # --------------------------------------------------------

    for selector in (
        "strong",
        "h3",
        "h4",
        "h5",
        ".title",
        ".tit",
        ".subject",
        "dt",
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


        value = re.sub(
            r"\s*상세보기\s*$",
            "",
            value,
        ).strip()


        if (
            value
            and value
            not in {
                "상세보기",
                "Image",
                "이미지",
            }
            and len(
                value
            ) >= 3
        ):

            return value


    # --------------------------------------------------------
    # 최후 fallback
    # --------------------------------------------------------

    card_text = (
        clean_text(
            card.get_text(
                " ",
                strip=True,
            )
        )
    )


    first = re.split(
        (
            r"상세보기"
            r"|모집중"
            r"|모집마감"
            r"|상시"
            r"|신청기간"
        ),
        card_text,
        maxsplit=1,
    )[0]


    first = re.sub(
        r"\bImage\b",
        "",
        first,
        flags=re.IGNORECASE,
    )


    return clean_text(
        first
    )


# ============================================================
# 청년소식 상세 URL 추출
#
# 직접 href 방식과 JavaScript onclick 방식 모두 지원.
# ============================================================

def candidate_url_from_news_tag(
    tag: Tag,
) -> tuple[
    str,
    str,
] | None:

    href = clean_text(
        tag.get(
            "href"
        )
    )


    onclick = clean_text(
        tag.get(
            "onclick"
        )
    )


    data_url = clean_text(
        tag.get(
            "data-url"
        )
    )


    data_href = clean_text(
        tag.get(
            "data-href"
        )
    )


    raw_values = [
        href,
        onclick,
        data_url,
        data_href,
    ]


    for raw in raw_values:

        if not raw:

            continue


        # searchSeq가 직접 들어간 경우
        match = re.search(
            r"searchSeq\s*=\s*(\d+)",
            raw,
        )


        if match:

            seq = (
                match.group(1)
            )


            url = (
                BASE_URL
                + "/board/"
                  "BBSMSTR_000000000251/"
                  "articleView.do"
                + "?commonMenuNo=180_35"
                + f"&searchSeq={seq}"
            )


            return (
                seq,
                url,
            )


        # onclick JavaScript:
        # fnView('17089')
        # fn_select('17089')
        # 등 여러 변형 지원
        if (
            raw
            == onclick
            and onclick
        ):

            match = re.search(
                r"['\"](\d{4,})['\"]",
                raw,
            )


            if match:

                seq = (
                    match.group(1)
                )


                url = (
                    BASE_URL
                    + "/board/"
                      "BBSMSTR_000000000251/"
                      "articleView.do"
                    + "?commonMenuNo=180_35"
                    + f"&searchSeq={seq}"
                )


                return (
                    seq,
                    url,
                )


    return None


# ============================================================
# 청년소식 목록
# ============================================================

def parse_news_list(
    html: str,
    *,
    verbose: bool = False,
) -> list[
    ProgramCandidate
]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )


    results: list[
        ProgramCandidate
    ] = []


    seen: set[str] = set()


    for tag in soup.find_all(
        [
            "a",
            "button",
        ]
    ):

        if not isinstance(
            tag,
            Tag,
        ):

            continue


        found = (
            candidate_url_from_news_tag(
                tag
            )
        )


        if not found:

            continue


        seq, detail_url = (
            found
        )


        if seq in seen:

            continue


        card = (
            find_card_parent(
                tag,
                require_application=False,
            )
        )


        if card is None:

            continue


        card_text = (
            clean_text(
                card.get_text(
                    " ",
                    strip=True,
                )
            )
        )


        title = (
            infer_title_from_card(
                card,
                tag,
            )
        )


        if not title:

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
                        "    [TITLE REJECT] "
                        f"{reason} "
                        f"| {title}"
                    )
                )


            continue


        # ----------------------------------------------------
        # 제목 1차 필터
        # ----------------------------------------------------

        if not news_title_allowed(
            title
        ):

            if verbose:

                print(
                    (
                        "    [TITLE REJECT] "
                        "NO_SIGNAL "
                        f"| {title}"
                    )
                )


            continue


        seen.add(
            seq
        )


        results.append(
            ProgramCandidate(
                source_kind=(
                    "YOUTH_NEWS"
                ),

                source_record_key=(
                    f"news:{seq}"
                ),

                title=(
                    title
                ),

                detail_url=(
                    detail_url
                ),

                list_status=(
                    extract_status(
                        card_text
                    )
                ),

                list_application_text=(
                    extract_card_field(
                        card_text,

                        "신청기간",

                        (
                            "진행일정",
                            "장소",
                            "대상",
                            "담당기관",
                            "상세보기",
                        ),
                    )
                ),

                list_program_text=(
                    extract_card_field(
                        card_text,

                        "진행일정",

                        (
                            "장소",
                            "대상",
                            "담당기관",
                            "상세보기",
                        ),
                    )
                ),

                card_text=(
                    card_text[
                        :2000
                    ]
                ),
            )
        )


    return results


# ============================================================
# 정책사업 목록
#
# 핵심:
# /content/... 링크를 모두 잡지 않고
# "신청기간 + 진행일정/모집상태"가 있는 실제 카드만 허용.
#
# 그 뒤 CareerQuest 정책 allowlist를 다시 적용.
# ============================================================

def parse_policy_list(
    html: str,
    *,
    verbose: bool = False,
) -> list[
    ProgramCandidate
]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )


    results: list[
        ProgramCandidate
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
            "/content/CT_"
            not in href
            or "cntPage.do"
            not in href
        ):

            continue


        full_url = urljoin(
            BASE_URL,
            href,
        )


        match = re.search(
            (
                r"/content/"
                r"(CT_[A-Za-z0-9_]+)/"
            ),
            urlparse(
                full_url
            ).path,
        )


        if not match:

            continue


        content_id = (
            match.group(1)
        )


        if content_id in seen:

            continue


        # ----------------------------------------------------
        # 실제 정책 카드인지 확인
        # ----------------------------------------------------

        card = (
            find_card_parent(
                anchor,
                require_application=True,
            )
        )


        if card is None:

            # 비전/조직/개인정보처리방침 등의
            # 공통 메뉴 링크는 여기서 제거된다.
            continue


        card_text = (
            clean_text(
                card.get_text(
                    " ",
                    strip=True,
                )
            )
        )


        title = (
            infer_title_from_card(
                card,
                anchor,
            )
        )


        if not title:

            continue


        # ----------------------------------------------------
        # 블랙리스트
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
                        "    [POLICY REJECT] "
                        f"{reason} "
                        f"| {title}"
                    )
                )


            continue


        # ----------------------------------------------------
        # CareerQuest 정책 allowlist
        # ----------------------------------------------------

        if not policy_title_allowed(
            title
        ):

            if verbose:

                print(
                    (
                        "    [POLICY REJECT] "
                        "NOT_ALLOWLIST "
                        f"| {title}"
                    )
                )


            continue


        seen.add(
            content_id
        )


        results.append(
            ProgramCandidate(
                source_kind=(
                    "YOUTH_POLICY"
                ),

                source_record_key=(
                    f"policy:{content_id}"
                ),

                title=(
                    title
                ),

                detail_url=(
                    full_url
                ),

                list_status=(
                    extract_status(
                        card_text
                    )
                ),

                list_application_text=(
                    extract_card_field(
                        card_text,

                        "신청기간",

                        (
                            "진행일정",
                            "장소",
                            "대상",
                            "담당기관",
                            "상세보기",
                        ),
                    )
                ),

                list_program_text=(
                    extract_card_field(
                        card_text,

                        "진행일정",

                        (
                            "장소",
                            "대상",
                            "담당기관",
                            "상세보기",
                        ),
                    )
                ),

                card_text=(
                    card_text[
                        :2000
                    ]
                ),
            )
        )


    return results


# ============================================================
# 상세 페이지에서 제목 근처의 실제 본문만 남김
# ============================================================

def trim_content_around_title(
    content: str,
    title: str,
) -> str:

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


    if not lines:

        return ""


    title_norm = (
        normalize_title(
            title
        )
    )


    candidate_indices: list[
        tuple[
            int,
            int,
        ]
    ] = []


    for index, line in enumerate(
        lines
    ):

        line_norm = (
            normalize_title(
                line
            )
        )


        if (
            title_norm
            and (
                title_norm
                in line_norm

                or line_norm
                in title_norm
            )
        ):

            window = " ".join(
                lines[
                    index:
                    index + 35
                ]
            )


            score = 0


            for token in (
                "신청기간",
                "진행일정",
                "장소",
                "대상",
                "담당기관",
                "지원대상",
                "신청방법",
            ):

                if token in window:

                    score += 1


            candidate_indices.append(
                (
                    score,
                    index,
                )
            )


    if candidate_indices:

        candidate_indices.sort(
            key=lambda x: (
                -x[0],
                x[1],
            )
        )


        start_index = max(
            0,
            candidate_indices[
                0
            ][1]
            - 2,
        )


    else:

        start_index = 0


    selected: list[
        str
    ] = []


    for line in (
        lines[
            start_index:
        ]
    ):

        # footer 시작 시 종료
        if (
            selected
            and any(
                (
                    marker
                    == line
                    or line.startswith(
                        marker
                    )
                )

                for marker
                in FOOTER_MARKERS
            )
        ):

            break


        selected.append(
            line
        )


        if len(
            "\n".join(
                selected
            )
        ) >= MAX_CONTENT_LENGTH:

            break


    return "\n".join(
        selected
    )


# ============================================================
# 상세 페이지 본문 추출
# ============================================================

def extract_page_content(
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


    selectors = [
        ".board_view",
        ".boardView",

        ".view_cont",
        ".viewContent",
        ".view-content",

        ".board-content",

        ".detail",

        ".contents",
        ".content",

        "#content",
        "#contents",

        "main",
        "article",
    ]


    for selector in selectors:

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


            if len(
                value
            ) < 80:

                continue


            score = 0


            if (
                title
                and normalize_title(
                    title
                )
                in normalize_title(
                    value
                )
            ):

                score += 50


            for token in (
                "신청기간",
                "진행일정",
                "장소",
                "대상",
                "담당기관",
            ):

                if token in value:

                    score += 10


            # footer까지 포함된 거대한 영역은 약간 감점
            if (
                "개인정보처리방침"
                in value
            ):

                score -= 5


            candidates.append(
                (
                    score,
                    value,
                )
            )


    if (
        not candidates
        and soup.body
    ):

        candidates.append(
            (
                0,

                clean_multiline(
                    soup.body.get_text(
                        "\n",
                        strip=True,
                    )
                ),
            )
        )


    if not candidates:

        return ""


    # 점수가 높은 실제 콘텐츠 영역 우선
    candidates.sort(
        key=lambda x: (
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


    return (
        trim_content_around_title(
            content,
            title,
        )[
            :MAX_CONTENT_LENGTH
        ]
    )


# ============================================================
# 라벨 값 추출
#
# "신청기간 26-10-01~..."처럼
# colon이 없어도 같은 줄 값을 추출하도록 수정.
# ============================================================

def extract_labeled_value(
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


    normalized_labels = [
        (
            label,
            label.replace(
                " ",
                ""
            ),
        )

        for label
        in labels
    ]


    for index, line in enumerate(
        lines
    ):

        compact = (
            line.replace(
                " ",
                ""
            )
        )


        for (
            label,
            label_compact,
        ) in normalized_labels:

            if not compact.startswith(
                label_compact
            ):

                continue


            # ----------------------------------------------
            # 같은 줄
            # 신청기간 26-10-19~26-11-09
            # ----------------------------------------------

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


            # ----------------------------------------------
            # 다음 줄
            # ----------------------------------------------

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


                if (
                    next_line
                    and not any(
                        next_line
                        .replace(
                            " ",
                            ""
                        )
                        .startswith(
                            other_compact
                        )

                        for (
                            _,
                            other_compact,
                        )
                        in normalized_labels
                    )
                ):

                    return next_line[
                        :max_chars
                    ]


    return None


# ============================================================
# 담당기관
# ============================================================

def extract_organization(
    content: str,
) -> str | None:

    return (
        extract_labeled_value(
            content,

            [
                "담당기관",
                "운영기관",
                "주관기관",
            ],

            max_chars=300,
        )
    )


# ============================================================
# 작성일
# ============================================================

def extract_published_at(
    content: str,
) -> datetime | None:

    for pattern in (
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
    ):

        match = re.search(
            pattern,
            content,
        )


        if match:

            d = safe_date(
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
                d
            )


    return None


# ============================================================
# 비용 정보
# ============================================================

def extract_cost_text(
    content: str,
) -> str | None:

    patterns = [
        (
            r"(교육비\s*"
            r"[0-9,]+\s*만?\s*원\s*"
            r"전액\s*국비지원)"
        ),

        r"(교육비\s*전액\s*국비지원)",

        r"(전\s*과정\s*교육비\s*무상)",

        r"(전\s*과정\s*무료\s*운영)",

        r"(전\s*과정\s*무료)",

        r"(교육비\s*무료)",

        r"(자비부담금\s*없음)",

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
                )[
                    :300
                ]
            )


    return None


# ============================================================
# 상세본문 2차 관련성 판정
# ============================================================

def second_stage_relevance(
    title: str,
    content: str,
    source_kind: str,
) -> tuple[
    bool,
    int,
    list[str],
    str,
]:

    if title_block_reason(
        title
    ):

        return (
            False,
            0,
            [],
            "TITLE_BLOCK",
        )


    title_tech = (
        matched_terms(
            title,
            TITLE_TECH_TERMS,
        )
    )


    title_career = (
        matched_terms(
            title,
            TITLE_CAREER_TERMS,
        )
    )


    content_tech = (
        matched_terms(
            content,
            CONTENT_TECH_TERMS,
        )
    )


    content_career = (
        matched_terms(
            content,
            CONTENT_CAREER_TERMS,
        )
    )


    program_signals = (
        matched_terms(
            (
                title
                + "\n"
                + content
            ),
            PROGRAM_SIGNAL_TERMS,
        )
    )


    all_hits = sorted(
        set(
            title_tech
            + title_career
            + content_tech
            + content_career
            + program_signals
        )
    )


    # --------------------------------------------------------
    # 정책사업
    #
    # allowlist에 이미 들어온 정책은 통과.
    # --------------------------------------------------------

    if (
        source_kind
        == "YOUTH_POLICY"
    ):

        if not policy_title_allowed(
            title
        ):

            return (
                False,
                0,
                all_hits,
                "POLICY_NOT_ALLOWLIST",
            )


        score = (
            20
            + len(
                content_tech
            ) * 2
            + len(
                content_career
            )
        )


        return (
            True,
            score,
            all_hits,
            "POLICY_ALLOWLIST",
        )


    # --------------------------------------------------------
    # 청년소식
    #
    # 기술/커리어 신호 + 실제 프로그램 신호가 모두 필요
    # --------------------------------------------------------

    has_domain = bool(
        title_tech
        or title_career
        or content_tech
        or content_career
    )


    has_program = bool(
        program_signals
    )


    if not has_domain:

        return (
            False,
            0,
            all_hits,
            "NO_DOMAIN_SIGNAL",
        )


    if not has_program:

        return (
            False,
            0,
            all_hits,
            "NO_PROGRAM_SIGNAL",
        )


    score = (
        len(
            title_tech
        ) * 5

        + len(
            title_career
        ) * 4

        + min(
            len(
                content_tech
            ),
            10,
        ) * 2

        + min(
            len(
                content_career
            ),
            8,
        )

        + min(
            len(
                program_signals
            ),
            6,
        )
    )


    if score < 7:

        return (
            False,
            score,
            all_hits,
            "LOW_RELEVANCE",
        )


    return (
        True,
        score,
        all_hits,
        "PASS",
    )


# ============================================================
# 프로그램 상세 파싱
# ============================================================

def parse_program(
    candidate: ProgramCandidate,
    html: str,
    *,
    include_closed: bool,
    verbose: bool = False,
) -> ParsedProgram | None:

    content = (
        extract_page_content(
            html,
            candidate.title,
        )
    )


    if not content:

        if verbose:

            print(
                (
                    "    [DETAIL REJECT] "
                    "NO_CONTENT "
                    f"| {candidate.title}"
                )
            )


        return None


    (
        keep,
        score,
        hits,
        reason,
    ) = second_stage_relevance(
        candidate.title,
        content,
        candidate.source_kind,
    )


    if not keep:

        if verbose:

            print(
                (
                    "    [DETAIL REJECT] "
                    f"{reason} "
                    f"| {candidate.title}"
                )
            )


        return None


    # ========================================================
    # 신청기간
    #
    # 목록 카드에 있으면 그 값을 우선 신뢰.
    # ========================================================

    application_text = (
        candidate
        .list_application_text

        or extract_labeled_value(
            content,

            [
                "신청기간",
                "모집기간",
                "접수기간",
            ],

            max_chars=500,
        )
    )


    (
        application_start,
        application_end,
    ) = parse_date_range(
        application_text
        or ""
    )


    # ========================================================
    # 프로그램 기간
    # ========================================================

    program_text = (
        candidate
        .list_program_text

        or extract_labeled_value(
            content,

            [
                "진행일정",
                "교육기간",
                "운영기간",
                "활동기간",
                "사업기간",
                "교육일정",
            ],

            max_chars=800,
        )
    )


    (
        program_start,
        program_end,
    ) = parse_date_range(
        program_text
        or ""
    )


    # ========================================================
    # 대상
    # ========================================================

    target_audience = (
        extract_labeled_value(
            content,

            [
                "모집대상",
                "신청대상",
                "지원대상",
                "지원자격",
                "대상",
            ],

            max_chars=1200,
        )
    )


    # ========================================================
    # 장소
    # ========================================================

    location = (
        extract_labeled_value(
            content,

            [
                "교육장소",
                "진행장소",
                "운영장소",
                "공간위치",
                "장소",
            ],

            max_chars=500,
        )
    )


    # ========================================================
    # 신청 방법
    # ========================================================

    application_method = (
        extract_labeled_value(
            content,

            [
                "신청방법",
                "접수방법",
                "지원방법",
            ],

            max_chars=1500,
        )
    )


    organization = (
        extract_organization(
            content
        )
    )


    capacity = (
        parse_capacity(
            content
        )
    )


    cost_text = (
        extract_cost_text(
            content
        )
    )


    published_at = (
        extract_published_at(
            content
        )
    )


    # ========================================================
    # 활성 여부
    # ========================================================

    now = datetime.now(
        SEOUL
    )


    status = clean_text(
        candidate.list_status
    )


    if status in {
        "모집마감",
        "접수마감",
    }:

        is_active = False


    elif status in {
        "모집중",
        "접수중",
        "상시",
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

        if verbose:

            print(
                (
                    "    [DETAIL REJECT] "
                    "CLOSED "
                    f"| {candidate.title}"
                )
            )


        return None


    metadata = {
        "collector":
            "daejeon_programs_v2",

        "source_kind":
            candidate.source_kind,

        "list_status":
            candidate.list_status,

        "list_application_text":
            candidate.list_application_text,

        "list_program_text":
            candidate.list_program_text,

        "relevance_reason":
            reason,

        "relevance_score":
            score,

        "matched_terms":
            hits,

        "card_text":
            candidate.card_text,
    }


    return ParsedProgram(
        source_kind=(
            candidate.source_kind
        ),

        source_record_key=(
            candidate.source_record_key
        ),

        title=(
            candidate.title
        ),

        organization=(
            organization
        ),

        detail_url=(
            candidate.detail_url
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

        relevance_score=(
            score
        ),

        matched_terms=(
            hits
        ),

        raw_metadata=(
            metadata
        ),
    )


# ============================================================
# 목록 후보 수집
# ============================================================

def collect_candidates(
    session: requests.Session,
    *,
    news_pages: int,
    policy_pages: int,
    verbose: bool,
) -> list[
    ProgramCandidate
]:

    result: list[
        ProgramCandidate
    ] = []


    seen_keys: set[
        str
    ] = set()


    # ========================================================
    # 1. 청년소식
    # ========================================================

    for page in range(
        1,
        news_pages + 1,
    ):

        print(
            (
                f"[청년소식 page {page}]"
            )
        )


        html = (
            get_html(
                session,
                NEWS_LIST_URL,

                params={
                    "commonMenuNo":
                        "180_35",

                    "pageIndex":
                        page,
                },
            )
        )


        candidates = (
            parse_news_list(
                html,
                verbose=verbose,
            )
        )


        print(
            (
                "  CareerQuest 제목 후보: "
                f"{len(candidates)}개"
            )
        )


        for candidate in candidates:

            if (
                candidate.source_record_key
                in seen_keys
            ):

                continue


            seen_keys.add(
                candidate
                .source_record_key
            )


            result.append(
                candidate
            )


        time.sleep(
            REQUEST_DELAY
        )


    # ========================================================
    # 2. 정책사업
    # ========================================================

    for page in range(
        1,
        policy_pages + 1,
    ):

        print(
            (
                f"[정책사업 page {page}]"
            )
        )


        html = (
            get_html(
                session,
                POLICY_LIST_URL,

                params={
                    "commonMenuNo":
                        "438_323_324_325",

                    "dpmSectionFst":
                        "100",

                    "pageIndex":
                        page,
                },
            )
        )


        candidates = (
            parse_policy_list(
                html,
                verbose=verbose,
            )
        )


        print(
            (
                "  CareerQuest 정책 후보: "
                f"{len(candidates)}개"
            )
        )


        for candidate in candidates:

            if (
                candidate.source_record_key
                in seen_keys
            ):

                continue


            seen_keys.add(
                candidate
                .source_record_key
            )


            result.append(
                candidate
            )


        time.sleep(
            REQUEST_DELAY
        )


    return result


# ============================================================
# 상세 페이지까지 수집
# ============================================================

def collect_programs(
    *,
    news_pages: int,
    policy_pages: int,
    max_items: int | None,
    include_closed: bool,
    verbose: bool,
) -> list[
    ParsedProgram
]:

    session = (
        create_session()
    )


    candidates = (
        collect_candidates(
            session,

            news_pages=(
                news_pages
            ),

            policy_pages=(
                policy_pages
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
            "1차 필터 통과 상세 후보: "
            f"{len(candidates)}개"
        )
    )


    print(
        "=" * 80
    )


    results: list[
        ParsedProgram
    ] = []


    for index, candidate in enumerate(
        candidates,
        start=1,
    ):

        try:

            html = (
                get_html(
                    session,
                    candidate.detail_url,
                )
            )


            parsed = (
                parse_program(
                    candidate,
                    html,

                    include_closed=(
                        include_closed
                    ),

                    verbose=(
                        verbose
                    ),
                )
            )


        except Exception as exc:

            print(
                (
                    f"[{index:03d}] ERROR "
                    f"| {candidate.title}"
                    f"\n    {exc}"
                )
            )


            continue


        if parsed is None:

            continue


        results.append(
            parsed
        )


        print(
            (
                f"[{index:03d}] KEEP "
                f"| score="
                f"{parsed.relevance_score:02d} "
                f"| "
                f"{parsed.source_kind:<12} "
                f"| "
                f"{parsed.title}"
            )
        )


        if (
            max_items is not None
            and len(
                results
            )
            >= max_items
        ):

            break


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

        'DAEJEON_YOUTH_PORTAL',
        'OFFICIAL_WEB',
        'WEB_PAGE',

        :source_record_key,

        'DAEJEON_PROGRAM_COLLECTOR_V2',

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
        "program:daejeon:"
        + program
        .source_record_key
        .lower()
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


        resource_params = {
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
                    resource_params,
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
                    **resource_params,

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
                    program.source_record_key,

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
# 전체 저장
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
        "PROGRAM 수집 미리보기"
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
                f"{program.relevance_score}] "
                f"{program.title}"
            )
        )


        print(
            (
                f"    source       : "
                f"{program.source_kind}"
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
                f"    capacity     : "
                f"{program.capacity or '-'}"
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
# PROGRAM DB 통계
# ============================================================

def print_db_stats(
) -> None:

    sql = text(
        """
        SELECT
            COUNT(*) AS total,

            COUNT(*) FILTER (
                WHERE r.is_active = TRUE
            ) AS active,

            COUNT(*) FILTER (
                WHERE pd.application_end
                    IS NOT NULL
            ) AS with_deadline,

            COUNT(*) FILTER (
                WHERE pd.target_audience
                    IS NOT NULL
            ) AS with_target,

            COUNT(*) FILTER (
                WHERE sd.evidence_eligible
                    = TRUE
            ) AS evidence_ready

        FROM resource r

        JOIN program_detail pd
            ON pd.resource_id =
               r.resource_id

        LEFT JOIN source_document sd
            ON sd.resource_id =
               r.resource_id

           AND sd.source_name =
               'DAEJEON_YOUTH_PORTAL'

        WHERE r.resource_type =
            'PROGRAM'
        """
    )


    with engine.connect() as conn:

        row = (
            conn.execute(
                sql
            ).mappings().one()
        )


    print(
        "\n[현재 PROGRAM DB]"
    )


    print(
        (
            f"  전체             : "
            f"{row['total']}개"
        )
    )


    print(
        (
            f"  active           : "
            f"{row['active']}개"
        )
    )


    print(
        (
            "  신청 마감일 보유 : "
            f"{row['with_deadline']}개"
        )
    )


    print(
        (
            "  대상 정보 보유   : "
            f"{row['with_target']}개"
        )
    )


    print(
        (
            f"  evidence ready   : "
            f"{row['evidence_ready']}개"
        )
    )


# ============================================================
# 실행
# ============================================================

def run(
    *,
    news_pages: int,
    policy_pages: int,
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
            "대전 지역 PROGRAM 수집 v2"
        )
    )


    print(
        "=" * 80
    )


    print(
        (
            f"청년소식 페이지    : "
            f"{news_pages}"
        )
    )


    print(
        (
            f"정책사업 페이지    : "
            f"{policy_pages}"
        )
    )


    print(
        (
            "종료 프로그램 포함 : "
            f"{include_closed}"
        )
    )


    print(
        (
            "제외 이유 상세 출력: "
            f"{verbose}"
        )
    )


    programs = (
        collect_programs(
            news_pages=(
                news_pages
            ),

            policy_pages=(
                policy_pages
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


    # ========================================================
    # DRY RUN
    # ========================================================

    if not save_db:

        print(
            "\n[DRY RUN]"
        )


        print(
            "DB를 수정하지 않았습니다."
        )


        print(
            (
                "미리보기에 주거/금융/결혼지원/"
                "사이트 메뉴가 없을 때만 "
                "--save를 사용하세요."
            )
        )


        return


    # ========================================================
    # PostgreSQL 저장
    # ========================================================

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
        "PROGRAM 적재 결과"
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
        "--news-pages",

        type=int,

        default=3,

        help=(
            "대전청년포털 청년소식 "
            "탐색 페이지 수"
        ),
    )


    parser.add_argument(
        "--policy-pages",

        type=int,

        default=7,

        help=(
            "대전청년포털 정책사업 "
            "탐색 페이지 수"
        ),
    )


    parser.add_argument(
        "--max-items",

        type=int,

        default=None,
    )


    parser.add_argument(
        "--include-closed",

        action="store_true",

        help=(
            "신청이 종료된 과거 "
            "프로그램도 포함"
        ),
    )


    parser.add_argument(
        "--save",

        action="store_true",
    )


    parser.add_argument(
        "--verbose",

        action="store_true",

        help=(
            "제목/본문 필터에서 "
            "제외된 이유도 출력"
        ),
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
        news_pages=(
            args.news_pages
        ),

        policy_pages=(
            args.policy_pages
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