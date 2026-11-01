from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text

from src.db.database import engine
from src.mapping.schemas import (
    SKILL_MAPPING_CONTRACT_VERSION,
    ResourceType,
    SkillMappingInput,
    SkillMappingOutput,
    validate_mapping_output,
)


# ============================================================
# 기본 설정
# ============================================================

DEFAULT_MAPPING_METHOD = "team_model_v1"

DEFAULT_REPORT_PATH = Path(
    "model_exchange/write_report.json"
)


# Resource 종류별 최소 confidence
MIN_CONFIDENCE_BY_TYPE = {
    ResourceType.JOB: 0.80,
    ResourceType.CERTIFICATE: 0.85,
    ResourceType.COURSE: 0.90,
    ResourceType.PROGRAM: 0.85,
}


# ============================================================
# 문자열 처리
# ============================================================

def clean_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    return str(
        value
    ).strip()


def normalize_evidence(
    value: str,
) -> str:
    """
    evidence_text가 실제 원문에 존재하는지
    확인하기 위한 비교용 정규화.
    """

    value = clean_text(
        value
    ).lower()

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


# ============================================================
# JSON 로딩
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


# ============================================================
# Input JSON 파싱
# ============================================================

def parse_inputs(
    payload: Any,
) -> list[SkillMappingInput]:

    # export_mapping_inputs.py 결과
    if isinstance(
        payload,
        list,
    ):

        raw_items = (
            payload
        )


    # 혹시 wrapper 형태인 경우
    elif isinstance(
        payload,
        dict,
    ):

        if "inputs" in payload:

            raw_items = (
                payload[
                    "inputs"
                ]
            )

        else:

            raw_items = [
                payload
            ]


    else:

        raise ValueError(
            "Input JSON 형식이 올바르지 않습니다."
        )


    return [
        SkillMappingInput.model_validate(
            item
        )

        for item
        in raw_items
    ]


# ============================================================
# Output JSON 파싱
#
# 팀원이 어떤 wrapper를 쓰더라도
# 조금 유연하게 받을 수 있게 함.
# ============================================================

def parse_outputs(
    payload: Any,
) -> list[SkillMappingOutput]:

    if isinstance(
        payload,
        list,
    ):

        raw_items = (
            payload
        )


    elif isinstance(
        payload,
        dict,
    ):

        if "outputs" in payload:

            raw_items = (
                payload[
                    "outputs"
                ]
            )

        elif "results" in payload:

            raw_items = (
                payload[
                    "results"
                ]
            )

        elif "resource_id" in payload:

            raw_items = [
                payload
            ]

        else:

            raise ValueError(
                (
                    "Output JSON에서 "
                    "outputs/results/resource_id를 "
                    "찾지 못했습니다."
                )
            )


    else:

        raise ValueError(
            "Output JSON 형식이 올바르지 않습니다."
        )


    return [
        SkillMappingOutput.model_validate(
            item
        )

        for item
        in raw_items
    ]


# ============================================================
# Input 인덱스
# ============================================================

def index_inputs(
    inputs: list[
        SkillMappingInput
    ],
) -> dict[
    int,
    SkillMappingInput
]:

    result: dict[
        int,
        SkillMappingInput
    ] = {}


    for item in inputs:

        if (
            item.resource_id
            in result
        ):

            raise ValueError(
                (
                    "Input에 중복 resource_id: "
                    f"{item.resource_id}"
                )
            )


        result[
            item.resource_id
        ] = item


    return result


# ============================================================
# DB 상태 검증
# ============================================================

def validate_resource_in_db(
    mapping_input: SkillMappingInput,
) -> None:

    sql = text(
        """
        SELECT
            resource_type

        FROM resource

        WHERE resource_id = :resource_id
        """
    )


    with engine.connect() as conn:

        row = conn.execute(
            sql,

            {
                "resource_id":
                    mapping_input.resource_id,
            },
        ).mappings().first()


    if row is None:

        raise ValueError(
            (
                "DB에 존재하지 않는 resource_id: "
                f"{mapping_input.resource_id}"
            )
        )


    if (
        row[
            "resource_type"
        ]
        != mapping_input.resource_type.value
    ):

        raise ValueError(
            (
                "resource_type 불일치: "
                f"resource_id="
                f"{mapping_input.resource_id}, "
                f"DB={row['resource_type']}, "
                f"input="
                f"{mapping_input.resource_type.value}"
            )
        )


