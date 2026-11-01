from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from src.mapping.schemas import (
    RelationType,
    SkillMappingInput,
    SkillMappingItem,
    SkillMappingOutput,
)


# ============================================================
# Mock 목적
#
# 실제 AI가 아니다.
#
# 팀원 모델 연동 전에:
#
# Input JSON
# -> Output JSON
# -> Writer
#
# 전체 인터페이스가 정상인지 확인하기 위한
# 단순 exact-match 테스트 도구.
# ============================================================


GENERIC_TERMS = {
    "ai",
    "ml",
    "sw",
    "it",
    "db",
    "c",
    "r",
}


MAX_MAPPINGS_PER_RESOURCE = 5


# ============================================================
# JSON
# ============================================================

def load_json(
    path: Path,
) -> Any:

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:

        return json.load(
            file
        )


def load_inputs(
    path: Path,
) -> list[
    SkillMappingInput
]:

    payload = (
        load_json(
            path
        )
    )


    if not isinstance(
        payload,
        list,
    ):

        payload = [
            payload
        ]


    return [
        SkillMappingInput.model_validate(
            item
        )

        for item
        in payload
    ]


# ============================================================
# 직접 문자열 찾기
# ============================================================

def find_exact_match(
    content: str,
    term: str,
) -> str | None:

    term = (
        term.strip()
    )


    if not term:

        return None


    if (
        term.lower()
        in GENERIC_TERMS
    ):

        return None


    if len(
        term
    ) < 3:

        return None


    match = re.search(
        re.escape(
            term
        ),

        content,

        flags=re.IGNORECASE,
    )


    if match is None:

        return None


    # evidence는 실제 content substring
    return match.group(
        0
    )


# ============================================================
# Mock output
# ============================================================

def make_mock_output(
    mapping_input: SkillMappingInput,
) -> SkillMappingOutput:

    mappings: list[
        SkillMappingItem
    ] = []


    seen_skill_names: set[
        str
    ] = set()


    for skill in (
        mapping_input.allowed_skills
    ):

        terms = [
            skill.name,
            *skill.aliases,
        ]


        matched_evidence = None


        for term in terms:

            matched_evidence = (
                find_exact_match(
                    mapping_input.content,
                    term,
                )
            )


            if (
                matched_evidence
                is not None
            ):

                break


        if (
            matched_evidence
            is None
        ):

            continue


        normalized_skill = (
            skill.name
            .strip()
            .lower()
        )


        if (
            normalized_skill
            in seen_skill_names
        ):

            continue


        seen_skill_names.add(
            normalized_skill
        )


        # Mock에서는 의미를 과장하지 않고
        # 모든 Resource에 허용되는 RELATED 사용
        mappings.append(
            SkillMappingItem(
                skill_name=(
                    skill.name
                ),

                relation_type=(
                    RelationType.RELATED
                ),

                evidence_text=(
                    matched_evidence
                ),

                confidence=0.99,

                reason=(
                    "Mock exact string match"
                ),
            )
        )


        if (
            len(
                mappings
            )
            >= MAX_MAPPINGS_PER_RESOURCE
        ):

            break


    return SkillMappingOutput(
        resource_id=(
            mapping_input.resource_id
        ),

        source_document_id=(
            mapping_input
            .source_document_id
        ),

        mappings=(
            mappings
        ),

        unmapped_skills=[],
    )


# ============================================================
# CLI
# ============================================================

def parse_args(
) -> argparse.Namespace:

    parser = (
        argparse.ArgumentParser()
    )


    parser.add_argument(
        "--input",

        required=True,
    )


    parser.add_argument(
        "--output",

        required=True,
    )


    return (
        parser.parse_args()
    )


# ============================================================
# main
# ============================================================

def main(
) -> None:

    args = (
        parse_args()
    )


    input_path = Path(
        args.input
    )


    output_path = Path(
        args.output
    )


    inputs = (
        load_inputs(
            input_path
        )
    )


    outputs = [
        make_mock_output(
            item
        )

        for item
        in inputs
    ]


    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )


    payload = [
        output.model_dump(
            mode="json"
        )

        for output
        in outputs
    ]


    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )


    mapping_count = sum(
        len(
            output.mappings
        )

        for output
        in outputs
    )


    print(
        "=" * 72
    )

    print(
        "CareerQuest Mock Model Output"
    )

    print(
        "=" * 72
    )


    print(
        (
            f"Resources: "
            f"{len(outputs)}"
        )
    )


    print(
        (
            f"Mappings : "
            f"{mapping_count}"
        )
    )


    print(
        (
            f"Output   : "
            f"{output_path}"
        )
    )


    print(
        "=" * 72
    )


if __name__ == "__main__":
    main()