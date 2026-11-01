from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.database import engine
from src.db.models import (
    CertificateDetail,
    CertificateSection,
    Resource,
    ResourceRelation,
)

from src.loaders.common import (
    read_jsonl,
    upsert_source_document,
)


load_dotenv()


# ============================================================
# 설정
# ============================================================

RAW_DIR = Path(
    "data/raw/certificates"
)

QNET_ATTENTION_URL = os.getenv(
    "DATA_GO_QNET_ATTENTION_URL"
)

QNET_ITEM_URL = os.getenv(
    "DATA_GO_QNET_ITEM_URL"
)

KST = timezone(
    timedelta(hours=9)
)


# ============================================================
# 날짜 변환
# ============================================================

def parse_qnet_datetime(
    value: str | None,
) -> datetime | None:
    """
    Q-Net 날짜 예:
    20260907210433
    """

    if not value:
        return None

    value = value.strip()

    try:

        dt = datetime.strptime(
            value,
            "%Y%m%d%H%M%S",
        )

        return dt.replace(
            tzinfo=KST
        )

    except ValueError:

        return None


# ============================================================
# RAW 파일 탐색
# ============================================================

def find_latest_attention_file() -> Path:
    """
    가장 최근 관심자격 JSONL 파일 선택.
    """

    files = sorted(
        RAW_DIR.glob(
            "qnet_attention_*.jsonl"
        ),
        reverse=True,
    )

    if not files:
        raise FileNotFoundError(
            "qnet_attention_*.jsonl "
            "파일이 없습니다."
        )

    return files[0]


def find_latest_detail_files() -> list[Path]:
    """
    자격코드별 가장 최근 상세파일 하나만 선택.

    예:
    qnet_1320_20261031_032414.jsonl
    qnet_1320_20261031_032741.jsonl

    → 최신 파일 하나만 사용
    """

    pattern = re.compile(
        r"qnet_(\d+)_.*\.jsonl$"
    )

    latest_by_code: dict[
        str,
        Path,
    ] = {}

    for path in sorted(
        RAW_DIR.glob(
            "qnet_*.jsonl"
        )
    ):

        if path.name.startswith(
            "qnet_attention_"
        ):
            continue

        match = pattern.match(
            path.name
        )

        if not match:
            continue

        qualification_code = (
            match.group(1)
        )

        previous = (
            latest_by_code.get(
                qualification_code
            )
        )

        if (
            previous is None
            or path.name
            > previous.name
        ):

            latest_by_code[
                qualification_code
            ] = path

    return list(
        latest_by_code.values()
    )


# ============================================================
# Resource / Certificate 생성
# ============================================================

def get_or_create_certificate(
    session: Session,
    qualification_code: str,
    qualification_name: str,
) -> tuple[
    Resource,
    CertificateDetail,
]:
    """
    qualification_code를 기준으로
    자격증 Resource / CertificateDetail을 생성하거나 조회.
    """

    qualification_code = (
        qualification_code.strip()
    )

    qualification_name = (
        qualification_name.strip()
        if qualification_name
        else qualification_code
    )

    # --------------------------------------------------------
    # CertificateDetail이 이미 존재하는 경우
    # --------------------------------------------------------

    certificate = session.scalar(
        select(
            CertificateDetail
        ).where(
            CertificateDetail.qualification_code
            == qualification_code
        )
    )

    if certificate is not None:

        resource = session.get(
            Resource,
            certificate.resource_id,
        )

        if resource is None:
            raise RuntimeError(
                "CertificateDetail은 존재하지만 "
                "연결된 Resource가 없습니다."
            )

        # 최신 명칭 반영
        resource.name = (
            qualification_name
        )

        resource.last_seen_at = (
            datetime.now(
                timezone.utc
            )
        )

        return (
            resource,
            certificate,
        )

    # --------------------------------------------------------
    # Resource 찾기
    # --------------------------------------------------------

    dedup_key = (
        f"CERTIFICATE:"
        f"{qualification_code}"
    )

    resource = session.scalar(
        select(
            Resource
        ).where(
            Resource.dedup_key
            == dedup_key
        )
    )

    # --------------------------------------------------------
    # Resource 신규 생성
    # --------------------------------------------------------

    if resource is None:

        resource = Resource(
            resource_type=(
                "CERTIFICATE"
            ),

            dedup_key=(
                dedup_key
            ),

            name=(
                qualification_name
            ),

            organization=(
                "한국산업인력공단"
            ),

            is_active=True,
        )

        session.add(
            resource
        )

        session.flush()

    # --------------------------------------------------------
    # CertificateDetail 생성
    # --------------------------------------------------------

    certificate = (
        CertificateDetail(
            resource_id=(
                resource.resource_id
            ),

            qualification_code=(
                qualification_code
            ),

            administering_org=(
                "한국산업인력공단"
            ),
        )
    )

    session.add(
        certificate
    )

    session.flush()

    return (
        resource,
        certificate,
    )


