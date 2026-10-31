from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.database import engine
from src.db.models import (
    ResourceSkill,
    Skill,
)

from src.mapping.mapping_input_builder import (
    build_mapping_input,
)

from src.mapping.schemas import (
    SkillMappingOutput,
    normalize_text,
    validate_mapping_output,
)


# ============================================================
# LLM 결과 JSON 읽기
# ============================================================

def load_mapping_output(
    path: Path,
) -> SkillMappingOutput:

    if not path.exists():
        raise FileNotFoundError(
            f"LLM 결과 파일이 없습니다: {path}"
        )

    content = path.read_text(
        encoding="utf-8"
    )

    try:

        return (
            SkillMappingOutput
            .model_validate_json(
                content
            )
        )

    except ValidationError as error:

        print()
        print(
            "LLM Output JSON 형식 오류"
        )

        print(error)

        raise


# ============================================================
# Skill 조회
# ============================================================

def build_skill_lookup(
    session: Session,
) -> dict[str, Skill]:

    skills = list(
        session.scalars(
            select(
                Skill
            ).where(
                Skill.is_active
                == True  # noqa: E712
            )
        ).all()
    )

    return {
        normalize_text(
            skill.name
        ): skill

        for skill in skills
    }


# ============================================================
# 기존 ResourceSkill 조회
# ============================================================

def find_existing_mapping(
    session: Session,
    *,
    resource_id: int,
    skill_id: int,
    relation_type: str,
) -> ResourceSkill | None:

    return session.scalar(
        select(
            ResourceSkill
        ).where(
            ResourceSkill.resource_id
            == resource_id,

            ResourceSkill.skill_id
            == skill_id,

            ResourceSkill.relation_type
            == relation_type,
        )
    )


# ============================================================
# 기존 수동 검증 매핑 보호
# ============================================================

def is_protected_mapping(
    mapping: ResourceSkill,
) -> bool:
    """
    사람이 이미 검증한 매핑을
    LLM 결과가 자동으로 덮어쓰지 못하게 한다.

    예:
    MANUAL_DEMO + verified=True
    MANUAL + verified=True
    """

    if not mapping.verified:
        return False

    method = (
        mapping.mapping_method
        or ""
    ).upper()

    # 기존 LLM 결과를 사람이 verified 처리한 경우도
    # 보수적으로 보호한다.
    if mapping.verified:
        return True

    return method.startswith(
        "MANUAL"
    )


# ============================================================
# confidence → Decimal
# ============================================================

def to_decimal_confidence(
    value: float,
) -> Decimal:

    return Decimal(
        str(value)
    ).quantize(
        Decimal("0.00001")
    )


# ============================================================
# Mapping 하나 저장
# ============================================================

def write_mapping(
    session: Session,
    *,
    resource_id: int,
    source_document_id: int,
    skill: Skill,
    relation_type: str,
    evidence_text: str,
    confidence: float,
) -> str:
    """
    반환:
    CREATE
    UPDATE
    PROTECTED
    """

    existing = (
        find_existing_mapping(
            session,

            resource_id=(
                resource_id
            ),

            skill_id=(
                skill.skill_id
            ),

            relation_type=(
                relation_type
            ),
        )
    )

    # --------------------------------------------------------
    # 기존에 사람이 검증한 데이터가 있으면 보호
    # --------------------------------------------------------

    if (
        existing is not None
        and is_protected_mapping(
            existing
        )
    ):

        return "PROTECTED"

    # --------------------------------------------------------
    # 신규
    # --------------------------------------------------------

    if existing is None:

        mapping = ResourceSkill(
            resource_id=(
                resource_id
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
                "LLM"
            ),

            confidence=(
                to_decimal_confidence(
                    confidence
                )
            ),

            # LLM이 만들었으므로
            # 사람 검증 전에는 false
            verified=False,

            source_document_id=(
                source_document_id
            ),
        )

        session.add(
            mapping
        )

        session.flush()

        return "CREATE"

    # --------------------------------------------------------
    # 기존 자동 매핑 갱신
    # --------------------------------------------------------

    existing.evidence_text = (
        evidence_text
    )

    existing.mapping_method = (
        "LLM"
    )

    existing.confidence = (
        to_decimal_confidence(
            confidence
        )
    )

    existing.verified = False

    existing.source_document_id = (
        source_document_id
    )

    session.flush()

    return "UPDATE"


# ============================================================
# 전체 LLM 결과 저장
# ============================================================