# ============================================================
# Skill DB 확인
# ============================================================

def validate_skill_in_db(
    skill_id: int,
) -> None:

    sql = text(
        """
        SELECT
            skill_id

        FROM skill

        WHERE skill_id = :skill_id
          AND is_active = TRUE
        """
    )


    with engine.connect() as conn:

        found = conn.execute(
            sql,

            {
                "skill_id":
                    skill_id,
            },
        ).scalar()


    if found is None:

        raise ValueError(
            (
                "DB에 없는 또는 비활성 Skill: "
                f"{skill_id}"
            )
        )


# ============================================================
# SourceDocument 확인
# ============================================================

def validate_source_document(
    source_document_id: int | None,
) -> None:

    if (
        source_document_id
        is None
    ):

        return


    sql = text(
        """
        SELECT
            source_document_id

        FROM source_document

        WHERE source_document_id =
            :source_document_id
        """
    )


    with engine.connect() as conn:

        found = conn.execute(
            sql,

            {
                "source_document_id":
                    source_document_id,
            },
        ).scalar()


    if found is None:

        raise ValueError(
            (
                "DB에 없는 source_document_id: "
                f"{source_document_id}"
            )
        )


# ============================================================
# Canonical Skill name -> skill_id
# ============================================================

def build_allowed_skill_map(
    mapping_input: SkillMappingInput,
) -> dict[str, int]:

    result: dict[
        str,
        int
    ] = {}


    for skill in (
        mapping_input.allowed_skills
    ):

        normalized_name = (
            skill.name
            .strip()
            .lower()
        )


        if (
            normalized_name
            in result
        ):

            raise ValueError(
                (
                    "allowed_skills에 "
                    "중복 Skill name: "
                    f"{skill.name}"
                )
            )


        result[
            normalized_name
        ] = skill.skill_id


    return result


# ============================================================
# Output 전체 검증
#
# DB 저장 전에 전부 검증한다.
# 하나라도 치명적인 오류가 있으면
# 아무것도 저장하지 않는다.
# ============================================================