# ============================================================
# 관심자격 API Loader
# ============================================================

def load_attention_data(
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
        "relations": 0,
        "documents": 0,
    }

    print()
    print(
        f"[관심자격] {path}"
    )

    for record in records:

        qualification_code = (
            record.get(
                "qualification_code"
            )
        )

        qualification_name = (
            record.get(
                "qualification_name"
            )
        )

        if (
            not qualification_code
            or not qualification_name
        ):
            continue

        (
            resource,
            certificate,
        ) = get_or_create_certificate(
            session,
            qualification_code,
            qualification_name,
        )

        # ----------------------------------------------------
        # Q-Net 갱신일
        # ----------------------------------------------------

        modified_at = (
            parse_qnet_datetime(
                record.get(
                    "modified_at"
                )
            )
        )

        if modified_at:

            certificate.source_updated_at = (
                modified_at
            )

        # 원본 metadata도 저장
        certificate.raw_metadata = (
            record.get(
                "raw_metadata"
            )
            or record
        )

        # ----------------------------------------------------
        # SourceDocument
        # ----------------------------------------------------

        upsert_source_document(
            session,

            resource_id=(
                resource.resource_id
            ),

            source_name=(
                "QNET_ATTENTION_API"
            ),

            source_type=(
                "OFFICIAL_API"
            ),

            document_type=(
                "CERTIFICATE_INFO"
            ),

            source_record_key=(
                f"ATTENTION:"
                f"{qualification_code}"
            ),

            source_url=(
                QNET_ATTENTION_URL
            ),

            title=(
                f"{qualification_name} "
                f"관심자격 정보"
            ),

            content=(
                f"{qualification_name} "
                f"관련 추천 자격정보"
            ),

            raw_metadata=(
                record
            ),

            # modified_at은 게시일이 아니므로
            # published_at에 넣지 않는다.
            published_at=None,

            http_status=200,

            fetch_status=(
                "SUCCESS"
            ),

            validation_status=(
                "ACCEPTED"
            ),

            trust_level="A",

            evidence_eligible=True,
        )

        stats[
            "documents"
        ] += 1

        # ----------------------------------------------------
        # 추천 자격 관계
        # ----------------------------------------------------

        recommendations = [
            (
                record.get(
                    "recommended_code_1"
                ),
                record.get(
                    "recommended_name_1"
                ),
            ),
            (
                record.get(
                    "recommended_code_2"
                ),
                record.get(
                    "recommended_name_2"
                ),
            ),
        ]

        for (
            recommended_code,
            recommended_name,
        ) in recommendations:

            if (
                not recommended_code
                or not recommended_name
            ):
                continue

            (
                related_resource,
                _,
            ) = get_or_create_certificate(
                session,
                recommended_code,
                recommended_name,
            )

            # 자기 자신 추천 방지
            if (
                related_resource.resource_id
                == resource.resource_id
            ):
                continue

            relation = session.scalar(
                select(
                    ResourceRelation
                ).where(
                    ResourceRelation.resource_id
                    == resource.resource_id,

                    ResourceRelation.related_resource_id
                    == related_resource.resource_id,

                    ResourceRelation.relation_type
                    == "RECOMMENDED_CERTIFICATE",
                )
            )

            if relation is None:

                relation = ResourceRelation(
                    resource_id=(
                        resource.resource_id
                    ),

                    related_resource_id=(
                        related_resource.resource_id
                    ),

                    relation_type=(
                        "RECOMMENDED_CERTIFICATE"
                    ),

                    evidence_text=(
                        "Q-Net 관심자격 "
                        "API 추천 관계"
                    ),

                    mapping_method=(
                        "OFFICIAL_API"
                    ),

                    confidence=(
                        Decimal(
                            "1.00000"
                        )
                    ),
                )

                session.add(
                    relation
                )

                stats[
                    "relations"
                ] += 1

        stats[
            "records"
        ] += 1

    session.flush()

    return stats


# ============================================================
# 종목별 상세정보 Loader
# ============================================================

