from __future__ import annotations

import html
import re

from dataclasses import dataclass

from urllib.parse import (
    parse_qs,
    urlencode,
    unquote,
    urljoin,
    urlparse,
    urlunparse,
)

import requests

from bs4 import BeautifulSoup


# ============================================================
# 설정
# ============================================================

HTTP_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0 Safari/537.36"
)

CENTRAL_CURRICULUM_URL = (
    "https://sec.konyang.ac.kr/"
    "prog/curriculum/kor/"
    "sub06_01_04_04/list.do"
)


# ============================================================
# 중앙 FileDown이 실패하는 학과의
# 공식 학과 교육과정 페이지
# ============================================================

DEPARTMENT_CURRICULUM_PAGES: dict[str, str] = {

    "작업치료학과": (
        "https://www.konyang.ac.kr/"
        "prog/curriculum/kor/"
        "sub03_04_01_03/ot/view.do"
    ),

    "안경광학과": (
        "https://www.konyang.ac.kr/"
        "prog/curriculum/kor/"
        "sub03_04_03_03/opt/view.do"
    ),

    "임상병리학과": (
        "https://www.konyang.ac.kr/"
        "prog/curriculum/kor/"
        "sub03_04_04_03/biomed/view.do"
    ),

    "치위생학과": (
        "https://www.konyang.ac.kr/"
        "prog/curriculum/kor/"
        "sub03_04_06_03/dental/view.do"
    ),

    "물리치료학과": (
        "https://www.konyang.ac.kr/"
        "prog/curriculum/kor/"
        "sub03_04_07_03/kypt/view.do"
    ),

    "의료공간디자인학과": (
        "https://www.konyang.ac.kr/"
        "prog/curriculum/kor/"
        "sub03_05_03_03/msdm/view.do"
    ),
}


# ============================================================
# 결과 구조
# ============================================================

@dataclass(frozen=True)
class ResolvedCurriculumPdf:

    data: bytes

    pdf_url: str

    page_url: str

    method: str

    diagnostics: tuple[str, ...]


# ============================================================
# HTTP
# ============================================================

def create_session(
) -> requests.Session:

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent":
                USER_AGENT,

            "Accept":
                (
                    "application/pdf,"
                    "text/html,"
                    "application/xhtml+xml,"
                    "*/*;q=0.8"
                ),

            "Accept-Language":
                "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )

    return session


# ============================================================
# 기본 판별
# ============================================================

def is_pdf_bytes(
    data: bytes,
) -> bool:

    if not data:
        return False

    # BOM/공백이 앞에 붙는 이상한 서버 대응
    head = (
        data[:1024]
        .lstrip()
    )

    return (
        head.startswith(
            b"%PDF"
        )
    )


def short_content_type(
    response: requests.Response,
) -> str:

    return (
        response.headers
        .get(
            "Content-Type",
            "",
        )
        .split(
            ";",
            1,
        )[0]
        .strip()
        .lower()
    )


def response_diagnostic(
    response: requests.Response,
) -> str:

    content_type = (
        short_content_type(
            response
        )
        or "-"
    )

    disposition = (
        response.headers
        .get(
            "Content-Disposition",
            "",
        )
        or "-"
    )

    return (
        f"status={response.status_code}, "
        f"type={content_type}, "
        f"length={len(response.content)}, "
        f"disposition={disposition}"
    )


# ============================================================
# 연도 쿼리
# ============================================================

def curriculum_page_for_year(
    url: str,
    year: int,
) -> str:

    parsed = urlparse(
        url
    )

    query = parse_qs(
        parsed.query,
        keep_blank_values=True,
    )

    # 건양대 curriculum 페이지가
    # 과거 연도를 조회할 때 사용하는 파라미터 구조.
    query[
        "st_solidarity"
    ] = [
        "2020"
    ]

    query[
        "st_year"
    ] = [
        str(
            year
        )
    ]

    query[
        "year"
    ] = [
        str(
            year
        )
    ]

    new_query = urlencode(
        query,
        doseq=True,
    )

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )


# ============================================================
# PDF Viewer / URL 처리
# ============================================================

def unwrap_pdf_viewer_url(
    value: str,
    *,
    base_url: str,
) -> str | None:

    value = html.unescape(
        value
    ).strip()

    if not value:
        return None

    absolute = urljoin(
        base_url,
        value,
    )

    parsed = urlparse(
        absolute
    )

    # 직접 PDF
    if (
        parsed.path
        .lower()
        .endswith(
            ".pdf"
        )
    ):

        return absolute

    # PDF.js:
    # viewer.html?file=/uploads...pdf
    query = parse_qs(
        parsed.query
    )

    for file_value in (
        query.get(
            "file",
            [],
        )
    ):

        decoded = unquote(
            file_value
        )

        if (
            ".pdf"
            in decoded.lower()
        ):

            return urljoin(
                absolute,
                decoded,
            )

    # 문자열 내부에 직접 PDF가 들어있는 경우
    decoded = unquote(
        value
    )

    match = re.search(
        r"""(
            https?://[^\s"'<>]+\.pdf(?:\?[^\s"'<>]*)?
            |
            /[^\s"'<>]+\.pdf(?:\?[^\s"'<>]*)?
        )""",
        decoded,
        flags=(
            re.IGNORECASE
            | re.VERBOSE
        ),
    )

    if match:

        return urljoin(
            base_url,
            match.group(
                1
            ),
        )

    return None


