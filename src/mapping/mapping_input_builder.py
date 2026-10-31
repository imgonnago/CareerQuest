from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.database import engine
from src.db.models import (
    Resource,
    Skill,
    SkillAlias,
    SourceDocument,
)

from src.mapping.schemas import (
    ResourceType,
    SkillMappingInput,
    SkillReference,
)


OUTPUT_DIR = Path(
    "data/processed/mapping_inputs"
)


# ============================================================
# Resource 조회
# ============================================================

def get_resource(
    session: Session,
    resource_id: int,
) -> Resource:

    resource = session.get(
        Resource,
        resource_id,
    )

    if resource is None:
        raise ValueError(
            f"Resource를 찾을 수 없습니다: "
            f"{resource_id}"
        )

    return resource


# ============================================================
# SourceDocument 조회
# ============================================================

def get_best_source_document(
    session: Session,
    resource_id: int,
) -> SourceDocument:

    document = session.scalar(
        select(
            SourceDocument
        )
        .where(
            SourceDocument.resource_id
            == resource_id,

            SourceDocument.validation_status
            == "ACCEPTED",

            SourceDocument.evidence_eligible
            == True,  # noqa: E712

            SourceDocument.content
            .is_not(None),
        )
        .order_by(
            SourceDocument.fetched_at.desc()
        )
    )

    if document is None:
        raise ValueError(
            "Skill Mapping에 사용할 "
            "ACCEPTED SourceDocument가 없습니다."
        )

    if not document.content.strip():
        raise ValueError(
            "SourceDocument 본문이 비어 있습니다."
        )

    return document


# ============================================================
# Skill 사전 생성
# ============================================================

def build_skill_references(
    session: Session,
) -> list[SkillReference]:

    skills = list(
        session.scalars(
            select(
                Skill
            )
            .where(
                Skill.is_active
                == True  # noqa: E712
            )
            .order_by(
                Skill.skill_id
            )
        ).all()
    )

    aliases = list(
        session.scalars(
            select(
                SkillAlias
            )
            .order_by(
                SkillAlias.skill_id
            )
        ).all()
    )

    alias_map: dict[
        int,
        list[str],
    ] = {}

    for alias in aliases:

        alias_map.setdefault(
            alias.skill_id,
            [],
        ).append(
            alias.alias
        )

    references = []

    for skill in skills:

        references.append(
            SkillReference(
                skill_id=(
                    skill.skill_id
                ),

                name=(
                    skill.name
                ),

                aliases=(
                    alias_map.get(
                        skill.skill_id,
                        [],
                    )
                ),
            )
        )

    return references


# ============================================================
# Mapping Input 생성
# ============================================================

def build_mapping_input(
    session: Session,
    resource_id: int,
) -> SkillMappingInput:

    resource = get_resource(
        session,
        resource_id,
    )

    try:
        resource_type = (
            ResourceType(
                resource.resource_type
            )
        )

    except ValueError as error:

        raise ValueError(
            "현재 Skill Mapping Input에서 "
            f"지원하지 않는 Resource Type: "
            f"{resource.resource_type}"
        ) from error

    source_document = (
        get_best_source_document(
            session,
            resource.resource_id,
        )
    )

    skills = (
        build_skill_references(
            session
        )
    )

    return SkillMappingInput(
        resource_id=(
            resource.resource_id
        ),

        resource_type=(
            resource_type
        ),

        resource_name=(
            resource.name
        ),

        source_document_id=(
            source_document
            .source_document_id
        ),

        source_title=(
            source_document.title
        ),

        content=(
            source_document.content
        ),

        allowed_skills=(
            skills
        ),
    )


# ============================================================
# 파일 저장
# ============================================================

def save_mapping_input(
    mapping_input: SkillMappingInput,
) -> Path:

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path = (
        OUTPUT_DIR
        / (
            f"resource_"
            f"{mapping_input.resource_id}"
            f"_mapping_input.json"
        )
    )

    output_path.write_text(
        mapping_input.model_dump_json(
            indent=2,
        ),
        encoding="utf-8",
    )

    return output_path


# ============================================================
# 현대그린푸드 Resource 찾기
# 데모 편의를 위한 함수
# ============================================================

def find_hyundai_greenfood_job(
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


# ============================================================
# Main
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "CareerQuest "
            "LLM Skill Mapping Input Builder"
        )
    )

    parser.add_argument(
        "--resource-id",
        type=int,
        default=None,
        help=(
            "Skill Mapping 대상 "
            "Resource ID"
        ),
    )

    args = parser.parse_args()

    with Session(
        engine
    ) as session:

        resource_id = (
            args.resource_id
        )

        # --------------------------------------------
        # resource-id를 안 주면
        # 데모용 현대그린푸드 공고 사용
        # --------------------------------------------

        if resource_id is None:

            resource = (
                find_hyundai_greenfood_job(
                    session
                )
            )

            if resource is None:
                raise ValueError(
                    "현대그린푸드 "
                    "JOB Resource를 "
                    "찾을 수 없습니다."
                )

            resource_id = (
                resource.resource_id
            )

        mapping_input = (
            build_mapping_input(
                session,
                resource_id,
            )
        )

        output_path = (
            save_mapping_input(
                mapping_input
            )
        )

    print(
        "=" * 70
    )

    print(
        "CareerQuest "
        "LLM Skill Mapping Input"
    )

    print(
        "=" * 70
    )

    print()

    print(
        "Resource ID:",
        mapping_input.resource_id,
    )

    print(
        "Resource Type:",
        mapping_input.resource_type.value,
    )

    print(
        "Resource Name:",
        mapping_input.resource_name,
    )

    print(
        "SourceDocument ID:",
        mapping_input.source_document_id,
    )

    print(
        "본문 길이:",
        len(
            mapping_input.content
        ),
    )

    print(
        "전달 Skill 수:",
        len(
            mapping_input.allowed_skills
        ),
    )

    print()

    print(
        "저장 파일:",
        output_path,
    )

    print()

    print(
        "LLM 입력 JSON 생성 완료"
    )


if __name__ == "__main__":
    main()