def prepare_mappings(
    inputs: list[
        SkillMappingInput
    ],

    outputs: list[
        SkillMappingOutput
    ],

    mapping_method: str,

    min_confidence_override: (
        float | None
    ),
) -> tuple[
    list[dict],
    list[dict],
]:

    input_by_id = (
        index_inputs(
            inputs
        )
    )


    # --------------------------------------------------------
    # Output resource 중복 검사
    # --------------------------------------------------------

    output_ids: list[int] = [
        output.resource_id

        for output
        in outputs
    ]


    if (
        len(
            output_ids
        )
        != len(
            set(
                output_ids
            )
        )
    ):

        raise ValueError(
            "Output에 중복 resource_id가 있습니다."
        )


    input_ids = set(
        input_by_id
    )


    output_id_set = set(
        output_ids
    )


    # --------------------------------------------------------
    # 모든 input에 output 하나씩 존재해야 함
    #
    # Skill이 없더라도 mappings=[] 출력
    # --------------------------------------------------------

    missing_outputs = (
        input_ids
        - output_id_set
    )


    unknown_outputs = (
        output_id_set
        - input_ids
    )


    if missing_outputs:

        raise ValueError(
            (
                "모델 Output이 누락된 Resource: "
                f"{sorted(missing_outputs)}"
            )
        )


    if unknown_outputs:

        raise ValueError(
            (
                "Input에 없는 resource_id가 "
                "Output에 존재: "
                f"{sorted(unknown_outputs)}"
            )
        )


    prepared: list[
        dict
    ] = []


    skipped: list[
        dict
    ] = []


    checked_skill_ids: set[
        int
    ] = set()


    checked_source_documents: set[
        int
    ] = set()


    # --------------------------------------------------------
    # Resource DB 검증
    # --------------------------------------------------------

    for mapping_input in inputs:

        if (
            mapping_input.contract_version
            != SKILL_MAPPING_CONTRACT_VERSION
        ):

            raise ValueError(
                (
                    "Input contract_version 불일치: "
                    f"{mapping_input.contract_version}"
                )
            )


        validate_resource_in_db(
            mapping_input
        )


    # --------------------------------------------------------
    # Output 검증
    # --------------------------------------------------------

    for output in outputs:

        mapping_input = (
            input_by_id[
                output.resource_id
            ]
        )


        if (
            output.contract_version
            != SKILL_MAPPING_CONTRACT_VERSION
        ):

            raise ValueError(
                (
                    "Output contract_version 불일치: "
                    f"resource_id="
                    f"{output.resource_id}, "
                    f"version="
                    f"{output.contract_version}"
                )
            )


        # schemas.py 내부 검증
        validate_mapping_output(
            mapping_input,
            output,
        )


        allowed_skill_map = (
            build_allowed_skill_map(
                mapping_input
            )
        )


        source_document_id = (
            output.source_document_id

            if (
                output.source_document_id
                is not None
            )

            else (
                mapping_input
                .source_document_id
            )
        )


        if (
            source_document_id
            is not None
            and source_document_id
            not in checked_source_documents
        ):

            validate_source_document(
                source_document_id
            )


            checked_source_documents.add(
                source_document_id
            )


        # ----------------------------------------------------
        # Resource별 confidence 기준
        # ----------------------------------------------------

        if (
            min_confidence_override
            is not None
        ):

            min_confidence = (
                min_confidence_override
            )

        else:

            min_confidence = (
                MIN_CONFIDENCE_BY_TYPE[
                    mapping_input.resource_type
                ]
            )


        normalized_content = (
            normalize_evidence(
                mapping_input.content
            )
        )


        for mapping in (
            output.mappings
        ):

            skill_name_key = (
                mapping.skill_name
                .strip()
                .lower()
            )


            skill_id = (
                allowed_skill_map[
                    skill_name_key
                ]
            )


            if (
                skill_id
                not in checked_skill_ids
            ):

                validate_skill_in_db(
                    skill_id
                )


                checked_skill_ids.add(
                    skill_id
                )


            # ------------------------------------------------
            # confidence 미달
            # ------------------------------------------------

            if (
                mapping.confidence
                < min_confidence
            ):

                skipped.append(
                    {
                        "resource_id":
                            output.resource_id,

                        "resource_name":
                            mapping_input.resource_name,

                        "skill_name":
                            mapping.skill_name,

                        "reason":
                            (
                                "LOW_CONFIDENCE"
                            ),

                        "confidence":
                            mapping.confidence,

                        "minimum":
                            min_confidence,
                    }
                )

                continue


            # ------------------------------------------------
            # Evidence 검증
            #
            # 모델이 지어낸 문장이 아니라
            # 실제 input.content의 substring이어야 함.
            # ------------------------------------------------

            normalized_evidence = (
                normalize_evidence(
                    mapping.evidence_text
                )
            )


            if not normalized_evidence:

                raise ValueError(
                    (
                        "빈 evidence_text: "
                        f"resource_id="
                        f"{output.resource_id}, "
                        f"skill="
                        f"{mapping.skill_name}"
                    )
                )


            if (
                normalized_evidence
                not in normalized_content
            ):

                raise ValueError(
                    (
                        "evidence_text가 실제 원문에 "
                        "존재하지 않습니다.\n"
                        f"resource_id="
                        f"{output.resource_id}\n"
                        f"skill="
                        f"{mapping.skill_name}\n"
                        f"evidence="
                        f"{mapping.evidence_text}"
                    )
                )


            prepared.append(
                {
                    "resource_id":
                        output.resource_id,

                    "resource_type":
                        mapping_input
                        .resource_type
                        .value,

                    "resource_name":
                        mapping_input.resource_name,

                    "skill_id":
                        skill_id,

                    "skill_name":
                        mapping.skill_name,

                    "relation_type":
                        mapping
                        .relation_type
                        .value,

                    "evidence_text":
                        mapping.evidence_text,

                    "confidence":
                        mapping.confidence,

                    "reason":
                        mapping.reason,

                    "source_document_id":
                        source_document_id,

                    "mapping_method":
                        mapping_method,
                }
            )


        # ----------------------------------------------------
        # Skill DB에 없는 기술은 report에만 남김
        # ----------------------------------------------------

        for unmapped_skill in (
            output.unmapped_skills
        ):

            skipped.append(
                {
                    "resource_id":
                        output.resource_id,

                    "resource_name":
                        mapping_input.resource_name,

                    "skill_name":
                        unmapped_skill,

                    "reason":
                        "UNMAPPED_SKILL",

                    "confidence":
                        None,

                    "minimum":
                        None,
                }
            )


    return (
        prepared,
        skipped,
    )