def extract_pdf_candidates(
    page_html: str,
    *,
    page_url: str,
) -> list[str]:

    soup = BeautifulSoup(
        page_html,
        "html.parser",
    )

    candidates: list[str] = []

    # --------------------------------------------------------
    # HTML attribute
    # --------------------------------------------------------

    for tag in soup.find_all(
        True
    ):

        for attribute in (
            "href",
            "src",
            "data",
            "data-url",
            "data-href",
            "onclick",
        ):

            value = tag.get(
                attribute
            )

            if not value:
                continue

            value = str(
                value
            )

            candidate = (
                unwrap_pdf_viewer_url(
                    value,
                    base_url=(
                        page_url
                    ),
                )
            )

            if candidate:

                candidates.append(
                    candidate
                )

            # onclick 안의 따옴표 문자열
            for match in re.finditer(
                r"""['"]([^'"]+)['"]""",
                value,
            ):

                token = (
                    match.group(
                        1
                    )
                )

                candidate = (
                    unwrap_pdf_viewer_url(
                        token,
                        base_url=(
                            page_url
                        ),
                    )
                )

                if candidate:

                    candidates.append(
                        candidate
                    )

    # --------------------------------------------------------
    # raw HTML
    # --------------------------------------------------------

    raw = html.unescape(
        page_html
    )

    # viewer.html?file=...
    for match in re.finditer(
        r"""viewer\.html\?file=([^"'<> \t\r\n]+)""",
        raw,
        flags=(
            re.IGNORECASE
        ),
    ):

        file_value = unquote(
            match.group(
                1
            )
        )

        if (
            ".pdf"
            in file_value.lower()
        ):

            candidates.append(
                urljoin(
                    page_url,
                    file_value,
                )
            )

    # 직접 .pdf
    for match in re.finditer(
        r"""(
            https?://[^\s"'<>]+\.pdf(?:\?[^\s"'<>]*)?
            |
            /[^\s"'<>]+\.pdf(?:\?[^\s"'<>]*)?
        )""",
        raw,
        flags=(
            re.IGNORECASE
            | re.VERBOSE
        ),
    ):

        candidates.append(
            urljoin(
                page_url,
                match.group(
                    1
                ),
            )
        )

    # 순서 보존 중복 제거
    return list(
        dict.fromkeys(
            candidates
        )
    )


# ============================================================
# 응답 HTML 안에서 PDF redirect 찾기
# ============================================================

def pdf_from_non_pdf_response(
    response: requests.Response,
) -> list[str]:

    content_type = (
        short_content_type(
            response
        )
    )

    if (
        "html"
        not in content_type

        and not response.content
        .lstrip()
        .startswith(
            b"<"
        )
    ):

        return []

    try:

        response.encoding = (
            response.apparent_encoding
            or response.encoding
            or "utf-8"
        )

        return (
            extract_pdf_candidates(
                response.text,

                page_url=(
                    response.url
                ),
            )
        )

    except Exception:

        return []


# ============================================================
# 후보 PDF 다운로드
# ============================================================

