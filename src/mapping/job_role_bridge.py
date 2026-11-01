from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from src.db.database import engine


# ============================================================
# 기본 설정
# ============================================================

CONTRACT_VERSION = "1.0"

DEFAULT_INPUT_PATH = Path(
    "model_exchange/job_role_mapping_input.json"
)

DEFAULT_REPORT_PATH = Path(
    "model_exchange/job_role_write_report.json"
)

DEFAULT_MAPPING_METHOD = "team_stage1_v1"

MIN_CONFIDENCE = 0.80

# 하나의 공고가 여러 역할과 관계될 수는 있지만
# 현재 CareerQuest에서는 과도한 다중 분류 방지
MAX_ROLES_PER_JOB = 2


# ============================================================
# Pydantic Contract
# ============================================================

class JobRoleReference(BaseModel):
    job_role_id: int

    name: str

    category: str | None = None

    description: str | None = None

    standard_code: str | None = None


class JobRoleMappingInput(BaseModel):
    contract_version: str = CONTRACT_VERSION

    resource_id: int

    resource_name: str

    organization: str | None = None

    source_document_id: int | None = None

    content: str

    allowed_roles: list[
        JobRoleReference
    ] = Field(
        default_factory=list
    )


class JobRoleMappingItem(BaseModel):
    job_role_id: int

    is_primary: bool = False

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    evidence_text: str

    reason: str | None = None


class JobRoleMappingOutput(BaseModel):
    contract_version: str = CONTRACT_VERSION

    resource_id: int

    source_document_id: int | None = None

    mappings: list[
        JobRoleMappingItem
    ] = Field(
        default_factory=list
    )

    # 모델이 직무를 확정하기 어려운 경우
    review_required: bool = False

    review_reason: str | None = None


# ============================================================
# 문자열 처리
# ============================================================

def clean_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()


def normalize_text(
    value: Any,
) -> str:

    return clean_text(
        value
    ).lower()


# ============================================================
# JobRole DB 로딩
# ============================================================

def load_job_roles(
) -> list[JobRoleReference]:

    sql = text(
        """
        SELECT
            job_role_id,
            name,
            category,
            description,
            standard_code

        FROM job_role

        WHERE is_active = TRUE

        ORDER BY job_role_id
        """
    )


    with engine.connect() as conn:

        rows = conn.execute(
            sql
        ).mappings().all()


    return [
        JobRoleReference(
            job_role_id=int(
                row[
                    "job_role_id"
                ]
            ),

            name=row[
                "name"
            ],

            category=row[
                "category"
            ],

            description=row[
                "description"
            ],

            standard_code=row[
                "standard_code"
            ],
        )

        for row in rows
    ]


# ============================================================
# JOB Resource 로딩
# ============================================================

def load_jobs(
    limit: int | None = None,
) -> list[dict]:

    sql_text = """
        SELECT
            r.resource_id,
            r.name,
            r.organization,
            r.description,
            r.canonical_url,

            jd.career_type,
            jd.education_requirement,
            jd.employment_type,
            jd.location,

            jd.responsibilities,
            jd.requirements,
            jd.preferences,
            jd.deadline

        FROM resource r

        JOIN job_detail jd
            ON jd.resource_id =
               r.resource_id

        WHERE r.resource_type = 'JOB'
          AND r.is_active = TRUE

        ORDER BY
            r.resource_id
    """


    params: dict[str, Any] = {}


    if (
        limit is not None
        and limit > 0
    ):

        sql_text += (
            "\nLIMIT :limit"
        )

        params[
            "limit"
        ] = limit


    with engine.connect() as conn:

        rows = conn.execute(
            text(
                sql_text
            ),
            params,
        ).mappings().all()


    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# SourceDocument
# ============================================================