# ============================================================
# 기존 ResourceSkill 조회
# ============================================================

def get_existing_mapping(
    resource_id: int,
    skill_id: int,
    relation_type: str,
) -> dict | None:

    sql = text(
        """
        SELECT
            resource_skill_id,
            mapping_method,
            confidence,
            verified

        FROM resource_skill

        WHERE resource_id = :resource_id
          AND skill_id = :skill_id
          AND relation_type = :relation_type
        """
    )


    with engine.connect() as conn:

        row = conn.execute(
            sql,

            {
                "resource_id":
                    resource_id,

                "skill_id":
                    skill_id,

                "relation_type":
                    relation_type,
            },
        ).mappings().first()


    if row is None:

        return None


    return dict(
        row
    )


# ============================================================
# DB 쓰기 계획
#
# 기존 데이터 보호 규칙:
#
# 1. verified=True -> 절대 덮어쓰지 않음
# 2. 다른 mapping_method -> 덮어쓰지 않음
# 3. 같은 mapping_method -> UPDATE 가능
# 4. 없으면 INSERT
# ============================================================

def build_write_plan(
    prepared: list[dict],
    mapping_method: str,
) -> list[dict]:

    plan: list[
        dict
    ] = []


    for item in prepared:

        existing = (
            get_existing_mapping(
                resource_id=(
                    item[
                        "resource_id"
                    ]
                ),

                skill_id=(
                    item[
                        "skill_id"
                    ]
                ),

                relation_type=(
                    item[
                        "relation_type"
                    ]
                ),
            )
        )


        if existing is None:

            action = (
                "INSERT"
            )


        elif existing[
            "verified"
        ]:

            action = (
                "SKIP_VERIFIED"
            )


        elif (
            existing[
                "mapping_method"
            ]
            != mapping_method
        ):

            action = (
                "SKIP_OTHER_METHOD"
            )


        else:

            action = (
                "UPDATE"
            )


        plan_item = dict(
            item
        )


        plan_item[
            "action"
        ] = action


        plan_item[
            "existing_resource_skill_id"
        ] = (
            existing[
                "resource_skill_id"
            ]

            if existing

            else None
        )


        plan.append(
            plan_item
        )


    return plan


# ============================================================
# DB 저장
# ============================================================