def try_pdf_url(
    session: requests.Session,
    url: str,
    *,
    referer: str,
    diagnostics: list[str],
    label: str,
) -> tuple[
    bytes,
    str,
] | None:

    try:

        response = session.get(
            url,
            timeout=(
                HTTP_TIMEOUT
            ),
            headers={
                "Referer":
                    referer,
            },
            allow_redirects=True,
        )

        response.raise_for_status()

    except requests.RequestException as exc:

        diagnostics.append(
            (
                f"{label}: "
                f"요청 실패 "
                f"{type(exc).__name__}: "
                f"{exc}"
            )
        )

        return None

    diagnostics.append(
        (
            f"{label}: "
            f"{response.url} | "
            f"{response_diagnostic(response)}"
        )
    )

    if (
        is_pdf_bytes(
            response.content
        )
    ):

        return (
            response.content,
            response.url,
        )

    # 응답이 HTML이고 실제 PDF 주소로
    # 다시 연결하는 구조인지 확인
    nested_candidates = (
        pdf_from_non_pdf_response(
            response
        )
    )

    for (
        index,
        candidate,
    ) in enumerate(
        nested_candidates,
        start=1,
    ):

        try:

            nested = session.get(
                candidate,

                timeout=(
                    HTTP_TIMEOUT
                ),

                headers={
                    "Referer":
                        response.url,
                },

                allow_redirects=True,
            )

            nested.raise_for_status()

        except requests.RequestException as exc:

            diagnostics.append(
                (
                    f"{label}-nested-{index}: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )
            )

            continue

        diagnostics.append(
            (
                f"{label}-nested-{index}: "
                f"{nested.url} | "
                f"{response_diagnostic(nested)}"
            )
        )

        if (
            is_pdf_bytes(
                nested.content
            )
        ):

            return (
                nested.content,
                nested.url,
            )

    return None


# ============================================================
# 학과 페이지 fallback
# ============================================================

def resolve_from_department_page(
    session: requests.Session,
    *,
    department_name: str,
    year: int,
    diagnostics: list[str],
) -> ResolvedCurriculumPdf | None:

    base_page = (
        DEPARTMENT_CURRICULUM_PAGES.get(
            department_name
        )
    )

    if not base_page:

        diagnostics.append(
            (
                "학과 fallback 페이지가 "
                "등록되어 있지 않음"
            )
        )

        return None

    page_url = (
        curriculum_page_for_year(
            base_page,
            year,
        )
    )

    try:

        response = session.get(
            page_url,

            timeout=(
                HTTP_TIMEOUT
            ),

            allow_redirects=True,
        )

        response.raise_for_status()

    except requests.RequestException as exc:

        diagnostics.append(
            (
                "학과 페이지 요청 실패: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )
        )

        return None

    response.encoding = (
        response.apparent_encoding
        or response.encoding
        or "utf-8"
    )

    page_text = (
        BeautifulSoup(
            response.text,
            "html.parser",
        )
        .get_text(
            " ",
            strip=True,
        )
    )

    diagnostics.append(
        (
            "학과 페이지: "
            f"{response.url} | "
            f"{response_diagnostic(response)}"
        )
    )

    # 잘못된 과거 연도를 가져오는 것 방지
    if (
        str(
            year
        )
        not in page_text
    ):

        diagnostics.append(
            (
                f"학과 페이지에서 {year}년 "
                "교육과정 표시를 확인하지 못함"
            )
        )

        return None

    candidates = (
        extract_pdf_candidates(
            response.text,

            page_url=(
                response.url
            ),
        )
    )

    diagnostics.append(
        (
            "학과 페이지 PDF 후보: "
            f"{len(candidates)}개"
        )
    )

    for (
        index,
        candidate,
    ) in enumerate(
        candidates,
        start=1,
    ):

        result = (
            try_pdf_url(
                session,
                candidate,

                referer=(
                    response.url
                ),

                diagnostics=(
                    diagnostics
                ),

                label=(
                    f"학과후보-{index}"
                ),
            )
        )

        if not result:

            continue

        data, resolved_url = (
            result
        )

        return (
            ResolvedCurriculumPdf(
                data=(
                    data
                ),

                pdf_url=(
                    resolved_url
                ),

                page_url=(
                    response.url
                ),

                method=(
                    "DEPARTMENT_PAGE_FALLBACK"
                ),

                diagnostics=tuple(
                    diagnostics
                ),
            )
        )

    return None


# ============================================================
# 최종 Resolver
# ============================================================

def resolve_curriculum_pdf(
    *,
    department_name: str,
    year: int,
    central_url: str,
) -> ResolvedCurriculumPdf:

    """
    1. 중앙 FileDown URL
    2. 중앙 응답 내부 PDF redirect
    3. 학과별 공식 교육과정 페이지
    4. 학과 페이지의 PDF Viewer 실제 PDF

    순서로 시도한다.
    """

    session = (
        create_session()
    )

    diagnostics: list[
        str
    ] = []

    # --------------------------------------------------------
    # 1. 중앙 링크
    # --------------------------------------------------------

    central_result = (
        try_pdf_url(
            session,
            central_url,

            referer=(
                CENTRAL_CURRICULUM_URL
            ),

            diagnostics=(
                diagnostics
            ),

            label=(
                "중앙"
            ),
        )
    )

    if central_result:

        data, resolved_url = (
            central_result
        )

        return (
            ResolvedCurriculumPdf(
                data=(
                    data
                ),

                pdf_url=(
                    resolved_url
                ),

                page_url=(
                    CENTRAL_CURRICULUM_URL
                ),

                method=(
                    "CENTRAL_DIRECT"
                ),

                diagnostics=tuple(
                    diagnostics
                ),
            )
        )

    # --------------------------------------------------------
    # 2. 학과 페이지 fallback
    # --------------------------------------------------------

    fallback = (
        resolve_from_department_page(
            session,

            department_name=(
                department_name
            ),

            year=(
                year
            ),

            diagnostics=(
                diagnostics
            ),
        )
    )

    if fallback:

        return fallback

    # --------------------------------------------------------
    # 최종 실패
    # --------------------------------------------------------

    diagnostic_text = (
        "\n".join(
            (
                f"  - {item}"
                for item
                in diagnostics
            )
        )
    )

    raise RuntimeError(
        (
            f"{department_name} {year} "
            "교육과정 PDF를 복구하지 못했습니다.\n"
            f"{diagnostic_text}"
        )
    )