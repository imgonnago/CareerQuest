from __future__ import annotations

import argparse
import hashlib
import json
import re

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pymupdf
import requests

from bs4 import BeautifulSoup
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

HTTP_TIMEOUT = 30


DATAQ_BROCHURE_URL = (
    "https://www.dataq.or.kr/"
    "www/dataq_brochure_2022.pdf"
)

QNET_INFORMATION_PROCESSING_URL = (
    "https://www.q-net.or.kr/"
    "crf005.do?id=crf00503&jmCd=1320"
)


# ============================================================
# 대상 자격증
# ============================================================

@dataclass
class CertificateTarget:
    qualification_code: str

    name: str

    qualification_type: str

    administering_org: str

    source_name: str

    source_url: str

    source_host: str

    source_kind: str

    markers: list[str]

    aliases: list[str]

    validity_period: (
        str | None
    ) = None


CERTIFICATE_TARGETS = [
    # --------------------------------------------------------
    # DataQ
    # --------------------------------------------------------

    CertificateTarget(
        qualification_code="DATAQ-BAE",

        name="빅데이터분석기사",

        qualification_type=(
            "국가기술자격(기사)"
        ),

        administering_org=(
            "한국데이터산업진흥원"
        ),

        source_name=(
            "DATAQ_CERTIFICATE"
        ),

        source_url=(
            DATAQ_BROCHURE_URL
        ),

        source_host=(
            "dataq.or.kr"
        ),

        source_kind=(
            "DATAQ_PDF"
        ),

        markers=[
            "빅데이터분석기사",
            "Big Data Analysis Engineer",
            "BAE",
        ],

        aliases=[
            "빅데이터 분석 기사",
            "BAE",
        ],
    ),

    CertificateTarget(
        qualification_code="DATAQ-DAP",

        name="데이터아키텍처 전문가",

        qualification_type=(
            "국가공인민간자격"
        ),

        administering_org=(
            "한국데이터산업진흥원"
        ),

        source_name=(
            "DATAQ_CERTIFICATE"
        ),

        source_url=(
            DATAQ_BROCHURE_URL
        ),

        source_host=(
            "dataq.or.kr"
        ),

        source_kind=(
            "DATAQ_PDF"
        ),

        markers=[
            "데이터아키텍처 전문가",
            "Data Architecture Professional",
        ],

        aliases=[
            "DAP",
        ],
    ),

    CertificateTarget(
        qualification_code="DATAQ-DASP",

        name="데이터아키텍처 준전문가",

        qualification_type=(
            "등록민간자격"
        ),

        administering_org=(
            "한국데이터산업진흥원"
        ),

        source_name=(
            "DATAQ_CERTIFICATE"
        ),

        source_url=(
            DATAQ_BROCHURE_URL
        ),

        source_host=(
            "dataq.or.kr"
        ),

        source_kind=(
            "DATAQ_PDF"
        ),

        markers=[
            "데이터아키텍처 준전문가",
            "Data Architecture Semi-Professional",
        ],

        aliases=[
            "DAsP",
            "DASP",
        ],

        validity_period=(
            "영구"
        ),
    ),

    CertificateTarget(
        qualification_code="DATAQ-SQLP",

        name="SQL 전문가",

        qualification_type=(
            "국가공인민간자격"
        ),

        administering_org=(
            "한국데이터산업진흥원"
        ),

        source_name=(
            "DATAQ_CERTIFICATE"
        ),

        source_url=(
            DATAQ_BROCHURE_URL
        ),

        source_host=(
            "dataq.or.kr"
        ),

        source_kind=(
            "DATAQ_PDF"
        ),

        markers=[
            "SQL 전문가",
            "Structured Query Language Professional",
        ],

        aliases=[
            "SQLP",
        ],

        validity_period=(
            "2년 "
            "(보수교육 이수 후 영구자격 전환)"
        ),
    ),

    CertificateTarget(
        qualification_code="DATAQ-SQLD",

        name="SQL 개발자",

        qualification_type=(
            "국가공인민간자격"
        ),

        administering_org=(
            "한국데이터산업진흥원"
        ),

        source_name=(
            "DATAQ_CERTIFICATE"
        ),

        source_url=(
            DATAQ_BROCHURE_URL
        ),

        source_host=(
            "dataq.or.kr"
        ),

        source_kind=(
            "DATAQ_PDF"
        ),

        markers=[
            "SQL 개발자",
            "Structured Query Language Developer",
        ],

        aliases=[
            "SQLD",
        ],

        validity_period=(
            "2년 "
            "(보수교육 이수 후 영구자격 전환)"
        ),
    ),

    CertificateTarget(
        qualification_code="DATAQ-ADP",

        name="데이터분석 전문가",

        qualification_type=(
            "국가공인민간자격"
        ),

        administering_org=(
            "한국데이터산업진흥원"
        ),

        source_name=(
            "DATAQ_CERTIFICATE"
        ),

        source_url=(
            DATAQ_BROCHURE_URL
        ),

        source_host=(
            "dataq.or.kr"
        ),

        source_kind=(
            "DATAQ_PDF"
        ),

        markers=[
            "데이터분석 전문가",
            "Advanced Data Analytics Professional",
        ],

        aliases=[
            "ADP",
        ],
    ),

    CertificateTarget(
        qualification_code="DATAQ-ADSP",

        name="데이터분석 준전문가",

        qualification_type=(
            "국가공인민간자격"
        ),

        administering_org=(
            "한국데이터산업진흥원"
        ),

        source_name=(
            "DATAQ_CERTIFICATE"
        ),

        source_url=(
            DATAQ_BROCHURE_URL
        ),

        source_host=(
            "dataq.or.kr"
        ),

        source_kind=(
            "DATAQ_PDF"
        ),

        markers=[
            "데이터분석 준전문가",
            "Advanced Data Analytics Semi-Professional",
        ],

        aliases=[
            "ADsP",
            "ADSP",
        ],

        validity_period=(
            "영구"
        ),
    ),

    # --------------------------------------------------------
    # Q-Net
    # --------------------------------------------------------

    CertificateTarget(
        qualification_code="QNET-1320",

        name="정보처리기사",

        qualification_type=(
            "국가기술자격(기사)"
        ),

        administering_org=(
            "한국산업인력공단"
        ),

        source_name=(
            "QNET_CERTIFICATE"
        ),

        source_url=(
            QNET_INFORMATION_PROCESSING_URL
        ),

        source_host=(
            "q-net.or.kr"
        ),

        source_kind=(
            "QNET_HTML"
        ),

        markers=[
            "정보처리기사",
        ],

        aliases=[
            "정보처리 기사",
        ],
    ),
]


