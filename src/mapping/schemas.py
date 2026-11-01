from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


# ============================================================
# Contract version
# ============================================================

SKILL_MAPPING_CONTRACT_VERSION = "1.0"


# ============================================================
# Resource type
# ============================================================

class ResourceType(StrEnum):
    COURSE = "COURSE"
    CERTIFICATE = "CERTIFICATE"
    JOB = "JOB"
    PROGRAM = "PROGRAM"


# ============================================================
# Resource-Skill relation
# ============================================================

class RelationType(StrEnum):
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"

    TAUGHT = "TAUGHT"
    VERIFIED = "VERIFIED"

    RELATED = "RELATED"
    EXPERIENCED = "EXPERIENCED"


# ============================================================
# Resource 종류별 허용 관계
# ============================================================

ALLOWED_RELATIONS_BY_RESOURCE_TYPE: dict[
    ResourceType,
    set[RelationType],
] = {
    ResourceType.JOB: {
        RelationType.REQUIRED,
        RelationType.PREFERRED,
        RelationType.RELATED,
    },

    ResourceType.CERTIFICATE: {
        RelationType.VERIFIED,
        RelationType.RELATED,
    },

    ResourceType.COURSE: {
        RelationType.TAUGHT,
        RelationType.RELATED,
    },

    ResourceType.PROGRAM: {
        RelationType.TAUGHT,
        RelationType.RELATED,
        RelationType.EXPERIENCED,
    },
}


# ============================================================
# Skill reference
#
# 팀원 모델이 사용할 수 있는 Skill 목록
# ============================================================

class SkillReference(BaseModel):
    skill_id: int

    name: str

    aliases: list[str] = Field(
        default_factory=list
    )

    category: str | None = None

    description: str | None = None


# ============================================================
# Mapping Input
#
# 데이터 담당 -> 모델 담당
# ============================================================

class SkillMappingInput(BaseModel):
    contract_version: str = (
        SKILL_MAPPING_CONTRACT_VERSION
    )

    resource_id: int

    resource_type: ResourceType

    resource_name: str

    source_document_id: int | None = None

    source_title: str | None = None

    content: str

    allowed_skills: list[
        SkillReference
    ] = Field(
        default_factory=list
    )


# ============================================================
# 하나의 Skill 매핑 결과
# ============================================================

class SkillMappingItem(BaseModel):
    skill_name: str

    relation_type: RelationType

    evidence_text: str

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    reason: str | None = None


# ============================================================
# Mapping Output
#
# 모델 담당 -> 데이터 담당
# ============================================================

class SkillMappingOutput(BaseModel):
    contract_version: str = (
        SKILL_MAPPING_CONTRACT_VERSION
    )

    resource_id: int

    source_document_id: int | None = None

    mappings: list[
        SkillMappingItem
    ] = Field(
        default_factory=list
    )

    # 원문에서 기술명이 보이지만
    # Skill DB 45개에 없는 경우 기록
    #
    # 모델이 임의로 Skill을 생성하지 않도록 하기 위함
    unmapped_skills: list[
        str
    ] = Field(
        default_factory=list
    )


# ============================================================
# Contract 검증
# ============================================================

def validate_mapping_output(
    mapping_input: SkillMappingInput,
    mapping_output: SkillMappingOutput,
) -> None:

    # --------------------------------------------------------
    # 1. resource_id
    # --------------------------------------------------------

    if (
        mapping_input.resource_id
        != mapping_output.resource_id
    ):
        raise ValueError(
            (
                "resource_id mismatch: "
                f"input={mapping_input.resource_id}, "
                f"output={mapping_output.resource_id}"
            )
        )


    # --------------------------------------------------------
    # 2. source_document_id
    # --------------------------------------------------------

    if (
        mapping_input.source_document_id
        is not None
        and mapping_output.source_document_id
        is not None
        and mapping_input.source_document_id
        != mapping_output.source_document_id
    ):
        raise ValueError(
            (
                "source_document_id mismatch: "
                f"input={mapping_input.source_document_id}, "
                f"output={mapping_output.source_document_id}"
            )
        )


    # --------------------------------------------------------
    # 3. 허용 Skill
    # --------------------------------------------------------

    allowed_skill_names = {
        skill.name.strip().lower()

        for skill
        in mapping_input.allowed_skills
    }


    # --------------------------------------------------------
    # 4. 허용 relation
    # --------------------------------------------------------

    allowed_relations = (
        ALLOWED_RELATIONS_BY_RESOURCE_TYPE[
            mapping_input.resource_type
        ]
    )


    # --------------------------------------------------------
    # 5. 각 매핑 검증
    # --------------------------------------------------------

    seen_pairs: set[
        tuple[str, str]
    ] = set()


    for mapping in (
        mapping_output.mappings
    ):

        normalized_skill = (
            mapping.skill_name
            .strip()
            .lower()
        )


        if (
            normalized_skill
            not in allowed_skill_names
        ):
            raise ValueError(
                (
                    "Skill DB에 없는 Skill: "
                    f"{mapping.skill_name}"
                )
            )


        if (
            mapping.relation_type
            not in allowed_relations
        ):
            raise ValueError(
                (
                    "허용되지 않은 relation_type: "
                    f"resource_type="
                    f"{mapping_input.resource_type}, "
                    f"relation_type="
                    f"{mapping.relation_type}"
                )
            )


        if not (
            mapping.evidence_text
            .strip()
        ):
            raise ValueError(
                (
                    "evidence_text가 "
                    "비어 있습니다."
                )
            )


        pair = (
            normalized_skill,
            mapping.relation_type.value,
        )


        if pair in seen_pairs:
            raise ValueError(
                (
                    "중복 Skill 매핑: "
                    f"{mapping.skill_name} / "
                    f"{mapping.relation_type}"
                )
            )


        seen_pairs.add(
            pair
        )