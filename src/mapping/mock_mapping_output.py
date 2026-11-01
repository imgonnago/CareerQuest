from __future__ import annotations

import argparse
from pathlib import Path

from src.mapping.schemas import (
    SkillMappingInput,
    SkillMappingOutput,
    validate_mapping_output,
)


# ============================================================
# 기본 경로
# ============================================================

DEFAULT_INPUT_DIR = Path(
    "data/processed/mapping_inputs"
)

DEFAULT_OUTPUT_DIR = Path(
    "data/processed/mapping_outputs"
)


# ============================================================
# Input JSON 읽기
# ============================================================

def load_mapping_input(
    path: Path,
) -> SkillMappingInput:

    if not path.exists():
        raise FileNotFoundError(
            f"Mapping Input 파일이 없습니다: {path}"
        )

    content = path.read_text(
        encoding="utf-8"
    )

    return (
        SkillMappingInput
        .model_validate_json(
            content
        )
    )


# ============================================================
# 원문에서 정확한 근거 찾기
# ============================================================

def find_evidence_line(
    content: str,
    keywords: list[str],
    max_length: int = 900,
) -> str:
    """
    모든 keyword가 포함된 원문 구간을 찾되,
    evidence_text가 지나치게 길어지지 않도록
    최대 max_length자로 잘라 반환한다.

    반환되는 문자열은 실제 원문의 일부이므로
    이후 evidence 검증도 통과할 수 있다.
    """

    # --------------------------------------------------------
    # 1. 줄 단위로 먼저 검색
    # --------------------------------------------------------

    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip()
    ]

    for line in lines:

        lowered = (
            line.lower()
        )

        keyword_positions = []

        all_found = True

        for keyword in keywords:

            position = lowered.find(
                keyword.lower()
            )

            if position == -1:

                all_found = False
                break

            keyword_positions.append(
                (
                    position,
                    position
                    + len(keyword)
                )
            )

        if not all_found:
            continue

        # 짧은 줄이면 그대로 사용
        if len(line) <= max_length:

            return line

        # ----------------------------------------------------
        # 긴 Markdown 표 한 줄인 경우
        # keyword들이 있는 주변 부분만 잘라냄
        # ----------------------------------------------------

        start_keyword = min(
            start
            for start, _
            in keyword_positions
        )

        end_keyword = max(
            end
            for _, end
            in keyword_positions
        )

        keyword_span = (
            end_keyword
            - start_keyword
        )

        # keyword들 자체가 900자를 넘을 일은 거의 없지만
        # 방어적으로 처리
        if keyword_span >= max_length:

            return line[
                start_keyword:
                start_keyword + max_length
            ].strip()

        remaining = (
            max_length
            - keyword_span
        )

        left_padding = (
            remaining // 2
        )

        right_padding = (
            remaining
            - left_padding
        )

        start = max(
            0,
            start_keyword
            - left_padding,
        )

        end = min(
            len(line),
            end_keyword
            + right_padding,
        )

        # 끝쪽 길이가 부족하면
        # 남는 만큼 앞쪽을 더 포함
        if (
            end - start
            < max_length
        ):

            start = max(
                0,
                end - max_length,
            )

        # 앞쪽 길이가 부족하면
        # 뒤쪽을 더 포함
        if (
            end - start
            < max_length
        ):

            end = min(
                len(line),
                start + max_length,
            )

        return line[
            start:end
        ].strip()

    # --------------------------------------------------------
    # 2. 줄 하나에 같이 없으면
    # 전체 본문에서 가까운 범위를 확인
    # --------------------------------------------------------

    normalized_content = (
        " ".join(
            content.split()
        )
    )

    lowered_content = (
        normalized_content.lower()
    )

    positions = []

    for keyword in keywords:

        position = (
            lowered_content.find(
                keyword.lower()
            )
        )

        if position == -1:

            raise ValueError(
                "원문에서 근거를 찾지 못했습니다: "
                f"{keywords}"
            )

        positions.append(
            (
                position,
                position
                + len(keyword)
            )
        )

    start_keyword = min(
        start
        for start, _
        in positions
    )

    end_keyword = max(
        end
        for _, end
        in positions
    )

    keyword_span = (
        end_keyword
        - start_keyword
    )

    if keyword_span >= max_length:

        return normalized_content[
            start_keyword:
            start_keyword + max_length
        ].strip()

    remaining = (
        max_length
        - keyword_span
    )

    start = max(
        0,
        start_keyword
        - remaining // 2,
    )

    end = min(
        len(normalized_content),
        start + max_length,
    )

    return normalized_content[
        start:end
    ].strip()


# ============================================================
# Skill 존재 여부 확인
# ============================================================

def ensure_skill_exists(
    request: SkillMappingInput,
    skill_name: str,
) -> None:

    allowed_names = {
        skill.name.lower()
        for skill
        in request.allowed_skills
    }

    if (
        skill_name.lower()
        not in allowed_names
    ):
        raise ValueError(
            f"Skill 사전에 없는 Skill: "
            f"{skill_name}"
        )


# ============================================================
# Mock LLM Output 생성
# ============================================================

