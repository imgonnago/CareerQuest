from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.database import engine
from src.db.models import (
    CertificateSection,
    Resource,
    ResourceSkill,
    Skill,
    SourceDocument,
)


# ============================================================
# 공통 함수
# ============================================================

def normalize_name(
    value: str,
) -> str:

    return " ".join(
        value.strip().lower().split()
    )


def get_skill(
    session: Session,
    skill_name: str,
) -> Skill | None:
    """
    기존 Skill 사전에서만 찾는다.

    데모/LLM이 임의로 새로운 Skill을
    생성하지 못하도록 한다.
    """

    normalized = normalize_name(
        skill_name
    )

    return session.scalar(
        select(
            Skill
        ).where(
            Skill.normalized_name
            == normalized
        )
    )


def clip_evidence(
    text: str,
    max_length: int = 500,
) -> str:

    text = " ".join(
        text.split()
    )

    if len(text) <= max_length:
        return text

    return (
        text[:max_length]
        + "..."
    )


def find_evidence(
    content: str | None,
    keywords: list[str],
) -> str | None:
    """
    원문에서 모든 keyword가 포함된
    부분을 찾아 evidence_text로 사용한다.
    """

    if not content:
        return None

    # 줄 단위 검색
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip()
    ]

    for line in lines:

        lowered = line.lower()

        if all(
            keyword.lower()
            in lowered
            for keyword in keywords
        ):
            return clip_evidence(
                line
            )

    # 줄 단위에서 못 찾으면
    # 전체 본문 기준으로 한 번 더 확인
    lowered_content = (
        content.lower()
    )

    if all(
        keyword.lower()
        in lowered_content
        for keyword in keywords
    ):
        return clip_evidence(
            content
        )

    return None


# ============================================================
# ResourceSkill UPSERT
# ============================================================

def upsert_resource_skill(
    session: Session,
    *,
    resource: Resource,
    skill: Skill,
    relation_type: str,
    evidence_text: str,
    mapping_method: str,
    confidence: Decimal,
    verified: bool,
    source_document_id: int | None,
) -> tuple[
    ResourceSkill,
    bool,
]:
    """
    동일
    resource + skill + relation_type
    조합은 중복 생성하지 않는다.

    반환:
    (ResourceSkill, 신규생성여부)
    """

    mapping = session.scalar(
        select(
            ResourceSkill
        ).where(
            ResourceSkill.resource_id
            == resource.resource_id,

            ResourceSkill.skill_id
            == skill.skill_id,

            ResourceSkill.relation_type
            == relation_type,
        )
    )

    # --------------------------------------------
    # 신규
    # --------------------------------------------

    if mapping is None:

        mapping = ResourceSkill(
            resource_id=(
                resource.resource_id
            ),

            skill_id=(
                skill.skill_id
            ),

            relation_type=(
                relation_type
            ),

            evidence_text=(
                evidence_text
            ),

            mapping_method=(
                mapping_method
            ),

            confidence=(
                confidence
            ),

            verified=(
                verified
            ),

            source_document_id=(
                source_document_id
            ),
        )

        session.add(
            mapping
        )

        session.flush()

        return (
            mapping,
            True,
        )

    # --------------------------------------------
    # 기존 매핑 최신화
    # --------------------------------------------

    mapping.evidence_text = (
        evidence_text
    )

    mapping.mapping_method = (
        mapping_method
    )

    mapping.confidence = (
        confidence
    )

    mapping.verified = (
        verified
    )

    mapping.source_document_id = (
        source_document_id
    )

    return (
        mapping,
        False,
    )


# ============================================================
# 현대그린푸드 Job Mapping
# ============================================================

def find_hyundai_job(
    session: Session,
) -> Resource | None:

    return session.scalar(
        select(
            Resource
        ).where(
            Resource.resource_type
            == "JOB",

            Resource.name.ilike(
                "%현대그린푸드%"
            ),
        )
    )


def find_job_source(
    session: Session,
    resource_id: int,
) -> SourceDocument | None:

    return session.scalar(
        select(
            SourceDocument
        ).where(
            SourceDocument.resource_id
            == resource_id,

            SourceDocument.source_name
            == "JOB_WEB_DETAIL",

            SourceDocument.validation_status
            == "ACCEPTED",
        )
    )


