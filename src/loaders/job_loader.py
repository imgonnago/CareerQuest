from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.database import engine
from src.db.models import (
    JobDetail,
    Resource,
)

from src.loaders.common import (
    normalize_url,
    read_jsonl,
    sha256_text,
    upsert_source_document,
    utc_now,
)


# ============================================================
# 설정
# ============================================================

RAW_DIR = Path(
    "data/raw/jobs"
)


# ============================================================
# 출처 분류
# ============================================================

# 공공기관 / 공신력 있는 공식 채용 제공처
OFFICIAL_JOB_HOSTS = {
    "worldjob.or.kr",
    "work.mma.go.kr",
    "work24.go.kr",
    "work.go.kr",
    "kofia.or.kr",
}

# 민간 채용 플랫폼
JOB_BOARD_HOSTS = {
    "wanted.co.kr",
    "saramin.co.kr",
    "jobkorea.co.kr",
    "jumpit.saramin.co.kr",
    "career.rememberapp.co.kr",
    "jobplanet.co.kr",
    "catch.co.kr",
}

# 기업 공식 채용페이지에서 자주 나타나는 host/path 문자열
OFFICIAL_RECRUIT_HOST_KEYWORDS = (
    "recruit.",
    "career.",
    "careers.",
    "jobs.",
)

OFFICIAL_RECRUIT_PATH_KEYWORDS = (
    "/recruit/",
    "/recruit-",
    "/career/",
    "/careers/",
    "/jobs/",
    "/employment/",
    "/announcement/",
)


# ============================================================
# 채용 여부 판별 키워드
# ============================================================

JOB_TITLE_KEYWORDS = (
    "채용",
    "모집",
    "공고",
    "recruit",
    "career",
    "hiring",
)

JOB_BODY_KEYWORDS = (
    "담당업무",
    "주요업무",
    "수행업무",
    "자격요건",
    "지원자격",
    "우대사항",
    "모집요강",
    "모집부문",
    "채용인원",
    "근무지",
    "지원방법",
    "전형절차",
)

# 채용공고 검색 시 섞이기 쉬운 노이즈
NOISE_TITLE_KEYWORDS = (
    "뉴스",
    "기사",
    "부트캠프",
    "교육과정",
    "교육생",
    "양성",
    "특강",
    "세미나",
    "설명회",
    "보고서",
    "연구보고서",
    "전망",
    "후기",
    "취업뉴스",
)

NOISE_URL_KEYWORDS = (
    "/news/",
    "/article/",
    "/press/",
    "/blog/",
)


# ============================================================
# 파일 탐색
# ============================================================

def find_latest_file(
    pattern: str,
) -> Path:

    files = sorted(
        RAW_DIR.glob(
            pattern
        ),
        reverse=True,
    )

    if not files:

        raise FileNotFoundError(
            f"{pattern} 파일을 "
            f"찾을 수 없습니다."
        )

    return files[0]


def find_latest_candidate_file() -> Path:

    return find_latest_file(
        "job_candidates_*.jsonl"
    )


def find_latest_detail_file() -> Path:

    return find_latest_file(
        "job_details_*.jsonl"
    )


# ============================================================
# 문자열 / 날짜
# ============================================================

def clean_string(
    value,
) -> str | None:

    if value is None:
        return None

    value = str(
        value
    ).strip()

    return value or None


def parse_iso_datetime(
    value,
) -> datetime | None:

    value = clean_string(
        value
    )

    if not value:
        return None

    try:

        result = (
            datetime.fromisoformat(
                value.replace(
                    "Z",
                    "+00:00",
                )
            )
        )

        if (
            result.tzinfo
            is None
        ):
            result = (
                result.replace(
                    tzinfo=timezone.utc
                )
            )

        return result

    except ValueError:

        return None


# ============================================================
# URL / Host
# ============================================================

def get_host(
    url: str | None,
) -> str:

    if not url:
        return ""

    host = (
        urlsplit(url)
        .netloc
        .lower()
    )

    if host.startswith(
        "www."
    ):
        host = host[4:]

    return host


def host_matches(
    host: str,
    targets: set[str],
) -> bool:

    return any(
        host == target
        or host.endswith(
            "." + target
        )
        for target in targets
    )