def load_best_source_document(
    resource_id: int,
) -> dict | None:

    sql = text(
        """
        SELECT
            source_document_id,
            title,
            content,
            source_url,
            validation_status,
            trust_level,
            evidence_eligible

        FROM source_document

        WHERE resource_id =
            :resource_id

        ORDER BY

            CASE
                WHEN validation_status = 'ACCEPTED'
                THEN 0
                ELSE 1
            END,

            CASE
                WHEN evidence_eligible = TRUE
                THEN 0
                ELSE 1
            END,

            CASE trust_level
                WHEN 'A' THEN 0
                WHEN 'B' THEN 1
                WHEN 'C' THEN 2
                ELSE 3
            END,

            source_document_id DESC

        LIMIT 1
        """
    )


    with engine.connect() as conn:

        row = conn.execute(
            sql,

            {
                "resource_id":
                    resource_id,
            },
        ).mappings().first()


    if row is None:
        return None


    return dict(
        row
    )


# ============================================================
# 모델 입력 Content
# ============================================================

def build_job_content(
    job: dict,
    source_document: dict | None,
) -> str:

    lines: list[str] = []


    lines.append(
        (
            f"Job Title: "
            f"{clean_text(job['name'])}"
        )
    )


    fields = (
        (
            "Organization",
            "organization",
        ),

        (
            "Description",
            "description",
        ),

        (
            "Career Type",
            "career_type",
        ),

        (
            "Education Requirement",
            "education_requirement",
        ),

        (
            "Employment Type",
            "employment_type",
        ),

        (
            "Location",
            "location",
        ),

        (
            "Responsibilities",
            "responsibilities",
        ),

        (
            "Requirements",
            "requirements",
        ),

        (
            "Preferences",
            "preferences",
        ),

        (
            "Deadline",
            "deadline",
        ),
    )


    for label, key in fields:

        value = clean_text(
            job.get(
                key
            )
        )


        if value:

            lines.append(
                (
                    f"{label}: "
                    f"{value}"
                )
            )


    if source_document:

        source_content = clean_text(
            source_document.get(
                "content"
            )
        )


        if source_content:

            lines.append(
                ""
            )

            lines.append(
                "===== SOURCE DOCUMENT ====="
            )

            lines.append(
                source_content
            )


    return "\n".join(
        lines
    )


# ============================================================
# Export
# ============================================================

def build_inputs(
    limit: int | None,
) -> list[JobRoleMappingInput]:

    roles = (
        load_job_roles()
    )


    if not roles:

        raise RuntimeError(
            "활성 JobRole이 없습니다."
        )


    jobs = (
        load_jobs(
            limit
        )
    )


    results: list[
        JobRoleMappingInput
    ] = []


    for job in jobs:

        resource_id = int(
            job[
                "resource_id"
            ]
        )


        source_document = (
            load_best_source_document(
                resource_id
            )
        )


        source_document_id = (
            int(
                source_document[
                    "source_document_id"
                ]
            )

            if (
                source_document
                and source_document.get(
                    "source_document_id"
                )
                is not None
            )

            else None
        )


        content = (
            build_job_content(
                job,
                source_document,
            )
        )


        results.append(
            JobRoleMappingInput(
                resource_id=(
                    resource_id
                ),

                resource_name=(
                    job[
                        "name"
                    ]
                ),

                organization=(
                    job.get(
                        "organization"
                    )
                ),

                source_document_id=(
                    source_document_id
                ),

                content=(
                    content
                ),

                allowed_roles=(
                    roles
                ),
            )
        )


    return results