def map_hyundai_job(
    session: Session,
) -> dict:

    print()
    print(
        "=" * 70
    )

    print(
        "1. 현대그린푸드 채용공고 Skill Mapping"
    )

    print(
        "=" * 70
    )

    stats = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
    }

    resource = (
        find_hyundai_job(
            session
        )
    )

    if resource is None:

        print(
            "현대그린푸드 JOB Resource를 "
            "찾지 못했습니다."
        )

        return stats

    source_document = (
        find_job_source(
            session,
            resource.resource_id,
        )
    )

    if source_document is None:

        print(
            "ACCEPTED 상태의 "
            "채용 원문을 찾지 못했습니다."
        )

        return stats

    print(
        "Resource:",
        resource.name,
    )

    print(
        "SourceDocument:",
        source_document.source_document_id,
    )

    # --------------------------------------------------------
    # 매핑 정의
    #
    # keywords는 실제 원문에 해당 근거가
    # 존재하는지 검사하는 용도.
    # --------------------------------------------------------

    mappings = [
        {
            "skill": "Python",

            "relation_type": (
                "REQUIRED"
            ),

            "keywords": [
                "SQL",
                "Python",
            ],

            "evidence": (
                "자격요건: SQL 및 Python을 "
                "능숙하게 활용하여 데이터 전처리, "
                "자동화, 파이프라인 구축을 직접 "
                "수행할 수 있는 분"
            ),
        },

        {
            "skill": "SQL",

            "relation_type": (
                "REQUIRED"
            ),

            "keywords": [
                "SQL",
                "Python",
            ],

            "evidence": (
                "자격요건: SQL 및 Python을 "
                "능숙하게 활용하여 데이터 전처리, "
                "자동화, 파이프라인 구축을 직접 "
                "수행할 수 있는 분"
            ),
        },

        {
            "skill": "AWS",

            "relation_type": (
                "REQUIRED"
            ),

            "keywords": [
                "AWS",
                "클라우드",
            ],

            "evidence": (
                "자격요건: AWS 등 클라우드 환경에서 "
                "MLOps 기반 파이프라인 설계 또는 "
                "운영 경험"
            ),
        },

        {
            "skill": "MLOps",

            "relation_type": (
                "REQUIRED"
            ),

            "keywords": [
                "MLOps",
            ],

            "evidence": (
                "자격요건: 데이터 엔지니어링, "
                "MLOps 또는 데이터 아키텍처 "
                "설계·운영 경험"
            ),
        },

        {
            "skill": "LLM",

            "relation_type": (
                "PREFERRED"
            ),

            "keywords": [
                "LLM",
                "우대",
            ],

            "evidence": (
                "우대사항: GenAI / LLM 연계 경험, "
                "사내 데이터와 LLM을 연계하는 "
                "아키텍처 설계 경험"
            ),
        },

        {
            "skill": "RAG",

            "relation_type": (
                "PREFERRED"
            ),

            "keywords": [
                "RAG",
            ],

            "evidence": (
                "우대사항: 사내 데이터와 LLM을 "
                "연계하는 RAG 구축 경험"
            ),
        },
    ]

    # --------------------------------------------------------
    # 실제 매핑
    # --------------------------------------------------------

    for item in mappings:

        skill = get_skill(
            session,
            item["skill"],
        )

        # Skill 사전에 없는 경우
        if skill is None:

            print(
                "[SKIP]",
                item["skill"],
                "→ Skill 사전에 없음",
            )

            stats[
                "skipped"
            ] += 1

            continue

        # 실제 원문에 근거가 있는지 확인
        found = find_evidence(
            source_document.content,
            item["keywords"],
        )

        if found is None:

            print(
                "[SKIP]",
                item["skill"],
                "→ 원문 근거 확인 실패",
            )

            stats[
                "skipped"
            ] += 1

            continue

        _, created = (
            upsert_resource_skill(
                session,

                resource=(
                    resource
                ),

                skill=(
                    skill
                ),

                relation_type=(
                    item[
                        "relation_type"
                    ]
                ),

                evidence_text=(
                    item[
                        "evidence"
                    ]
                ),

                mapping_method=(
                    "MANUAL_DEMO"
                ),

                confidence=(
                    Decimal(
                        "1.00000"
                    )
                ),

                verified=True,

                source_document_id=(
                    source_document
                    .source_document_id
                ),
            )
        )

        if created:

            stats[
                "created"
            ] += 1

            action = (
                "CREATE"
            )

        else:

            stats[
                "updated"
            ] += 1

            action = (
                "UPDATE"
            )

        print(
            f"[{action}]",
            item["skill"],
            "→",
            item[
                "relation_type"
            ],
        )

    return stats


# ============================================================
# 정보처리기사 Skill Mapping
# ============================================================

def find_information_processing_certificate(
    session: Session,
) -> Resource | None:

    return session.scalar(
        select(
            Resource
        ).where(
            Resource.resource_type
            == "CERTIFICATE",

            Resource.name
            == "정보처리기사",
        )
    )