def load_detail_file(
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
        "sections": 0,
        "documents": 0,
    }

    print()
    print(
        f"[종목 상세] {path}"
    )

    for index, record in enumerate(
        records,
        start=1,
    ):

        qualification_code = (
            record.get(
                "qualification_code"
            )
        )

        qualification_name = (
            record.get(
                "qualification_name"
            )
        )

        info_type = (
            record.get(
                "info_type"
            )
        )

        content = (
            record.get(
                "content"
            )
        )

        if (
            not qualification_code
            or not qualification_name
            or not info_type
        ):
            continue

        (
            resource,
            certificate,
        ) = get_or_create_certificate(
            session,
            qualification_code,
            qualification_name,
        )

        # ----------------------------------------------------
        # Certificate 기본정보 업데이트
        # ----------------------------------------------------

        certificate.major_field_code = (
            record.get(
                "major_field_code"
            )
        )

        certificate.major_field_name = (
            record.get(
                "major_field_name"
            )
        )

        certificate.middle_field_code = (
            record.get(
                "middle_field_code"
            )
        )

        certificate.middle_field_name = (
            record.get(
                "middle_field_name"
            )
        )

        raw_metadata = (
            record.get(
                "raw_metadata"
            )
            or record
        )

        certificate.raw_metadata = (
            raw_metadata
        )

        # ----------------------------------------------------
        # SourceDocument
        # ----------------------------------------------------

        source_document = (
            upsert_source_document(
                session,

                resource_id=(
                    resource.resource_id
                ),

                source_name=(
                    "QNET_ITEM_API"
                ),

                source_type=(
                    "OFFICIAL_API"
                ),

                document_type=(
                    "CERTIFICATE_INFO"
                ),

                source_record_key=(
                    f"DETAIL:"
                    f"{qualification_code}:"
                    f"{info_type}"
                ),

                source_url=(
                    QNET_ITEM_URL
                ),

                title=(
                    f"{qualification_name} - "
                    f"{info_type}"
                ),

                content=(
                    content
                ),

                raw_metadata=(
                    record
                ),

                published_at=None,

                http_status=200,

                fetch_status=(
                    "SUCCESS"
                ),

                validation_status=(
                    "ACCEPTED"
                ),

                trust_level="A",

                evidence_eligible=True,
            )
        )

        stats[
            "documents"
        ] += 1

        # ----------------------------------------------------
        # CertificateSection
        # ----------------------------------------------------

        section = session.scalar(
            select(
                CertificateSection
            ).where(
                CertificateSection.resource_id
                == resource.resource_id,

                CertificateSection.section_type
                == info_type,
            )
        )

        if section is None:

            section = (
                CertificateSection(
                    resource_id=(
                        resource.resource_id
                    ),

                    section_type=(
                        info_type
                    ),

                    section_order=(
                        index
                    ),

                    content=(
                        content or ""
                    ),

                    source_document_id=(
                        source_document
                        .source_document_id
                    ),
                )
            )

            session.add(
                section
            )

            stats[
                "sections"
            ] += 1

        else:

            section.section_order = (
                index
            )

            section.content = (
                content or ""
            )

            section.source_document_id = (
                source_document
                .source_document_id
            )

        stats[
            "records"
        ] += 1

    session.flush()

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

    attention_file = (
        find_latest_attention_file()
    )

    detail_files = (
        find_latest_detail_files()
    )

    print(
        "=" * 70
    )

    print(
        "CareerQuest Q-Net Loader"
    )

    print(
        "=" * 70
    )

    print()

    print(
        "관심자격 파일:",
        attention_file.name,
    )

    print(
        "상세정보 파일:",
        len(detail_files),
        "개",
    )

    for file in detail_files:

        print(
            f"  - {file.name}"
        )

    # ========================================================
    # 하나의 Transaction으로 실행
    # ========================================================

    with Session(
        engine
    ) as session:

        try:

            attention_stats = (
                load_attention_data(
                    session,
                    attention_file,
                )
            )

            detail_stats = []

            for path in detail_files:

                stats = (
                    load_detail_file(
                        session,
                        path,
                    )
                )

                detail_stats.append(
                    stats
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

    # ========================================================
    # 결과 출력
    # ========================================================

    print()

    print(
        "=" * 70
    )

    print(
        "Q-Net 적재 완료"
    )

    print(
        "=" * 70
    )

    print()
    print(
        "[관심자격]"
    )

    print(
        "처리 레코드:",
        attention_stats[
            "records"
        ],
    )

    print(
        "추천 관계 신규:",
        attention_stats[
            "relations"
        ],
    )

    print(
        "원문 문서 처리:",
        attention_stats[
            "documents"
        ],
    )

    total_detail_records = sum(
        stat["records"]
        for stat in detail_stats
    )

    total_sections = sum(
        stat["sections"]
        for stat in detail_stats
    )

    total_detail_documents = sum(
        stat["documents"]
        for stat in detail_stats
    )

    print()
    print(
        "[종목 상세]"
    )

    print(
        "처리 레코드:",
        total_detail_records,
    )

    print(
        "Section 신규:",
        total_sections,
    )

    print(
        "원문 문서 처리:",
        total_detail_documents,
    )


if __name__ == "__main__":
    main()