def looks_like_official_recruit_page(
    url: str,
) -> bool:

    lowered = url.lower()

    host = get_host(
        url
    )

    if any(
        keyword in host
        for keyword
        in OFFICIAL_RECRUIT_HOST_KEYWORDS
    ):
        return True

    if any(
        keyword in lowered
        for keyword
        in OFFICIAL_RECRUIT_PATH_KEYWORDS
    ):
        return True

    return False


# ============================================================
# 채용 상세페이지 검증
# ============================================================

def validate_job_detail(
    record: dict,
) -> dict:
    """
    LLM을 사용하지 않고
    보수적인 규칙으로 실제 채용공고인지 판별한다.

    결과:
    {
        status:
            ACCEPTED / REJECTED / PENDING,
        source_type:
            OFFICIAL_WEB / JOB_BOARD / OTHER,
        trust_level:
            A / B / C,
        evidence_eligible:
            bool,
        reason:
            판별 사유
    }
    """

    fetch_status = clean_string(
        record.get(
            "fetch_status"
        )
    )

    http_status = record.get(
        "http_status"
    )

    raw_text = (
        clean_string(
            record.get(
                "raw_text"
            )
        )
        or ""
    )

    title = (
        clean_string(
            record.get(
                "title"
            )
        )
        or ""
    )

    page_title = (
        clean_string(
            record.get(
                "page_title"
            )
        )
        or ""
    )

    source_url = (
        clean_string(
            record.get(
                "final_url"
            )
        )
        or clean_string(
            record.get(
                "source_url"
            )
        )
        or ""
    )

    host = get_host(
        source_url
    )

    combined_title = (
        f"{title} {page_title}"
    ).lower()

    lowered_url = (
        source_url.lower()
    )

    # --------------------------------------------------------
    # 1. 페이지 자체를 가져오지 못함
    # --------------------------------------------------------

    if (
        fetch_status
        != "SUCCESS"
    ):

        return {
            "status": "REJECTED",
            "source_type": "OTHER",
            "trust_level": "C",
            "evidence_eligible": False,
            "reason": (
                "상세페이지 수집 실패: "
                f"{fetch_status}"
            ),
        }

    if (
        not raw_text
        or len(raw_text) < 300
    ):

        return {
            "status": "REJECTED",
            "source_type": "OTHER",
            "trust_level": "C",
            "evidence_eligible": False,
            "reason": (
                "본문이 없거나 "
                "너무 짧음"
            ),
        }

    # --------------------------------------------------------
    # 2. 뉴스 / 교육 / 보고서 등 명확한 노이즈
    # --------------------------------------------------------

    if any(
        keyword.lower()
        in combined_title

        for keyword
        in NOISE_TITLE_KEYWORDS
    ):

        return {
            "status": "REJECTED",
            "source_type": "OTHER",
            "trust_level": "C",
            "evidence_eligible": False,
            "reason": (
                "뉴스/교육/보고서형 "
                "콘텐츠"
            ),
        }

    if any(
        keyword
        in lowered_url

        for keyword
        in NOISE_URL_KEYWORDS
    ):

        return {
            "status": "REJECTED",
            "source_type": "OTHER",
            "trust_level": "C",
            "evidence_eligible": False,
            "reason": (
                "뉴스/기사형 URL"
            ),
        }

    # --------------------------------------------------------
    # 3. 채용공고 형식 확인
    # --------------------------------------------------------

    title_signal = any(
        keyword.lower()
        in combined_title

        for keyword
        in JOB_TITLE_KEYWORDS
    )

    body_signal_count = sum(
        1
        for keyword
        in JOB_BODY_KEYWORDS

        if keyword.lower()
        in raw_text.lower()
    )

    # 제목에도 채용 신호가 없고
    # 본문 구조도 약하면 보류
    if (
        not title_signal
        and body_signal_count < 2
    ):

        return {
            "status": "PENDING",
            "source_type": "OTHER",
            "trust_level": "C",
            "evidence_eligible": False,
            "reason": (
                "채용공고 여부 "
                "추가 검토 필요"
            ),
        }

    # --------------------------------------------------------
    # 4. 공공 / 공식 채용 출처
    # --------------------------------------------------------

    if host_matches(
        host,
        OFFICIAL_JOB_HOSTS,
    ):

        if body_signal_count >= 2:

            return {
                "status": "ACCEPTED",
                "source_type": (
                    "OFFICIAL_WEB"
                ),
                "trust_level": "A",
                "evidence_eligible": True,
                "reason": (
                    "공식/공공 채용 출처"
                ),
            }

    # --------------------------------------------------------
    # 5. 민간 채용 플랫폼
    # --------------------------------------------------------

    if host_matches(
        host,
        JOB_BOARD_HOSTS,
    ):

        if body_signal_count >= 2:

            return {
                "status": "ACCEPTED",
                "source_type": (
                    "JOB_BOARD"
                ),
                "trust_level": "B",
                "evidence_eligible": True,
                "reason": (
                    "채용 플랫폼 "
                    "상세 공고"
                ),
            }

    # --------------------------------------------------------
    # 6. 기업 공식 채용 페이지로 보이는 URL
    # --------------------------------------------------------

    if (
        looks_like_official_recruit_page(
            source_url
        )
        and body_signal_count >= 2
    ):

        return {
            "status": "ACCEPTED",
            "source_type": (
                "OFFICIAL_WEB"
            ),
            "trust_level": "A",
            "evidence_eligible": True,
            "reason": (
                "기업 공식 채용페이지"
            ),
        }

    # --------------------------------------------------------
    # 7. 출처를 확실하게 판단할 수 없음
    # --------------------------------------------------------

    return {
        "status": "PENDING",
        "source_type": "OTHER",
        "trust_level": "C",
        "evidence_eligible": False,
        "reason": (
            "채용 내용은 있으나 "
            "출처 추가 검토 필요"
        ),
    }