def map_certificate(
    session: Session,
) -> dict:

    print()
    print(
        "=" * 70
    )

    print(
        "2. 정보처리기사 Skill Mapping"
    )

    print(
        "=" * 70
    )

    stats = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
    }

    resource = (
        find_information_processing_certificate(
            session
        )
    )

    if resource is None:

        print(
            "정보처리기사 Resource를 "
            "찾지 못했습니다."
        )

        return stats

    sections = list(
        session.scalars(
            select(
                CertificateSection
            ).where(
                CertificateSection.resource_id
                == resource.resource_id
            )
        ).all()
    )

    if not sections:

        print(
            "정보처리기사 CertificateSection이 "
            "없습니다."
        )

        return stats

    print(
        "Resource:",
        resource.name,
    )

    print(
        "Section:",
        len(sections),
        "개",
    )

    # --------------------------------------------------------
    # 자격증의 경우,
    # 실제 section 원문에 키워드가 존재할 때만
    # 매핑 후보로 인정한다.
    #
    # keyword 하나를 발견했다고 해서
    # Skill 검증을 완전히 확정하는 것은 아니므로
    #
    # verified=False
    # confidence=0.9
    #
    # 로 저장한다.
    # --------------------------------------------------------

    mappings = [
        {
            "skill": "SQL",
            "keywords": [
                "SQL",
            ],
        },

        {
            "skill": "Database",
            "keywords": [
                "데이터베이스",
            ],
        },

        {
            "skill": "Software Testing",
            "keywords": [
                "테스트",
            ],
        },

        {
            "skill": "Software Engineering",
            "keywords": [
                "소프트웨어",
            ],
        },
    ]

    for item in mappings:

        skill = get_skill(
            session,
            item["skill"],
        )

        if skill is None:

            print(
                "[SKIP]",
                item["skill"],
                "→ Skill 사전에 없음",
            )

            stats[
                "skipped"
            ] += 1

            continue

        evidence = None
        source_document_id = None
        section_type = None

        # ----------------------------------------
        # 여러 CertificateSection 중
        # 실제 근거가 있는 Section 탐색
        # ----------------------------------------

        for section in sections:

            found = find_evidence(
                section.content,
                item["keywords"],
            )

            if found:

                evidence = (
                    found
                )

                source_document_id = (
                    section
                    .source_document_id
                )

                section_type = (
                    section
                    .section_type
                )

                break

        if evidence is None:

            print(
                "[SKIP]",
                item["skill"],
                "→ CertificateSection에서 "
                "근거 없음",
            )

            stats[
                "skipped"
            ] += 1

            continue

        evidence_text = (
            f"[{section_type}] "
            f"{evidence}"
        )

        _, created = (
            upsert_resource_skill(
                session,

                resource=(
                    resource
                ),

                skill=(
                    skill
                ),

                relation_type=(
                    "VERIFIED"
                ),

                evidence_text=(
                    evidence_text
                ),

                mapping_method=(
                    "RULE_DEMO"
                ),

                confidence=(
                    Decimal(
                        "0.90000"
                    )
                ),

                # Keyword 규칙으로 찾아낸 것이므로
                # 아직 사람의 최종 승인 전 상태
                verified=False,

                source_document_id=(
                    source_document_id
                ),
            )
        )

        if created:

            stats[
                "created"
            ] += 1

            action = (
                "CREATE"
            )

        else:

            stats[
                "updated"
            ] += 1

            action = (
                "UPDATE"
            )

        print(
            f"[{action}]",
            item["skill"],
            "→ VERIFIED",
        )

    return stats


# ============================================================
# 결과 출력
# ============================================================

def print_resource_skill_summary(
    session: Session,
) -> None:

    print()
    print(
        "=" * 70
    )

    print(
        "ResourceSkill 결과"
    )

    print(
        "=" * 70
    )

    rows = session.execute(
        select(
            Resource.name,
            Skill.name,
            ResourceSkill.relation_type,
            ResourceSkill.mapping_method,
            ResourceSkill.confidence,
            ResourceSkill.verified,
        )
        .join(
            Resource,
            Resource.resource_id
            == ResourceSkill.resource_id,
        )
        .join(
            Skill,
            Skill.skill_id
            == ResourceSkill.skill_id,
        )
        .where(
            ResourceSkill.mapping_method.in_(
                [
                    "MANUAL_DEMO",
                    "RULE_DEMO",
                ]
            )
        )
        .order_by(
            Resource.name,
            ResourceSkill.relation_type,
            Skill.name,
        )
    ).all()

    for row in rows:

        print(
            f"{row.name}"
            f" | {row[1]}"
            f" | {row.relation_type}"
            f" | {row.mapping_method}"
            f" | confidence={row.confidence}"
            f" | verified={row.verified}"
        )


# ============================================================
# Main
# ============================================================

def main() -> None:

    print(
        "=" * 70
    )

    print(
        "CareerQuest Demo Skill Mapping"
    )

    print(
        "=" * 70
    )

    with Session(
        engine
    ) as session:

        try:

            job_stats = (
                map_hyundai_job(
                    session
                )
            )

            certificate_stats = (
                map_certificate(
                    session
                )
            )

            session.commit()

            print_resource_skill_summary(
                session
            )

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
        "데모 Skill Mapping 완료"
    )

    print(
        "=" * 70
    )

    print()

    print(
        "[현대그린푸드]"
    )

    print(
        "신규:",
        job_stats[
            "created"
        ],
    )

    print(
        "갱신:",
        job_stats[
            "updated"
        ],
    )

    print(
        "건너뜀:",
        job_stats[
            "skipped"
        ],
    )

    print()

    print(
        "[정보처리기사]"
    )

    print(
        "신규:",
        certificate_stats[
            "created"
        ],
    )

    print(
        "갱신:",
        certificate_stats[
            "updated"
        ],
    )

    print(
        "건너뜀:",
        certificate_stats[
            "skipped"
        ],
    )


if __name__ == "__main__":
    main()