def apply_write_plan(
    plan: list[dict],
) -> dict:

    insert_sql = text(
        """
        INSERT INTO resource_skill (
            resource_id,
            skill_id,
            relation_type,
            evidence_text,
            mapping_method,
            confidence,
            verified,
            source_document_id
        )

        VALUES (
            :resource_id,
            :skill_id,
            :relation_type,
            :evidence_text,
            :mapping_method,
            :confidence,
            FALSE,
            :source_document_id
        )
        """
    )


    update_sql = text(
        """
        UPDATE resource_skill

        SET
            evidence_text =
                :evidence_text,

            confidence =
                :confidence,

            source_document_id =
                :source_document_id,

            updated_at =
                NOW()

        WHERE resource_skill_id =
            :resource_skill_id

          AND verified = FALSE

          AND mapping_method =
            :mapping_method
        """
    )


    counts = {
        "INSERT":
            0,

        "UPDATE":
            0,

        "SKIP_VERIFIED":
            0,

        "SKIP_OTHER_METHOD":
            0,
    }


    with engine.begin() as conn:

        for item in plan:

            action = (
                item[
                    "action"
                ]
            )


            if action == "INSERT":

                conn.execute(
                    insert_sql,

                    {
                        "resource_id":
                            item[
                                "resource_id"
                            ],

                        "skill_id":
                            item[
                                "skill_id"
                            ],

                        "relation_type":
                            item[
                                "relation_type"
                            ],

                        "evidence_text":
                            item[
                                "evidence_text"
                            ],

                        "mapping_method":
                            item[
                                "mapping_method"
                            ],

                        "confidence":
                            item[
                                "confidence"
                            ],

                        "source_document_id":
                            item[
                                "source_document_id"
                            ],
                    },
                )


            elif action == "UPDATE":

                conn.execute(
                    update_sql,

                    {
                        "resource_skill_id":
                            item[
                                "existing_resource_skill_id"
                            ],

                        "evidence_text":
                            item[
                                "evidence_text"
                            ],

                        "confidence":
                            item[
                                "confidence"
                            ],

                        "source_document_id":
                            item[
                                "source_document_id"
                            ],

                        "mapping_method":
                            item[
                                "mapping_method"
                            ],
                    },
                )


            counts[
                action
            ] += 1


    return counts


# ============================================================
# 콘솔 출력
# ============================================================

def print_plan(
    plan: list[dict],
    skipped: list[dict],
) -> None:

    print(
        "\n"
        + "=" * 80
    )

    print(
        "RESOURCE_SKILL WRITE PLAN"
    )

    print(
        "=" * 80
    )


    if not plan:

        print(
            "\n저장 후보 없음"
        )


    for item in plan:

        print(
            (
                f"\n[{item['action']}] "
                f"{item['resource_name']}"
            )
        )


        print(
            (
                f"  -> "
                f"{item['skill_name']} "
                f"| relation="
                f"{item['relation_type']} "
                f"| confidence="
                f"{item['confidence']:.2f}"
            )
        )


        print(
            (
                f"     evidence: "
                f"{item['evidence_text']}"
            )
        )


        if item[
            "reason"
        ]:

            print(
                (
                    f"     model reason: "
                    f"{item['reason']}"
                )
            )


    if skipped:

        print(
            "\n"
            + "-" * 80
        )

        print(
            "SKIPPED / UNMAPPED"
        )

        print(
            "-" * 80
        )


        for item in skipped:

            print(
                (
                    f"{item['resource_name']} "
                    f"-> "
                    f"{item['skill_name']} "
                    f"| "
                    f"{item['reason']}"
                )
            )


    print(
        "\n"
        + "-" * 80
    )


    action_count: dict[
        str,
        int
    ] = {}


    for item in plan:

        action = (
            item[
                "action"
            ]
        )


        action_count[
            action
        ] = (
            action_count.get(
                action,
                0,
            )
            + 1
        )


    print(
        (
            f"INSERT: "
            f"{action_count.get('INSERT', 0)}"
        )
    )

    print(
        (
            f"UPDATE: "
            f"{action_count.get('UPDATE', 0)}"
        )
    )

    print(
        (
            "SKIP_VERIFIED: "
            f"{action_count.get('SKIP_VERIFIED', 0)}"
        )
    )

    print(
        (
            "SKIP_OTHER_METHOD: "
            f"{action_count.get('SKIP_OTHER_METHOD', 0)}"
        )
    )

    print(
        (
            f"LOW/UNMAPPED: "
            f"{len(skipped)}"
        )
    )


# ============================================================
# Report JSON
# ============================================================