def write_mapping_output(
    session: Session,
    response: SkillMappingOutput,
) -> dict:

    # --------------------------------------------------------
    # 현재 DB로부터 동일 Resource의
    # Mapping Input을 다시 생성한다.
    #
    # 즉 LLM이 받은 입력과 DB 상태를
    # 서버가 다시 확인하는 과정.
    # --------------------------------------------------------

    request = (
        build_mapping_input(
            session,
            response.resource_id,
        )
    )

    # --------------------------------------------------------
    # 핵심 검증
    # --------------------------------------------------------

    errors = (
        validate_mapping_output(
            request,
            response,
        )
    )

    if errors:

        print()
        print(
            "=" * 70
        )

        print(
            "LLM Mapping 검증 실패"
        )

        print(
            "=" * 70
        )

        for error in errors:

            print(
                "-",
                error,
            )

        raise ValueError(
            f"Mapping 검증 오류 "
            f"{len(errors)}개"
        )

    # --------------------------------------------------------
    # Skill lookup
    # --------------------------------------------------------

    skill_lookup = (
        build_skill_lookup(
            session
        )
    )

    stats = {
        "created": 0,
        "updated": 0,
        "protected": 0,
        "processed": 0,
    }

    # --------------------------------------------------------
    # 저장
    # --------------------------------------------------------

    for mapping in (
        response.mappings
    ):

        normalized_skill = (
            normalize_text(
                mapping.skill_name
            )
        )

        skill = (
            skill_lookup.get(
                normalized_skill
            )
        )

        # validate_mapping_output을 통과했다면
        # 일반적으로 발생하지 않아야 함.
        if skill is None:

            raise RuntimeError(
                "검증 후 Skill 조회 실패: "
                f"{mapping.skill_name}"
            )

        action = write_mapping(
            session,

            resource_id=(
                response.resource_id
            ),

            source_document_id=(
                response.source_document_id
            ),

            skill=(
                skill
            ),

            relation_type=(
                mapping
                .relation_type
                .value
            ),

            evidence_text=(
                mapping.evidence_text
            ),

            confidence=(
                mapping.confidence
            ),
        )

        stats[
            "processed"
        ] += 1

        if action == "CREATE":

            stats[
                "created"
            ] += 1

        elif action == "UPDATE":

            stats[
                "updated"
            ] += 1

        elif action == "PROTECTED":

            stats[
                "protected"
            ] += 1

        print(
            f"[{action}] "
            f"{mapping.skill_name} "
            f"→ "
            f"{mapping.relation_type.value}"
        )

    return stats


# ============================================================
# Main
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "CareerQuest "
            "LLM Skill Mapping Result Writer"
        )
    )

    parser.add_argument(
        "--file",
        required=True,
        help=(
            "LLM SkillMappingOutput "
            "JSON 파일 경로"
        ),
    )

    args = (
        parser.parse_args()
    )

    output_path = Path(
        args.file
    )

    response = (
        load_mapping_output(
            output_path
        )
    )

    print(
        "=" * 70
    )

    print(
        "CareerQuest "
        "LLM Skill Mapping Writer"
    )

    print(
        "=" * 70
    )

    print()

    print(
        "입력 파일:",
        output_path,
    )

    print(
        "Resource ID:",
        response.resource_id,
    )

    print(
        "SourceDocument ID:",
        response.source_document_id,
    )

    print(
        "Mapping 수:",
        len(
            response.mappings
        ),
    )

    print(
        "미등록 Skill 수:",
        len(
            response.unmapped_skills
        ),
    )

    with Session(
        engine
    ) as session:

        try:

            stats = (
                write_mapping_output(
                    session,
                    response,
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
        "LLM Skill Mapping 저장 완료"
    )

    print(
        "=" * 70
    )

    print()

    print(
        "처리:",
        stats[
            "processed"
        ],
    )

    print(
        "신규:",
        stats[
            "created"
        ],
    )

    print(
        "갱신:",
        stats[
            "updated"
        ],
    )

    print(
        "보호됨:",
        stats[
            "protected"
        ],
    )

    # --------------------------------------------------------
    # Skill 사전에 없는 후보
    # --------------------------------------------------------

    if (
        response.unmapped_skills
    ):

        print()
        print(
            "[미등록 Skill 후보]"
        )

        for skill_name in (
            response.unmapped_skills
        ):

            print(
                "-",
                skill_name,
            )

        print()
        print(
            "※ 자동으로 Skill 테이블에 "
            "추가하지 않습니다."
        )


if __name__ == "__main__":
    main()