# ============================================================
# Job Resource
# ============================================================

def build_job_dedup_key(
    url: str,
) -> str:

    normalized = (
        normalize_url(url)
        or url
    )

    url_hash = (
        sha256_text(
            normalized
        )
    )

    if not url_hash:

        raise ValueError(
            "채용 URL hash 생성 실패"
        )

    return (
        f"JOB:{url_hash}"
    )


def get_or_create_job_resource(
    session: Session,
    record: dict,
) -> Resource:

    source_url = (
        clean_string(
            record.get(
                "final_url"
            )
        )
        or clean_string(
            record.get(
                "source_url"
            )
        )
    )

    if not source_url:

        raise ValueError(
            "채용공고 URL이 없습니다."
        )

    canonical_url = (
        normalize_url(
            source_url
        )
        or source_url
    )

    dedup_key = (
        build_job_dedup_key(
            canonical_url
        )
    )

    resource = session.scalar(
        select(
            Resource
        ).where(
            Resource.dedup_key
            == dedup_key
        )
    )

    title = (
        clean_string(
            record.get(
                "title"
            )
        )
        or clean_string(
            record.get(
                "page_title"
            )
        )
        or "채용공고"
    )

    description = (
        clean_string(
            record.get(
                "snippet"
            )
        )
    )

    published_at = (
        parse_iso_datetime(
            record.get(
                "published_at"
            )
        )
    )

    # --------------------------------------------------------
    # 신규 Resource
    # --------------------------------------------------------

    if resource is None:

        resource = Resource(
            resource_type="JOB",

            dedup_key=(
                dedup_key
            ),

            name=title,

            organization=None,

            description=(
                description
            ),

            canonical_url=(
                canonical_url
            ),

            published_at=(
                published_at
            ),

            is_active=True,
        )

        session.add(
            resource
        )

        session.flush()

    # --------------------------------------------------------
    # 기존 Resource 최신화
    # --------------------------------------------------------

    else:

        resource.name = (
            title
        )

        resource.description = (
            description
        )

        resource.canonical_url = (
            canonical_url
        )

        if published_at:
            resource.published_at = (
                published_at
            )

        resource.last_seen_at = (
            utc_now()
        )

        resource.is_active = True

    return resource