def export_inputs(
    output_path: Path,
    limit: int | None,
) -> None:

    inputs = (
        build_inputs(
            limit
        )
    )


    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )


    payload = [
        item.model_dump(
            mode="json"
        )

        for item
        in inputs
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


    role_count = (
        len(
            inputs[0].allowed_roles
        )

        if inputs

        else 0
    )


    print(
        "=" * 72
    )

    print(
        "CareerQuest - JobRole Mapping Export"
    )

    print(
        "=" * 72
    )

    print(
        (
            f"JOB resources : "
            f"{len(inputs)}"
        )
    )

    print(
        (
            f"Allowed roles : "
            f"{role_count}"
        )
    )

    print(
        (
            f"Output        : "
            f"{output_path}"
        )
    )

    print(
        "=" * 72
    )


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


def parse_inputs(
    payload: Any,
) -> list[JobRoleMappingInput]:

    if isinstance(
        payload,
        list,
    ):

        items = payload


    elif isinstance(
        payload,
        dict,
    ):

        if "inputs" in payload:

            items = (
                payload[
                    "inputs"
                ]
            )

        else:

            items = [
                payload
            ]


    else:

        raise ValueError(
            "잘못된 Input JSON 형식"
        )


    return [
        JobRoleMappingInput.model_validate(
            item
        )

        for item
        in items
    ]


def parse_outputs(
    payload: Any,
) -> list[JobRoleMappingOutput]:

    if isinstance(
        payload,
        list,
    ):

        items = payload


    elif isinstance(
        payload,
        dict,
    ):

        if "outputs" in payload:

            items = (
                payload[
                    "outputs"
                ]
            )


        elif "results" in payload:

            items = (
                payload[
                    "results"
                ]
            )


        elif "resource_id" in payload:

            items = [
                payload
            ]


        else:

            raise ValueError(
                (
                    "Output JSON에 "
                    "outputs/results/resource_id가 없습니다."
                )
            )


    else:

        raise ValueError(
            "잘못된 Output JSON 형식"
        )


    return [
        JobRoleMappingOutput.model_validate(
            item
        )

        for item
        in items
    ]


# ============================================================
# Output 검증
# ============================================================

def validate_outputs(
    inputs: list[
        JobRoleMappingInput
    ],

    outputs: list[
        JobRoleMappingOutput
    ],
) -> tuple[
    list[dict],
    list[dict],
]:

    input_by_id = {
        item.resource_id:
            item

        for item
        in inputs
    }


    if (
        len(
            input_by_id
        )
        != len(
            inputs
        )
    ):

        raise ValueError(
            "Input resource_id 중복"
        )


    output_ids = [
        item.resource_id

        for item
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
            "Output resource_id 중복"
        )


    input_ids = set(
        input_by_id
    )

    output_id_set = set(
        output_ids
    )


    if (
        input_ids
        != output_id_set
    ):

        missing = sorted(
            input_ids
            - output_id_set
        )

        unknown = sorted(
            output_id_set
            - input_ids
        )


        raise ValueError(
            (
                "Input/Output resource 불일치 "
                f"| missing={missing} "
                f"| unknown={unknown}"
            )
        )


    prepared: list[
        dict
    ] = []


    review_items: list[
        dict
    ] = []


    for output in outputs:

        mapping_input = (
            input_by_id[
                output.resource_id
            ]
        )


        if (
            output.contract_version
            != CONTRACT_VERSION
        ):

            raise ValueError(
                (
                    "contract_version 불일치: "
                    f"{output.contract_version}"
                )
            )


        if (
            mapping_input.source_document_id
            is not None
            and output.source_document_id
            is not None
            and mapping_input.source_document_id
            != output.source_document_id
        ):

            raise ValueError(
                (
                    "source_document_id 불일치: "
                    f"resource_id="
                    f"{output.resource_id}"
                )
            )


        # ----------------------------------------------------
        # REVIEW_REQUIRED
        # ----------------------------------------------------

        if output.review_required:

            review_items.append(
                {
                    "resource_id":
                        output.resource_id,

                    "resource_name":
                        mapping_input.resource_name,

                    "reason":
                        (
                            output.review_reason
                            or
                            "MODEL_REVIEW_REQUIRED"
                        ),
                }
            )

            # 보수적으로:
            # review_required면 DB 저장 후보 제외
            continue


        # ----------------------------------------------------
        # 역할 개수
        # ----------------------------------------------------

        if (
            len(
                output.mappings
            )
            > MAX_ROLES_PER_JOB
        ):

            raise ValueError(
                (
                    "JobRole 매핑이 너무 많습니다: "
                    f"resource_id="
                    f"{output.resource_id}"
                )
            )


        if not output.mappings:

            review_items.append(
                {
                    "resource_id":
                        output.resource_id,

                    "resource_name":
                        mapping_input.resource_name,

                    "reason":
                        "NO_JOB_ROLE_MAPPING",
                }
            )

            continue


        # ----------------------------------------------------
        # primary 하나 이하
        # ----------------------------------------------------

        primary_count = sum(
            1

            for item
            in output.mappings

            if item.is_primary
        )


        if primary_count > 1:

            raise ValueError(
                (
                    "Primary JobRole이 "
                    "2개 이상입니다: "
                    f"resource_id="
                    f"{output.resource_id}"
                )
            )


        # 매핑이 존재하는 경우
        # primary는 정확히 하나 권장
        if primary_count == 0:

            raise ValueError(
                (
                    "JobRole 매핑은 존재하지만 "
                    "Primary가 없습니다: "
                    f"resource_id="
                    f"{output.resource_id}"
                )
            )


        allowed_role_ids = {
            role.job_role_id

            for role
            in mapping_input.allowed_roles
        }


        normalized_content = (
            normalize_text(
                mapping_input.content
            )
        )


        seen_roles: set[
            int
        ] = set()


        for mapping in (
            output.mappings
        ):

            if (
                mapping.job_role_id
                in seen_roles
            ):

                raise ValueError(
                    (
                        "중복 JobRole: "
                        f"resource_id="
                        f"{output.resource_id}, "
                        f"job_role_id="
                        f"{mapping.job_role_id}"
                    )
                )


            seen_roles.add(
                mapping.job_role_id
            )


            if (
                mapping.job_role_id
                not in allowed_role_ids
            ):

                raise ValueError(
                    (
                        "허용되지 않은 JobRole: "
                        f"{mapping.job_role_id}"
                    )
                )


            if (
                mapping.confidence
                < MIN_CONFIDENCE
            ):

                review_items.append(
                    {
                        "resource_id":
                            output.resource_id,

                        "resource_name":
                            mapping_input.resource_name,

                        "job_role_id":
                            mapping.job_role_id,

                        "reason":
                            "LOW_CONFIDENCE",

                        "confidence":
                            mapping.confidence,
                    }
                )

                continue


            evidence = clean_text(
                mapping.evidence_text
            )


            if not evidence:

                raise ValueError(
                    (
                        "evidence_text가 비어있음: "
                        f"resource_id="
                        f"{output.resource_id}"
                    )
                )


            # 모델이 evidence를 지어내면 안 됨.
            if (
                normalize_text(
                    evidence
                )
                not in normalized_content
            ):

                raise ValueError(
                    (
                        "evidence가 실제 입력 원문에 "
                        "존재하지 않습니다.\n"
                        f"resource_id="
                        f"{output.resource_id}\n"
                        f"evidence="
                        f"{evidence}"
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


            prepared.append(
                {
                    "resource_id":
                        output.resource_id,

                    "resource_name":
                        mapping_input.resource_name,

                    "job_role_id":
                        mapping.job_role_id,

                    "is_primary":
                        mapping.is_primary,

                    "confidence":
                        mapping.confidence,

                    "evidence_text":
                        evidence,

                    "reason":
                        mapping.reason,

                    "source_document_id":
                        source_document_id,
                }
            )


    return (
        prepared,
        review_items,
    )


# ============================================================
# 기존 다른 방식의 매핑 충돌 확인
# ============================================================

def check_conflict(
    resource_id: int,
    job_role_id: int,
    mapping_method: str,
) -> dict | None:

    sql = text(
        """
        SELECT
            resource_job_role_id,
            mapping_method,
            confidence,
            is_primary

        FROM resource_job_role

        WHERE resource_id =
            :resource_id

          AND job_role_id =
            :job_role_id
        """
    )


    with engine.connect() as conn:

        row = conn.execute(
            sql,

            {
                "resource_id":
                    resource_id,

                "job_role_id":
                    job_role_id,
            },
        ).mappings().first()


    if row is None:

        return None


    result = dict(
        row
    )


    if (
        result.get(
            "mapping_method"
        )
        == mapping_method
    ):

        return None


    return result


# ============================================================
# Dry-run Plan
# ============================================================

def build_write_plan(
    prepared: list[dict],
    mapping_method: str,
) -> list[dict]:

    plan: list[
        dict
    ] = []


    for item in prepared:

        conflict = (
            check_conflict(
                resource_id=(
                    item[
                        "resource_id"
                    ]
                ),

                job_role_id=(
                    item[
                        "job_role_id"
                    ]
                ),

                mapping_method=(
                    mapping_method
                ),
            )
        )


        plan_item = dict(
            item
        )


        if conflict:

            plan_item[
                "action"
            ] = (
                "SKIP_OTHER_METHOD"
            )


            plan_item[
                "existing_mapping_method"
            ] = (
                conflict.get(
                    "mapping_method"
                )
            )


        else:

            plan_item[
                "action"
            ] = (
                "UPSERT"
            )


        plan.append(
            plan_item
        )


    return plan


# ============================================================
# DB 저장
#
# 같은 mapping_method가 이전에 만든 결과만
# 해당 resource 기준으로 지우고 다시 생성.
# ============================================================

def apply_write_plan(
    plan: list[dict],
    mapping_method: str,
) -> dict:

    by_resource: dict[
        int,
        list[dict]
    ] = {}


    for item in plan:

        by_resource.setdefault(
            item[
                "resource_id"
            ],
            [],
        ).append(
            item
        )


    delete_sql = text(
        """
        DELETE FROM resource_job_role

        WHERE resource_id =
            :resource_id

          AND mapping_method =
            :mapping_method
        """
    )


    insert_sql = text(
        """
        INSERT INTO resource_job_role (
            resource_id,
            job_role_id,
            is_primary,
            mapping_method,
            confidence,
            evidence_text
        )

        VALUES (
            :resource_id,
            :job_role_id,
            :is_primary,
            :mapping_method,
            :confidence,
            :evidence_text
        )
        """
    )


    saved = 0
    skipped = 0


    with engine.begin() as conn:

        for (
            resource_id,
            items,
        ) in by_resource.items():

            # 이 모델이 이전에 만든 결과만 제거
            conn.execute(
                delete_sql,

                {
                    "resource_id":
                        resource_id,

                    "mapping_method":
                        mapping_method,
                },
            )


            for item in items:

                if (
                    item[
                        "action"
                    ]
                    != "UPSERT"
                ):

                    skipped += 1
                    continue


                conn.execute(
                    insert_sql,

                    {
                        "resource_id":
                            resource_id,

                        "job_role_id":
                            item[
                                "job_role_id"
                            ],

                        "is_primary":
                            item[
                                "is_primary"
                            ],

                        "mapping_method":
                            mapping_method,

                        "confidence":
                            item[
                                "confidence"
                            ],

                        "evidence_text":
                            item[
                                "evidence_text"
                            ],
                    },
                )


                saved += 1


    return {
        "saved":
            saved,

        "skipped":
            skipped,
    }


# ============================================================
# Report
# ============================================================

def print_plan(
    plan: list[dict],
    review_items: list[dict],
    role_names: dict[int, str],
) -> None:

    print(
        "\n"
        + "=" * 80
    )

    print(
        "JOB -> JOB_ROLE WRITE PLAN"
    )

    print(
        "=" * 80
    )


    for item in plan:

        role_name = (
            role_names.get(
                item[
                    "job_role_id"
                ],
                "UNKNOWN",
            )
        )


        print(
            (
                f"\n[{item['action']}] "
                f"{item['resource_name']}"
            )
        )


        print(
            (
                f"  -> "
                f"{role_name} "
                f"(job_role_id="
                f"{item['job_role_id']})"
            )
        )


        print(
            (
                f"     primary="
                f"{item['is_primary']} "
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


    if review_items:

        print(
            "\n"
            + "-" * 80
        )

        print(
            "REVIEW REQUIRED"
        )

        print(
            "-" * 80
        )


        for item in review_items:

            print(
                (
                    f"{item['resource_name']} "
                    f"| {item['reason']}"
                )
            )


    print(
        "\n"
        + "-" * 80
    )


    print(
        (
            f"저장 후보: "
            f"{sum(1 for i in plan if i['action'] == 'UPSERT')}개"
        )
    )


    print(
        (
            f"다른 방식과 충돌: "
            f"{sum(1 for i in plan if i['action'] == 'SKIP_OTHER_METHOD')}개"
        )
    )


    print(
        (
            f"검토 필요: "
            f"{len(review_items)}개"
        )
    )


# ============================================================
# Write
# ============================================================

def write_outputs(
    input_path: Path,
    output_path: Path,
    mapping_method: str,
    save: bool,
) -> None:

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


    prepared, review_items = (
        validate_outputs(
            inputs,
            outputs,
        )
    )


    plan = (
        build_write_plan(
            prepared,
            mapping_method,
        )
    )


    roles = (
        load_job_roles()
    )


    role_names = {
        role.job_role_id:
            role.name

        for role in roles
    }


    print(
        "\n검증 통과"
    )


    print_plan(
        plan,
        review_items,
        role_names,
    )


    if save:

        result = (
            apply_write_plan(
                plan,
                mapping_method,
            )
        )


        print(
            "\n[DB SAVE]"
        )


        print(
            (
                f"saved   : "
                f"{result['saved']}"
            )
        )


        print(
            (
                f"skipped : "
                f"{result['skipped']}"
            )
        )


    else:

        print(
            "\n[DRY RUN]"
        )


        print(
            "DB를 수정하지 않았습니다."
        )


# ============================================================
# CLI
# ============================================================

def parse_args(
) -> argparse.Namespace:

    parser = (
        argparse.ArgumentParser()
    )


    subparsers = (
        parser.add_subparsers(
            dest="command",
            required=True,
        )
    )


    # --------------------------------------------------------
    # export
    # --------------------------------------------------------

    export_parser = (
        subparsers.add_parser(
            "export"
        )
    )


    export_parser.add_argument(
        "--limit",

        type=int,

        default=None,
    )


    export_parser.add_argument(
        "--output",

        default=str(
            DEFAULT_INPUT_PATH
        ),
    )


    # --------------------------------------------------------
    # write
    # --------------------------------------------------------

    write_parser = (
        subparsers.add_parser(
            "write"
        )
    )


    write_parser.add_argument(
        "--input",

        required=True,
    )


    write_parser.add_argument(
        "--output",

        required=True,
    )


    write_parser.add_argument(
        "--mapping-method",

        default=(
            DEFAULT_MAPPING_METHOD
        ),
    )


    write_parser.add_argument(
        "--save",

        action="store_true",
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


    if (
        args.command
        == "export"
    ):

        export_inputs(
            output_path=(
                Path(
                    args.output
                )
            ),

            limit=(
                args.limit
            ),
        )


    elif (
        args.command
        == "write"
    ):

        write_outputs(
            input_path=(
                Path(
                    args.input
                )
            ),

            output_path=(
                Path(
                    args.output
                )
            ),

            mapping_method=(
                args.mapping_method
            ),

            save=(
                args.save
            ),
        )


if __name__ == "__main__":
    main()