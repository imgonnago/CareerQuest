from src.mapping.schemas import (
    ResourceType,
    SkillMappingInput,
    SkillMappingOutput,
    SkillReference,
    validate_mapping_output,
)


def main() -> None:

    # ========================================================
    # LLM에게 전달한다고 가정하는 입력
    # ========================================================

    request = SkillMappingInput(
        resource_id=100,

        resource_type=(
            ResourceType.JOB
        ),

        resource_name=(
            "현대그린푸드 "
            "AI 데이터 엔지니어"
        ),

        source_document_id=500,

        source_title=(
            "현대그린푸드 "
            "AI 데이터 엔지니어 채용"
        ),

        content=(
            "자격요건: "
            "SQL 및 Python을 능숙하게 활용하여 "
            "데이터 전처리, 자동화, "
            "파이프라인 구축을 직접 수행할 수 있는 분. "
            "AWS 등 클라우드 환경에서 "
            "MLOps 기반 파이프라인 설계 또는 "
            "운영 경험이 있으신 분. "
            "우대사항: 사내 데이터와 LLM을 "
            "연계하는 RAG 구축 경험."
        ),

        allowed_skills=[
            SkillReference(
                skill_id=1,
                name="Python",
                aliases=[
                    "파이썬",
                ],
            ),

            SkillReference(
                skill_id=2,
                name="SQL",
                aliases=[],
            ),

            SkillReference(
                skill_id=3,
                name="AWS",
                aliases=[
                    "Amazon Web Services",
                ],
            ),

            SkillReference(
                skill_id=4,
                name="MLOps",
                aliases=[
                    "ML Ops",
                ],
            ),

            SkillReference(
                skill_id=5,
                name="LLM",
                aliases=[
                    "Large Language Model",
                ],
            ),

            SkillReference(
                skill_id=6,
                name="RAG",
                aliases=[
                    "Retrieval Augmented Generation",
                ],
            ),
        ],
    )

    # ========================================================
    # LLM이 반환했다고 가정하는 JSON
    # ========================================================

    response = SkillMappingOutput(
        resource_id=100,

        source_document_id=500,

        mappings=[
            {
                "skill_name": "Python",

                "relation_type": (
                    "REQUIRED"
                ),

                "evidence_text": (
                    "SQL 및 Python을 능숙하게 활용하여 "
                    "데이터 전처리, 자동화, "
                    "파이프라인 구축을 직접 수행할 수 있는 분."
                ),

                "confidence": 0.99,

                "reason": (
                    "자격요건에 명시됨"
                ),
            },

            {
                "skill_name": "SQL",

                "relation_type": (
                    "REQUIRED"
                ),

                "evidence_text": (
                    "SQL 및 Python을 능숙하게 활용하여 "
                    "데이터 전처리, 자동화, "
                    "파이프라인 구축을 직접 수행할 수 있는 분."
                ),

                "confidence": 0.99,

                "reason": (
                    "자격요건에 명시됨"
                ),
            },

            {
                "skill_name": "RAG",

                "relation_type": (
                    "PREFERRED"
                ),

                "evidence_text": (
                    "우대사항: 사내 데이터와 LLM을 "
                    "연계하는 RAG 구축 경험."
                ),

                "confidence": 0.98,

                "reason": (
                    "우대사항에 명시됨"
                ),
            },
        ],

        # 예를 들어 원문에 LangChain이 있었지만
        # 우리 Skill 사전에 없다면 여기로
        unmapped_skills=[
            "LangChain",
        ],
    )

    # ========================================================
    # 검증
    # ========================================================

    errors = (
        validate_mapping_output(
            request,
            response,
        )
    )

    print(
        "=" * 70
    )

    print(
        "CareerQuest "
        "Skill Mapping Contract Test"
    )

    print(
        "=" * 70
    )

    print()

    if not errors:

        print(
            "검증 성공"
        )

        print(
            "→ 이 결과는 DB 저장 단계로 "
            "넘겨도 됩니다."
        )

    else:

        print(
            "검증 실패"
        )

        for error in errors:

            print(
                "-",
                error,
            )

    print()

    print(
        "LLM Output JSON:"
    )

    print(
        response.model_dump_json(
            indent=2,
        )
    )


if __name__ == "__main__":
    main()