def get_or_create_job_detail(
    session: Session,
    resource: Resource,
) -> JobDetail:

    detail = session.get(
        JobDetail,
        resource.resource_id,
    )

    if detail is None:

        detail = JobDetail(
            resource_id=(
                resource.resource_id
            ),

            # LLM/Parser가 아직
            # 구조화하지 않은 상태
            parse_status=(
                "UNPARSED"
            ),
        )

        session.add(
            detail
        )

        session.flush()

    return detail


# ============================================================
# Search API 후보 적재
# ============================================================

def load_search_candidates(
    session: Session,
    path: Path,
) -> dict:

    records = (
        read_jsonl(
            path
        )
    )

    stats = {
        "records": 0,
        "documents": 0,
    }

    print()
    print(
        f"[검색 후보] {path.name}"
    )

    for record in records:

        candidate_id = (
            clean_string(
                record.get(
                    "candidate_id"
                )
            )
        )

        if not candidate_id:
            continue

        search_engine = (
            clean_string(
                record.get(
                    "search_engine"
                )
            )
            or "UNKNOWN"
        ).upper()

        source_name = (
            f"{search_engine}_JOB_SEARCH_API"
        )

        snippet = (
            clean_string(
                record.get(
                    "snippet"
                )
            )
        )

        raw_metadata = (
            dict(record)
        )

        upsert_source_document(
            session,

            # 검색 결과는 아직
            # JOB Resource가 아니다.
            resource_id=None,

            source_name=(
                source_name
            ),

            source_type=(
                "SEARCH_API"
            ),

            document_type=(
                "SEARCH_RESULT"
            ),

            source_record_key=(
                f"JOB_SEARCH:"
                f"{candidate_id}"
            ),

            source_url=(
                clean_string(
                    record.get(
                        "source_url"
                    )
                )
            ),

            title=(
                clean_string(
                    record.get(
                        "title"
                    )
                )
            ),

            content=(
                snippet
            ),

            raw_metadata=(
                raw_metadata
            ),

            published_at=(
                parse_iso_datetime(
                    record.get(
                        "published_at"
                    )
                )
            ),

            discovery_channel=(
                search_engine
            ),

            discovery_query=(
                clean_string(
                    record.get(
                        "search_query"
                    )
                )
            ),

            http_status=None,

            fetch_status=(
                "SUCCESS"
            ),

            # 검색 결과 자체는
            # 원문 근거가 아니다.
            validation_status=(
                "PENDING"
            ),

            trust_level="C",

            evidence_eligible=False,
        )

        stats[
            "records"
        ] += 1

        stats[
            "documents"
        ] += 1

    return stats


# ============================================================
# 상세 페이지 적재
# ============================================================

