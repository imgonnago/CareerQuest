from __future__ import annotations

import re
from enum import Enum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


# ============================================================
# Enum
# ============================================================

class ResourceType(str, Enum):
    JOB = "JOB"
    CERTIFICATE = "CERTIFICATE"
    COURSE = "COURSE"
    PROGRAM = "PROGRAM"


class RelationType(str, Enum):
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"
    TAUGHT = "TAUGHT"
    VERIFIED = "VERIFIED"
    RELATED = "RELATED"
    EXPERIENCED = "EXPERIENCED"


# ============================================================
# Resource 유형별 허용 관계
# ============================================================

ALLOWED_RELATIONS_BY_RESOURCE = {
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
    },
}


# ============================================================
# Skill 사전 전달용
# ============================================================

class SkillReference(BaseModel):
    """
    LLM에게 알려줄 CareerQuest 표준 Skill.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    skill_id: int

    name: str = Field(
        min_length=1
    )

    aliases: list[str] = Field(
        default_factory=list
    )


# ============================================================
# LLM 입력
# ============================================================

class SkillMappingInput(BaseModel):
    """
    LLM Skill Mapper에게 전달되는 입력 데이터.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    resource_id: int

    resource_type: ResourceType

    resource_name: str = Field(
        min_length=1
    )

    source_document_id: int

    source_title: str | None = None

    content: str = Field(
        min_length=1
    )

    allowed_skills: list[
        SkillReference
    ] = Field(
        min_length=1
    )


# ============================================================
# LLM이 반환할 Skill 하나
# ============================================================

class SkillMappingItem(BaseModel):
    """
    LLM이 추출한 하나의 Skill Mapping.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    # 반드시 CareerQuest Skill 사전의
    # canonical name을 반환해야 함
    skill_name: str = Field(
        min_length=1
    )

    relation_type: RelationType

    # 실제 원문에 존재하는 근거
    evidence_text: str = Field(
        min_length=1,
        max_length=1000,
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    # 선택적 설명
    reason: str | None = Field(
        default=None,
        max_length=500,
    )


# ============================================================
# LLM 전체 출력
# ============================================================

class SkillMappingOutput(BaseModel):
    """
    LLM이 최종 반환해야 하는 JSON 구조.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    resource_id: int

    source_document_id: int

    mappings: list[
        SkillMappingItem
    ] = Field(
        default_factory=list
    )

    # 원문에는 기술이 있는데
    # CareerQuest Skill 사전에는 없었던 경우
    #
    # 절대 자동으로 Skill을 생성하지 않고
    # 이곳에 보고만 한다.
    unmapped_skills: list[str] = Field(
        default_factory=list
    )


# ============================================================
# 문자열 정규화
# ============================================================

def normalize_text(
    value: str,
) -> str:

    return re.sub(
        r"\s+",
        " ",
        value.strip().lower(),
    )


# ============================================================
# 최종 검증
# ============================================================

def validate_mapping_output(
    request: SkillMappingInput,
    response: SkillMappingOutput,
) -> list[str]:
    """
    LLM 결과를 DB에 넣기 전에
    백엔드에서 반드시 수행하는 검증.

    반환값:
    - []        → 정상
    - ["..."]   → 오류 있음
    """

    errors: list[str] = []

    # --------------------------------------------------------
    # Resource 일치
    # --------------------------------------------------------

    if (
        request.resource_id
        != response.resource_id
    ):

        errors.append(
            "resource_id가 "
            "입력값과 다릅니다."
        )

    # --------------------------------------------------------
    # SourceDocument 일치
    # --------------------------------------------------------

    if (
        request.source_document_id
        != response.source_document_id
    ):

        errors.append(
            "source_document_id가 "
            "입력값과 다릅니다."
        )

    # --------------------------------------------------------
    # 허용된 Skill 목록
    # --------------------------------------------------------

    allowed_skill_names = {
        normalize_text(skill.name):
            skill.name

        for skill
        in request.allowed_skills
    }

    # --------------------------------------------------------
    # Resource 유형별 허용 관계
    # --------------------------------------------------------

    allowed_relations = (
        ALLOWED_RELATIONS_BY_RESOURCE[
            request.resource_type
        ]
    )

    # --------------------------------------------------------
    # 원문
    # --------------------------------------------------------

    normalized_content = (
        normalize_text(
            request.content
        )
    )

    # --------------------------------------------------------
    # 중복 확인
    # --------------------------------------------------------

    seen: set[
        tuple[str, RelationType]
    ] = set()

    # --------------------------------------------------------
    # Mapping 검증
    # --------------------------------------------------------

    for index, mapping in enumerate(
        response.mappings,
        start=1,
    ):

        normalized_skill = (
            normalize_text(
                mapping.skill_name
            )
        )

        # --------------------------------------------
        # Skill 사전에 존재?
        # --------------------------------------------

        if (
            normalized_skill
            not in allowed_skill_names
        ):

            errors.append(
                f"{index}번 mapping: "
                f"등록되지 않은 Skill "
                f"'{mapping.skill_name}'"
            )

        # --------------------------------------------
        # 관계 유형이 Resource에 적합?
        # --------------------------------------------

        if (
            mapping.relation_type
            not in allowed_relations
        ):

            errors.append(
                f"{index}번 mapping: "
                f"{request.resource_type.value}에는 "
                f"{mapping.relation_type.value} "
                f"관계를 사용할 수 없습니다."
            )

        # --------------------------------------------
        # Evidence가 실제 원문에 존재?
        # --------------------------------------------

        normalized_evidence = (
            normalize_text(
                mapping.evidence_text
            )
        )

        if (
            normalized_evidence
            not in normalized_content
        ):

            errors.append(
                f"{index}번 mapping: "
                f"evidence_text가 "
                f"원문에서 확인되지 않습니다."
            )

        # --------------------------------------------
        # 중복 Mapping?
        # --------------------------------------------

        mapping_key = (
            normalized_skill,
            mapping.relation_type,
        )

        if mapping_key in seen:

            errors.append(
                f"{index}번 mapping: "
                f"동일 Skill + relation_type "
                f"중복입니다."
            )

        seen.add(
            mapping_key
        )

    # --------------------------------------------------------
    # unmapped_skill이 이미 정식 Skill이면 오류
    # --------------------------------------------------------

    for skill_name in (
        response.unmapped_skills
    ):

        if (
            normalize_text(
                skill_name
            )
            in allowed_skill_names
        ):

            errors.append(
                f"unmapped_skills의 "
                f"'{skill_name}'은 이미 "
                f"Skill 사전에 존재합니다."
            )

    return errors