# ============================================================
# 최종 수집 결과
# ============================================================

@dataclass
class CollectedCertificate:
    target: CertificateTarget

    content: str

    description: str | None

    sections: dict[str, str]

    content_hash: str

    fetch_status: str

    evidence_eligible: bool

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

        # PDF에서 같은 줄이 반복되는 경우 일부 제거
        if line == previous:
            continue

        result.append(
            line
        )

        previous = (
            line
        )

    return "\n".join(
        result
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


# ============================================================
# DataQ PDF 다운로드
# ============================================================

def download_pdf(
    session: requests.Session,
    url: str,
) -> bytes:

    response = session.get(
        url,
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()

    data = (
        response.content
    )

    if not data.startswith(
        b"%PDF"
    ):

        raise RuntimeError(
            (
                "PDF 다운로드 결과가 "
                "정상적인 PDF가 아닙니다.\n"
                f"URL: {url}"
            )
        )

    return data


# ============================================================
# DataQ PDF 페이지 텍스트
# ============================================================

def read_pdf_pages(
    pdf_bytes: bytes,
) -> list[str]:

    document = pymupdf.open(
        stream=pdf_bytes,
        filetype="pdf",
    )

    pages: list[str] = []

    try:

        for page_index in range(
            document.page_count
        ):

            page = document.load_page(
                page_index
            )

            text_value = (
                page.get_text(
                    "text"
                )
            )

            pages.append(
                clean_multiline(
                    text_value
                )
            )

    finally:

        document.close()

    return pages


# ============================================================
# DataQ에서 특정 자격증 관련 페이지 선택
# ============================================================

def extract_dataq_certificate_content(
    target: CertificateTarget,
    pages: list[str],
) -> tuple[
    str,
    list[int],
]:

    scored_pages: list[
        tuple[int, int]
    ] = []


    for index, page_text in enumerate(
        pages
    ):

        score = 0

        page_lower = (
            page_text.lower()
        )


        for marker in (
            target.markers
        ):

            marker_lower = (
                marker.lower()
            )

            if (
                marker_lower
                in page_lower
            ):

                # 이름 전체가 있는 페이지를 강하게 우선
                score += (
                    10
                    if len(marker) >= 6
                    else 3
                )


        # 일반적인 시험 정보가 같이 있으면 가점
        for keyword in (
            "과목 안내",
            "과목안내",
            "합격 기준",
            "합격기준",
            "응시 자격",
            "응시자격",
            "유효 기간",
            "유효기간",
        ):

            if keyword in page_text:

                score += 1


        if score > 0:

            scored_pages.append(
                (
                    score,
                    index,
                )
            )


    if not scored_pages:

        return (
            "",
            [],
        )


    # 점수 높은 페이지 우선
    scored_pages.sort(
        key=lambda item:
            (
                -item[0],
                item[1],
            )
    )


    selected_indices = sorted(
        {
            index

            for _score, index
            in scored_pages[
                :3
            ]
        }
    )


    selected_texts: list[
        str
    ] = []


    for index in selected_indices:

        selected_texts.append(
            (
                f"[공식 안내 PDF "
                f"{index + 1}페이지]\n"
                f"{pages[index]}"
            )
        )


    return (
        "\n\n".join(
            selected_texts
        ),
        [
            index + 1
            for index
            in selected_indices
        ],
    )


# ============================================================
# Q-Net HTML 추출
# ============================================================

def fetch_qnet_content(
    session: requests.Session,
    target: CertificateTarget,
) -> str:

    response = session.get(
        target.source_url,
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()

    # Q-Net은 EUC-KR 계열 페이지가 섞여 있음
    if response.apparent_encoding:

        response.encoding = (
            response.apparent_encoding
        )


    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )


    for tag in soup(
        [
            "script",
            "style",
            "nav",
            "footer",
            "header",
            "svg",
            "noscript",
        ]
    ):

        tag.decompose()


    content_node = (
        soup.select_one(
            "#content"
        )
        or
        soup.select_one(
            "#contents"
        )
        or
        soup.select_one(
            ".content"
        )
        or
        soup.select_one(
            "main"
        )
        or
        soup.body
    )


    if content_node is None:

        return ""


    content = (
        clean_multiline(
            content_node.get_text(
                "\n",
                strip=True,
            )
        )
    )


    # 정보처리기사 등장 전의 메뉴가 너무 길면 잘라냄
    marker_pos = (
        content.find(
            target.name
        )
    )


    if marker_pos >= 0:

        start = max(
            0,
            marker_pos - 300,
        )

        content = (
            content[
                start:
            ]
        )


    return content[
        :40000
    ]


# ============================================================
# 섹션 추출
# ============================================================

def extract_section(
    content: str,

    start_keywords: list[str],

    end_keywords: list[str],

    max_chars: int = 6000,
) -> str | None:

    if not content:
        return None


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


    start_index = None


    for index, line in enumerate(
        lines
    ):

        compact = (
            line.replace(
                " ",
                ""
            )
        )


        if any(
            keyword.replace(
                " ",
                ""
            )
            in compact

            for keyword
            in start_keywords
        ):

            start_index = (
                index
            )

            break


    if start_index is None:

        return None


    result: list[str] = []


    for index in range(
        start_index,
        len(
            lines
        ),
    ):

        line = (
            lines[index]
        )


        if (
            index > start_index
        ):

            compact = (
                line.replace(
                    " ",
                    ""
                )
            )


            if any(
                keyword.replace(
                    " ",
                    ""
                )
                in compact

                for keyword
                in end_keywords
            ):

                break


        result.append(
            line
        )


        if len(
            "\n".join(
                result
            )
        ) >= max_chars:

            break


    value = (
        "\n".join(
            result
        ).strip()
    )


    return (
        value
        or None
    )


# ============================================================
# 자격증 내용에서 공통 섹션 생성
# ============================================================

def build_sections(
    content: str,
) -> dict[str, str]:

    sections: dict[
        str,
        str
    ] = {}


    if content:

        sections[
            "OFFICIAL_INFO"
        ] = content


    exam_subjects = (
        extract_section(
            content,

            start_keywords=[
                "과목 안내",
                "과목안내",
                "시험과목",
            ],

            end_keywords=[
                "합격 기준",
                "합격기준",
                "유효 기간",
                "유효기간",
                "응시 자격",
                "응시자격",
                "수수료",
            ],
        )
    )


    if exam_subjects:

        sections[
            "EXAM_SUBJECTS"
        ] = exam_subjects


    qualification = (
        extract_section(
            content,

            start_keywords=[
                "응시 자격",
                "응시자격",
                "응시안내",
            ],

            end_keywords=[
                "수수료",
                "응시료",
                "응시절차",
                "시험일정",
                "취득방법",
            ],
        )
    )


    if qualification:

        sections[
            "ELIGIBILITY"
        ] = qualification


    pass_criteria = (
        extract_section(
            content,

            start_keywords=[
                "합격 기준",
                "합격기준",
            ],

            end_keywords=[
                "유효 기간",
                "유효기간",
                "응시 자격",
                "응시자격",
                "응시안내",
                "수수료",
            ],
        )
    )


    if pass_criteria:

        sections[
            "PASS_CRITERIA"
        ] = pass_criteria


    validity = (
        extract_section(
            content,

            start_keywords=[
                "유효 기간",
                "유효기간",
            ],

            end_keywords=[
                "응시 자격",
                "응시자격",
                "응시안내",
                "수수료",
            ],

            max_chars=1500,
        )
    )


    if validity:

        sections[
            "VALIDITY"
        ] = validity


    return sections


# ============================================================
# 설명 생성
# ============================================================

def build_description(
    target: CertificateTarget,
    content: str,
) -> str:

    # 너무 긴 메뉴/표 전체를 description에 넣지 않음
    simple_content = clean_text(
        content
    )


    if target.name in simple_content:

        pos = (
            simple_content.find(
                target.name
            )
        )

        start = max(
            0,
            pos,
        )

        excerpt = (
            simple_content[
                start:
                start + 1000
            ]
        )

    else:

        excerpt = (
            simple_content[
                :1000
            ]
        )


    prefix = (
        f"{target.name} | "
        f"{target.administering_org} 시행. "
    )


    return (
        prefix
        + excerpt
    )[
        :1500
    ]


# ============================================================
# 전체 수집
# ============================================================

def collect_certificates(
    targets: list[
        CertificateTarget
    ],
) -> list[
    CollectedCertificate
]:

    session = (
        create_session()
    )


    dataq_targets = [
        target

        for target
        in targets

        if target.source_kind
        == "DATAQ_PDF"
    ]


    dataq_pages: list[
        str
    ] = []


    # --------------------------------------------------------
    # DataQ PDF는 한 번만 다운로드
    # --------------------------------------------------------

    if dataq_targets:

        print(
            "\n[DataQ 공식 안내 PDF 다운로드]"
        )


        pdf_bytes = (
            download_pdf(
                session,
                DATAQ_BROCHURE_URL,
            )
        )


        print(
            (
                f"  다운로드: "
                f"{len(pdf_bytes):,} bytes"
            )
        )


        dataq_pages = (
            read_pdf_pages(
                pdf_bytes
            )
        )


        print(
            (
                f"  PDF 페이지: "
                f"{len(dataq_pages)}"
            )
        )


    results: list[
        CollectedCertificate
    ] = []


    for index, target in enumerate(
        targets,
        start=1,
    ):

        print(
            (
                f"\n[{index}/"
                f"{len(targets)}] "
                f"{target.name}"
            )
        )


        try:

            metadata: dict = {
                "collector":
                    "career_certificates",

                "qualification_code":
                    target.qualification_code,

                "aliases":
                    target.aliases,

                "source_kind":
                    target.source_kind,
            }


            if (
                target.source_kind
                == "DATAQ_PDF"
            ):

                content, pages = (
                    extract_dataq_certificate_content(
                        target,
                        dataq_pages,
                    )
                )


                metadata[
                    "source_pages"
                ] = pages


            elif (
                target.source_kind
                == "QNET_HTML"
            ):

                content = (
                    fetch_qnet_content(
                        session,
                        target,
                    )
                )


            else:

                raise RuntimeError(
                    (
                        "지원하지 않는 source_kind: "
                        f"{target.source_kind}"
                    )
                )


            content = (
                clean_multiline(
                    content
                )
            )


            if not content:

                print(
                    "  [실패] 공식 내용 추출 실패"
                )

                continue


            sections = (
                build_sections(
                    content
                )
            )


            description = (
                build_description(
                    target,
                    content,
                )
            )


            content_hash = (
                hashlib.sha256(
                    content.encode(
                        "utf-8"
                    )
                ).hexdigest()
            )


            evidence_eligible = (
                len(
                    content
                ) >= 150
            )


            metadata[
                "content_length"
            ] = len(
                content
            )


            metadata[
                "section_types"
            ] = list(
                sections
            )


            result = (
                CollectedCertificate(
                    target=(
                        target
                    ),

                    content=(
                        content
                    ),

                    description=(
                        description
                    ),

                    sections=(
                        sections
                    ),

                    content_hash=(
                        content_hash
                    ),

                    fetch_status=(
                        "SUCCESS"
                    ),

                    evidence_eligible=(
                        evidence_eligible
                    ),

                    raw_metadata=(
                        metadata
                    ),
                )
            )


            results.append(
                result
            )


            print(
                (
                    "  [OK] "
                    f"content={len(content):,}자 "
                    f"| sections="
                    f"{len(sections)}"
                )
            )


        except Exception as exc:

            print(
                (
                    "  [ERROR] "
                    f"{exc}"
                )
            )


    return results


# ============================================================
# DB SQL
# ============================================================

FIND_BY_DEDUP_SQL = text(
    """
    SELECT resource_id

    FROM resource

    WHERE dedup_key =
        :dedup_key
    """
)


FIND_BY_CODE_SQL = text(
    """
    SELECT resource_id

    FROM certificate_detail

    WHERE qualification_code =
        :qualification_code
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
        is_active
    )

    VALUES (
        'CERTIFICATE',
        :dedup_key,
        :name,
        :organization,
        :description,
        :canonical_url,
        TRUE
    )

    RETURNING resource_id
    """
)


UPDATE_RESOURCE_SQL = text(
    """
    UPDATE resource

    SET
        resource_type =
            'CERTIFICATE',

        dedup_key =
            :dedup_key,

        name =
            :name,

        organization =
            :organization,

        description =
            :description,

        canonical_url =
            :canonical_url,

        is_active =
            TRUE,

        last_seen_at =
            NOW(),

        updated_at =
            NOW()

    WHERE resource_id =
        :resource_id
    """
)


UPSERT_CERTIFICATE_DETAIL_SQL = text(
    """
    INSERT INTO certificate_detail (
        resource_id,

        qualification_code,
        qualification_type,

        administering_org,

        exam_frequency,

        validity_period,

        source_updated_at,

        raw_metadata
    )

    VALUES (
        :resource_id,

        :qualification_code,
        :qualification_type,

        :administering_org,

        :exam_frequency,

        :validity_period,

        NULL,

        CAST(
            :raw_metadata
            AS jsonb
        )
    )

    ON CONFLICT (
        resource_id
    )

    DO UPDATE SET

        qualification_code =
            EXCLUDED.qualification_code,

        qualification_type =
            EXCLUDED.qualification_type,

        administering_org =
            EXCLUDED.administering_org,

        exam_frequency =
            EXCLUDED.exam_frequency,

        validity_period =
            EXCLUDED.validity_period,

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
        'OFFICIAL_WEB',
        'CERTIFICATE_INFO',

        :source_record_key,

        'OFFICIAL_CERTIFICATE_SOURCE',

        :source_url,
        :normalized_url,
        :source_host,

        :title,
        :content,

        CAST(
            :raw_metadata
            AS jsonb
        ),

        :content_hash,

        200,
        'SUCCESS',

        'ACCEPTED',
        'A',
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
            EXCLUDED.evidence_eligible,

        fetched_at =
            NOW(),

        updated_at =
            NOW()

    RETURNING source_document_id
    """
)


UPSERT_CERTIFICATE_SECTION_SQL = text(
    """
    INSERT INTO certificate_section (
        resource_id,

        section_type,
        section_order,

        content,

        source_document_id,

        source_updated_at
    )

    VALUES (
        :resource_id,

        :section_type,
        :section_order,

        :content,

        :source_document_id,

        NULL
    )

    ON CONFLICT (
        resource_id,
        section_type
    )

    DO UPDATE SET

        section_order =
            EXCLUDED.section_order,

        content =
            EXCLUDED.content,

        source_document_id =
            EXCLUDED.source_document_id,

        updated_at =
            NOW()
    """
)


# ============================================================
# Resource ID 찾기
# ============================================================

def find_existing_resource_id(
    conn,
    *,
    dedup_key: str,
    qualification_code: str,
) -> int | None:

    resource_id = (
        conn.execute(
            FIND_BY_DEDUP_SQL,

            {
                "dedup_key":
                    dedup_key,
            },
        ).scalar()
    )


    if resource_id is not None:

        return int(
            resource_id
        )


    resource_id = (
        conn.execute(
            FIND_BY_CODE_SQL,

            {
                "qualification_code":
                    qualification_code,
            },
        ).scalar()
    )


    if resource_id is not None:

        return int(
            resource_id
        )


    return None


# ============================================================
# 한 자격증 저장
# ============================================================

def save_certificate(
    certificate: CollectedCertificate,
) -> str:

    target = (
        certificate.target
    )


    dedup_key = (
        "certificate:"
        + target.qualification_code.lower()
    )


    raw_metadata = {
        **certificate.raw_metadata,

        "aliases":
            target.aliases,

        "fetched_at":
            datetime.now(
                SEOUL
            ).isoformat(),
    }


    with engine.begin() as conn:

        # ----------------------------------------------------
        # 기존 Resource 확인
        # ----------------------------------------------------

        existing_id = (
            find_existing_resource_id(
                conn,

                dedup_key=(
                    dedup_key
                ),

                qualification_code=(
                    target
                    .qualification_code
                ),
            )
        )


        resource_params = {
            "dedup_key":
                dedup_key,

            "name":
                target.name,

            "organization":
                target.administering_org,

            "description":
                certificate.description,

            "canonical_url":
                target.source_url,
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

            resource_id = (
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
        # CertificateDetail
        # ----------------------------------------------------

        conn.execute(
            UPSERT_CERTIFICATE_DETAIL_SQL,

            {
                "resource_id":
                    resource_id,

                "qualification_code":
                    target
                    .qualification_code,

                "qualification_type":
                    target
                    .qualification_type,

                "administering_org":
                    target
                    .administering_org,

                # 연간 실제 횟수는 바뀔 수 있으므로
                # 고정 숫자로 박지 않는다.
                "exam_frequency":
                    (
                        "정기 시행 "
                        "(세부 일정은 공식 사이트 확인)"
                    ),

                "validity_period":
                    target
                    .validity_period,

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

        source_document_id = int(
            conn.execute(
                UPSERT_SOURCE_DOCUMENT_SQL,

                {
                    "resource_id":
                        resource_id,

                    "source_name":
                        target.source_name,

                    "source_record_key":
                        target
                        .qualification_code,

                    "source_url":
                        target.source_url,

                    "normalized_url":
                        target.source_url,

                    "source_host":
                        target.source_host,

                    "title":
                        (
                            f"{target.name} "
                            "공식 자격정보"
                        ),

                    "content":
                        certificate.content,

                    "raw_metadata":
                        json.dumps(
                            raw_metadata,
                            ensure_ascii=False,
                        ),

                    "content_hash":
                        certificate
                        .content_hash,

                    "evidence_eligible":
                        certificate
                        .evidence_eligible,
                },
            ).scalar_one()
        )


        # ----------------------------------------------------
        # CertificateSection
        # ----------------------------------------------------

        for order, (
            section_type,
            section_content,
        ) in enumerate(
            certificate
            .sections
            .items(),
            start=1,
        ):

            conn.execute(
                UPSERT_CERTIFICATE_SECTION_SQL,

                {
                    "resource_id":
                        resource_id,

                    "section_type":
                        section_type,

                    "section_order":
                        order,

                    "content":
                        section_content,

                    "source_document_id":
                        source_document_id,
                },
            )


    return action


# ============================================================
# 여러 자격증 저장
# ============================================================

def save_certificates(
    certificates: list[
        CollectedCertificate
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


    for index, certificate in enumerate(
        certificates,
        start=1,
    ):

        try:

            action = (
                save_certificate(
                    certificate
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
                    f"{len(certificates):02d}] "
                    f"{action:<7} | "
                    f"{certificate.target.name}"
                )
            )


        except Exception as exc:

            result[
                "failed"
            ] += 1


            print(
                (
                    f"[{index:02d}/"
                    f"{len(certificates):02d}] "
                    "FAILED  | "
                    f"{certificate.target.name}\n"
                    f"    {exc}"
                )
            )


    return result


# ============================================================
# 미리보기
# ============================================================

def print_preview(
    certificates: list[
        CollectedCertificate
    ],
) -> None:

    print(
        "\n"
        + "=" * 80
    )

    print(
        "CERTIFICATE 수집 미리보기"
    )

    print(
        "=" * 80
    )


    for index, certificate in enumerate(
        certificates,
        start=1,
    ):

        target = (
            certificate.target
        )


        print(
            (
                f"\n{index:02d}. "
                f"{target.name}"
            )
        )


        print(
            (
                f"    code        : "
                f"{target.qualification_code}"
            )
        )


        print(
            (
                f"    type        : "
                f"{target.qualification_type}"
            )
        )


        print(
            (
                f"    org         : "
                f"{target.administering_org}"
            )
        )


        print(
            (
                f"    source      : "
                f"{target.source_url}"
            )
        )


        print(
            (
                f"    content     : "
                f"{len(certificate.content):,}자"
            )
        )


        print(
            (
                "    sections    : "
                + ", ".join(
                    certificate
                    .sections
                    .keys()
                )
            )
        )


        print(
            (
                f"    evidence    : "
                f"{certificate.evidence_eligible}"
            )
        )


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
                WHERE sd.evidence_eligible = TRUE
            ) AS evidence_ready,

            COUNT(DISTINCT cs.resource_id)
                AS with_section

        FROM resource r

        JOIN certificate_detail cd
            ON cd.resource_id =
               r.resource_id

        LEFT JOIN source_document sd
            ON sd.resource_id =
               r.resource_id

           AND sd.document_type =
               'CERTIFICATE_INFO'

        LEFT JOIN certificate_section cs
            ON cs.resource_id =
               r.resource_id

        WHERE r.resource_type =
            'CERTIFICATE'
        """
    )


    with engine.connect() as conn:

        row = conn.execute(
            sql
        ).mappings().one()


    print(
        "\n[현재 CERTIFICATE DB]"
    )


    print(
        (
            f"  전체             : "
            f"{row['total']}개"
        )
    )


    print(
        (
            f"  evidence 사용가능 : "
            f"{row['evidence_ready']}개"
        )
    )


    print(
        (
            f"  section 보유      : "
            f"{row['with_section']}개"
        )
    )


# ============================================================
# 대상 선택
# ============================================================

def select_targets(
    only: list[str] | None,
) -> list[
    CertificateTarget
]:

    if not only:

        return list(
            CERTIFICATE_TARGETS
        )


    requested = {
        value.upper()

        for value
        in only
    }


    result: list[
        CertificateTarget
    ] = []


    for target in (
        CERTIFICATE_TARGETS
    ):

        searchable = {
            target
            .qualification_code
            .upper(),

            target
            .name
            .upper(),

            *[
                alias.upper()

                for alias
                in target.aliases
            ],
        }


        if (
            searchable
            & requested
        ):

            result.append(
                target
            )


    return result


# ============================================================
# 실행
# ============================================================

def run(
    *,
    save_db: bool,
    only: list[str] | None,
) -> None:

    print(
        "=" * 80
    )

    print(
        (
            "CareerQuest - "
            "CERTIFICATE 공식정보 수집"
        )
    )

    print(
        "=" * 80
    )


    targets = (
        select_targets(
            only
        )
    )


    if not targets:

        raise RuntimeError(
            (
                "선택된 자격증이 없습니다."
            )
        )


    print(
        (
            f"수집 대상: "
            f"{len(targets)}개"
        )
    )


    for target in targets:

        print(
            (
                f"  - "
                f"{target.name} "
                f"({target.qualification_code})"
            )
        )


    # --------------------------------------------------------
    # 공식 정보 수집
    # --------------------------------------------------------

    certificates = (
        collect_certificates(
            targets
        )
    )


    print(
        "\n"
        + "=" * 80
    )


    print(
        (
            f"수집 성공: "
            f"{len(certificates)}"
            f"/{len(targets)}개"
        )
    )


    print(
        "=" * 80
    )


    print_preview(
        certificates
    )


    # --------------------------------------------------------
    # DRY RUN
    # --------------------------------------------------------

    if not save_db:

        print(
            "\n[DRY RUN]"
        )


        print(
            (
                "DB를 수정하지 않았습니다."
            )
        )


        print(
            (
                "수집 내용이 정상이라면 "
                "--save를 붙여 다시 실행하세요."
            )
        )


        return


    # --------------------------------------------------------
    # PostgreSQL
    # --------------------------------------------------------

    print(
        "\n[PostgreSQL 적재]"
    )


    result = (
        save_certificates(
            certificates
        )
    )


    print(
        "\n"
        + "=" * 80
    )

    print(
        "CERTIFICATE 적재 결과"
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
        "--save",

        action="store_true",

        help=(
            "수집 결과를 "
            "PostgreSQL에 실제 적재"
        ),
    )


    parser.add_argument(
        "--only",

        nargs="*",

        default=None,

        help=(
            "특정 자격증만 실행. "
            "예: --only SQLD ADsP 정보처리기사"
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
        save_db=(
            args.save
        ),

        only=(
            args.only
        ),
    )