def build_mock_output(
    request: SkillMappingInput,
) -> SkillMappingOutput:
    """
    실제 LLM이 반환했다고 가정하는
    SkillMappingOutput을 생성한다.

    현대그린푸드 공고 기준 데모.
    """

    # --------------------------------------------------------
    # 필요한 Skill이 현재 Skill DB에 존재하는지 확인
    # --------------------------------------------------------

    required_skills = [
        "Python",
        "SQL",
        "AWS",
        "MLOps",
        "Cloud",
        "LLM",
        "RAG",
    ]

    for skill_name in required_skills:

        ensure_skill_exists(
            request,
            skill_name,
        )

    # --------------------------------------------------------
    # 실제 원문에서 evidence 가져오기
    # --------------------------------------------------------

    python_sql_evidence = (
        find_evidence_line(
            request.content,
            [
                "SQL",
                "Python",
            ],
        )
    )

    aws_mlops_evidence = (
        find_evidence_line(
            request.content,
            [
                "AWS",
                "MLOps",
            ],
        )
    )

    llm_rag_evidence = (
        find_evidence_line(
            request.content,
            [
                "LLM",
                "RAG",
            ],
        )
    )

    # --------------------------------------------------------
    # LLM Output이라고 가정
    # --------------------------------------------------------

    response = SkillMappingOutput(
        resource_id=(
            request.resource_id
        ),

        source_document_id=(
            request.source_document_id
        ),

        mappings=[
            {
                "skill_name": "Python",

                "relation_type": (
                    "REQUIRED"
                ),

                "evidence_text": (
                    python_sql_evidence
                ),

                "confidence": 0.99,

                "reason": (
                    "자격요건에 Python 활용 능력이 "
                    "직접 명시되어 있음"
                ),
            },

            {
                "skill_name": "SQL",

                "relation_type": (
                    "REQUIRED"
                ),

                "evidence_text": (
                    python_sql_evidence
                ),

                "confidence": 0.99,

                "reason": (
                    "자격요건에 SQL 활용 능력이 "
                    "직접 명시되어 있음"
                ),
            },

            {
                "skill_name": "AWS",

                "relation_type": (
                    "REQUIRED"
                ),

                "evidence_text": (
                    aws_mlops_evidence
                ),

                "confidence": 0.98,

                "reason": (
                    "AWS 클라우드 환경 경험이 "
                    "자격요건에 명시되어 있음"
                ),
            },

            {
                "skill_name": "MLOps",

                "relation_type": (
                    "REQUIRED"
                ),

                "evidence_text": (
                    aws_mlops_evidence
                ),

                "confidence": 0.99,

                "reason": (
                    "MLOps 파이프라인 설계 또는 "
                    "운영 경험이 자격요건에 명시됨"
                ),
            },

            # 기존 MANUAL_DEMO에는 없는 Skill.
            # writer의 신규 INSERT 동작 확인용.
            {
                "skill_name": "Cloud",

                "relation_type": (
                    "REQUIRED"
                ),

                "evidence_text": (
                    aws_mlops_evidence
                ),

                "confidence": 0.95,

                "reason": (
                    "AWS 등 클라우드 환경에서의 "
                    "파이프라인 경험을 요구함"
                ),
            },

            {
                "skill_name": "LLM",

                "relation_type": (
                    "PREFERRED"
                ),

                "evidence_text": (
                    llm_rag_evidence
                ),

                "confidence": 0.98,

                "reason": (
                    "LLM 연계 경험이 "
                    "우대사항에 직접 명시됨"
                ),
            },

            {
                "skill_name": "RAG",

                "relation_type": (
                    "PREFERRED"
                ),

                "evidence_text": (
                    llm_rag_evidence
                ),

                "confidence": 0.99,

                "reason": (
                    "RAG 구축 경험이 "
                    "우대사항에 직접 명시됨"
                ),
            },
        ],

        # 실제 Skill DB에 없는 기술이 발견됐다고
        # 가정하는 예시.
        unmapped_skills=[
            "LangChain",
            "MCP",
        ],
    )

    return response


# ============================================================
# 저장
# ============================================================

def save_output(
    response: SkillMappingOutput,
) -> Path:

    DEFAULT_OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path = (
        DEFAULT_OUTPUT_DIR
        / (
            f"resource_"
            f"{response.resource_id}"
            f"_mapping_output.json"
        )
    )

    output_path.write_text(
        response.model_dump_json(
            indent=2,
        ),
        encoding="utf-8",
    )

    return output_path


# ============================================================
# Main
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "CareerQuest Mock "
            "LLM Skill Mapping Output Generator"
        )
    )

    parser.add_argument(
        "--file",
        default=None,
        help=(
            "mapping_input JSON 파일 경로. "
            "생략하면 resource_95 파일 사용"
        ),
    )

    args = (
        parser.parse_args()
    )

    # --------------------------------------------------------
    # 기본 현대그린푸드 Input
    # --------------------------------------------------------

    if args.file:

        input_path = Path(
            args.file
        )

    else:

        input_path = (
            DEFAULT_INPUT_DIR
            / "resource_95_mapping_input.json"
        )

    print(
        "=" * 70
    )

    print(
        "CareerQuest Mock LLM"
    )

    print(
        "=" * 70
    )

    print()

    print(
        "Input:",
        input_path,
    )

    # --------------------------------------------------------
    # 실제 Input 읽기
    # --------------------------------------------------------

    request = (
        load_mapping_input(
            input_path
        )
    )

    # --------------------------------------------------------
    # Mock LLM 실행
    # --------------------------------------------------------

    response = (
        build_mock_output(
            request
        )
    )

    # --------------------------------------------------------
    # 실제 writer에 넘기기 전에
    # 계약 검증까지 수행
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
            "Mock Output 검증 실패"
        )

        for error in errors:

            print(
                "-",
                error,
            )

        raise ValueError(
            f"검증 오류 {len(errors)}개"
        )

    print()
    print(
        "Mock Output 검증 성공"
    )

    # --------------------------------------------------------
    # JSON 저장
    # --------------------------------------------------------

    output_path = (
        save_output(
            response
        )
    )

    print()

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
        "미등록 Skill:",
        response.unmapped_skills,
    )

    print()

    print(
        "Output:",
        output_path,
    )

    print()

    print(
        "Mock LLM Output 생성 완료"
    )


if __name__ == "__main__":
    main()