def load_job_details(
    session: Session,
    path: Path,
) -> dict:

    records = (
        read_jsonl(
            path
        )
    )

    stats = {
        "records": 0,
        "accepted": 0,
        "pending": 0,
        "rejected": 0,
        "resources_created": 0,
        "documents": 0,
    }

    print()
    print(
        f"[상세 페이지] {path.name}"
    )

    # 기존 JOB Resource ID
    before_job_ids = set(
        session.scalars(
            select(
                Resource.resource_id
            ).where(
                Resource.resource_type
                == "JOB"
            )
        ).all()
    )

    for record in records:

        candidate_id = (
            clean_string(
                record.get(
                    "candidate_id"
                )
            )
        )

        if not candidate_id:
            continue

        validation = (
            validate_job_detail(
                record
            )
        )

        status = (
            validation[
                "status"
            ]
        )

        resource = None

        # ----------------------------------------------------
        # ACCEPTED만 실제 JOB Resource 생성
        # ----------------------------------------------------

        if status == "ACCEPTED":

            resource = (
                get_or_create_job_resource(
                    session,
                    record,
                )
            )

            get_or_create_job_detail(
                session,
                resource,
            )

            stats[
                "accepted"
            ] += 1

        elif status == "PENDING":

            stats[
                "pending"
            ] += 1

        else:

            stats[
                "rejected"
            ] += 1

        # ----------------------------------------------------
        # 상세 SourceDocument
        # ----------------------------------------------------

        raw_metadata = (
            dict(record)
        )

        raw_metadata[
            "_loader_validation"
        ] = {
            "status": (
                status
            ),
            "reason": (
                validation[
                    "reason"
                ]
            ),
        }

        final_url = (
            clean_string(
                record.get(
                    "final_url"
                )
            )
            or clean_string(
                record.get(
                    "source_url"
                )
            )
        )

        upsert_source_document(
            session,

            resource_id=(
                resource.resource_id
                if resource
                else None
            ),

            source_name=(
                "JOB_WEB_DETAIL"
            ),

            source_type=(
                validation[
                    "source_type"
                ]
            ),

            document_type=(
                "WEB_PAGE"
            ),

            source_record_key=(
                f"JOB_DETAIL:"
                f"{candidate_id}"
            ),

            source_url=(
                final_url
            ),

            title=(
                clean_string(
                    record.get(
                        "title"
                    )
                )
                or clean_string(
                    record.get(
                        "page_title"
                    )
                )
            ),

            content=(
                clean_string(
                    record.get(
                        "raw_text"
                    )
                )
            ),

            raw_metadata=(
                raw_metadata
            ),

            published_at=(
                parse_iso_datetime(
                    record.get(
                        "published_at"
                    )
                )
            ),

            discovery_channel=(
                clean_string(
                    record.get(
                        "search_engine"
                    )
                )
            ),

            discovery_query=(
                clean_string(
                    record.get(
                        "search_query"
                    )
                )
            ),

            http_status=(
                record.get(
                    "http_status"
                )
            ),

            fetch_status=(
                clean_string(
                    record.get(
                        "fetch_status"
                    )
                )
                or "PENDING"
            ),

            validation_status=(
                status
            ),

            trust_level=(
                validation[
                    "trust_level"
                ]
            ),

            evidence_eligible=(
                validation[
                    "evidence_eligible"
                ]
            ),
        )

        stats[
            "documents"
        ] += 1

        stats[
            "records"
        ] += 1

        print(
            f"  [{status}] "
            f"{clean_string(record.get('title'))}"
        )

        print(
            "     →",
            validation[
                "reason"
            ],
        )

    session.flush()

    after_job_ids = set(
        session.scalars(
            select(
                Resource.resource_id
            ).where(
                Resource.resource_type
                == "JOB"
            )
        ).all()
    )

    stats[
        "resources_created"
    ] = len(
        after_job_ids
        - before_job_ids
    )

    return stats


# ============================================================
# Main
# ============================================================

def main() -> None:

    if not RAW_DIR.exists():

        raise FileNotFoundError(
            f"{RAW_DIR} "
            f"폴더가 없습니다."
        )

    candidate_file = (
        find_latest_candidate_file()
    )

    detail_file = (
        find_latest_detail_file()
    )

    print(
        "=" * 70
    )

    print(
        "CareerQuest Job Loader"
    )

    print(
        "=" * 70
    )

    print()

    print(
        "후보 파일:",
        candidate_file.name,
    )

    print(
        "상세 파일:",
        detail_file.name,
    )

    with Session(
        engine
    ) as session:

        try:

            search_stats = (
                load_search_candidates(
                    session,
                    candidate_file,
                )
            )

            detail_stats = (
                load_job_details(
                    session,
                    detail_file,
                )
            )

            session.commit()

        except Exception:

            session.rollback()

            print()
            print(
                "오류 발생 → "
                "전체 Transaction rollback"
            )

            raise

    print()
    print(
        "=" * 70
    )

    print(
        "채용 데이터 적재 완료"
    )

    print(
        "=" * 70
    )

    print()
    print(
        "[검색 후보]"
    )

    print(
        "처리:",
        search_stats[
            "records"
        ],
    )

    print(
        "SourceDocument:",
        search_stats[
            "documents"
        ],
    )

    print()
    print(
        "[상세 페이지]"
    )

    print(
        "처리:",
        detail_stats[
            "records"
        ],
    )

    print(
        "ACCEPTED:",
        detail_stats[
            "accepted"
        ],
    )

    print(
        "PENDING:",
        detail_stats[
            "pending"
        ],
    )

    print(
        "REJECTED:",
        detail_stats[
            "rejected"
        ],
    )

    print(
        "신규 JOB Resource:",
        detail_stats[
            "resources_created"
        ],
    )


if __name__ == "__main__":
    main()