def save_report(
    path: Path,

    input_path: Path,

    output_path: Path,

    mapping_method: str,

    save_mode: bool,

    plan: list[dict],

    skipped: list[dict],

    applied_counts: dict | None,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )


    report = {
        "input_file":
            str(
                input_path
            ),

        "output_file":
            str(
                output_path
            ),

        "mapping_method":
            mapping_method,

        "save_mode":
            save_mode,

        "plan":
            plan,

        "skipped":
            skipped,

        "applied_counts":
            applied_counts,
    }


    with path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            report,
            file,
            ensure_ascii=False,
            indent=2,
            default=str,
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

        help=(
            "export_mapping_inputs.py로 "
            "생성한 JSON"
        ),
    )


    parser.add_argument(
        "--output",

        required=True,

        help=(
            "팀원 모델이 반환한 "
            "SkillMappingOutput JSON"
        ),
    )


    parser.add_argument(
        "--save",

        action="store_true",

        help=(
            "검증 통과 결과를 "
            "RESOURCE_SKILL에 저장"
        ),
    )


    parser.add_argument(
        "--mapping-method",

        default=(
            DEFAULT_MAPPING_METHOD
        ),
    )


    parser.add_argument(
        "--min-confidence",

        type=float,

        default=None,

        help=(
            "Resource 종류별 기본값 대신 "
            "공통 최소 confidence 사용"
        ),
    )


    parser.add_argument(
        "--report",

        default=str(
            DEFAULT_REPORT_PATH
        ),
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


    report_path = Path(
        args.report
    )


    print(
        "=" * 80
    )

    print(
        "CareerQuest - Model Output Writer"
    )

    print(
        "=" * 80
    )


    # --------------------------------------------------------
    # JSON 읽기
    # --------------------------------------------------------

    inputs = (
        parse_inputs(
            load_json(
                input_path
            )
        )
    )


    outputs = (
        parse_outputs(
            load_json(
                output_path
            )
        )
    )


    print(
        (
            f"\nInput resources : "
            f"{len(inputs)}"
        )
    )


    print(
        (
            f"Output resources: "
            f"{len(outputs)}"
        )
    )


    # --------------------------------------------------------
    # 전체 검증
    # --------------------------------------------------------

    prepared, skipped = (
        prepare_mappings(
            inputs=(
                inputs
            ),

            outputs=(
                outputs
            ),

            mapping_method=(
                args.mapping_method
            ),

            min_confidence_override=(
                args.min_confidence
            ),
        )
    )


    print(
        "\nSchema / DB / Evidence 검증 통과"
    )


    # --------------------------------------------------------
    # DB 쓰기 계획
    # --------------------------------------------------------

    plan = (
        build_write_plan(
            prepared=(
                prepared
            ),

            mapping_method=(
                args.mapping_method
            ),
        )
    )


    print_plan(
        plan,
        skipped,
    )


    # --------------------------------------------------------
    # 실제 DB 저장
    # --------------------------------------------------------

    applied_counts = None


    if args.save:

        print(
            "\n[DB SAVE]"
        )


        applied_counts = (
            apply_write_plan(
                plan
            )
        )


        print(
            (
                "저장 완료: "
                f"{applied_counts}"
            )
        )


    else:

        print(
            "\n[DRY RUN]"
        )


        print(
            (
                "DB를 수정하지 않았습니다. "
                "실제 저장하려면 --save를 붙이세요."
            )
        )


    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    save_report(
        path=(
            report_path
        ),

        input_path=(
            input_path
        ),

        output_path=(
            output_path
        ),

        mapping_method=(
            args.mapping_method
        ),

        save_mode=(
            args.save
        ),

        plan=(
            plan
        ),

        skipped=(
            skipped
        ),

        applied_counts=(
            applied_counts
        ),
    )


    print(
        (
            f"\nReport: "
            f"{report_path}"
        )
    )


    print(
        "\n"
        + "=" * 80
    )

    print(
        "완료"
    )

    print(
        "=" * 80
    )


if __name__ == "__main__":
    main()