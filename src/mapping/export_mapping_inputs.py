from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from src.db.database import engine

from src.mapping.schemas import (
    ResourceType,
    SkillMappingInput,
    SkillReference,
)


# ============================================================
# 기본 설정
# ============================================================

DEFAULT_OUTPUT_DIR = Path(
    "model_exchange"
)


# ============================================================
# 문자열
# ============================================================

def clean_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    return str(
        value
    ).strip()


# ============================================================
# Skill 45개 로딩
# ============================================================

def load_allowed_skills(
) -> list[SkillReference]:

    sql = text(
        """
        SELECT
            s.skill_id,
            s.name,
            s.category,
            s.description,

            COALESCE(
                string_agg(
                    sa.alias,
                    '|||'
                    ORDER BY sa.alias
                ),
                ''
            ) AS aliases

        FROM skill s

        LEFT JOIN skill_alias sa
            ON sa.skill_id = s.skill_id

        WHERE s.is_active = TRUE

        GROUP BY
            s.skill_id,
            s.name,
            s.category,
            s.description

        ORDER BY
            s.skill_id
        """
    )


    with engine.connect() as conn:

        rows = conn.execute(
            sql
        ).mappings().all()


    result: list[
        SkillReference
    ] = []


    for row in rows:

        alias_text = clean_text(
            row[
                "aliases"
            ]
        )


        aliases = (
            [
                alias.strip()

                for alias
                in alias_text.split(
                    "|||"
                )

                if alias.strip()
            ]

            if alias_text

            else []
        )


        result.append(
            SkillReference(
                skill_id=int(
                    row[
                        "skill_id"
                    ]
                ),

                name=row[
                    "name"
                ],

                aliases=aliases,

                category=row[
                    "category"
                ],

                description=row[
                    "description"
                ],
            )
        )


    return result


# ============================================================
# SourceDocument 선택
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

        WHERE resource_id = :resource_id

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
# Resource 기본 조회
# ============================================================

def load_resources(
    resource_type: ResourceType,
    limit: int | None,
) -> list[dict]:

    sql_text = """
        SELECT
            resource_id,
            resource_type,
            name,
            organization,
            description,
            canonical_url

        FROM resource

        WHERE resource_type = :resource_type
          AND is_active = TRUE

        ORDER BY
            resource_id
    """


    if (
        limit is not None
        and limit > 0
    ):
        sql_text += (
            "\nLIMIT :limit"
        )


    sql = text(
        sql_text
    )


    params = {
        "resource_type":
            resource_type.value,
    }


    if (
        limit is not None
        and limit > 0
    ):
        params[
            "limit"
        ] = limit


    with engine.connect() as conn:

        rows = conn.execute(
            sql,
            params,
        ).mappings().all()


    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# COURSE 상세
# ============================================================

def load_course_detail(
    resource_id: int,
) -> dict | None:

    sql = text(
        """
        SELECT
            cd.course_code,
            cd.credits,
            cd.year_level,
            cd.semester,
            cd.completion_type,
            cd.curriculum_year,

            d.name AS department_name

        FROM course_detail cd

        LEFT JOIN department d
            ON d.department_id = cd.department_id

        WHERE cd.resource_id = :resource_id
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
# JOB 상세
# ============================================================

def load_job_detail(
    resource_id: int,
) -> dict | None:

    sql = text(
        """
        SELECT
            career_type,
            career_min_years,
            career_max_years,

            education_requirement,
            employment_type,
            location,

            responsibilities,
            requirements,
            preferences,

            deadline

        FROM job_detail

        WHERE resource_id = :resource_id
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
# CERTIFICATE 상세
# ============================================================

def load_certificate_detail(
    resource_id: int,
) -> dict | None:

    sql = text(
        """
        SELECT
            qualification_code,
            qualification_type,
            administering_org,

            major_field_name,
            middle_field_name,

            exam_frequency,
            validity_period,
            credit_recognition

        FROM certificate_detail

        WHERE resource_id = :resource_id
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
# PROGRAM 상세
# ============================================================

def load_program_detail(
    resource_id: int,
) -> dict | None:

    sql = text(
        """
        SELECT
            target_audience,

            application_start,
            application_end,

            program_start,
            program_end,

            location,
            cost_text,
            capacity,
            application_method

        FROM program_detail

        WHERE resource_id = :resource_id
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
# content 생성
#
# 팀원 모델에 넘길 단일 텍스트
# ============================================================

def build_content(
    resource: dict,
    resource_type: ResourceType,
    source_document: dict | None,
) -> str:

    lines: list[
        str
    ] = []


    # --------------------------------------------------------
    # 기본 Resource
    # --------------------------------------------------------

    lines.append(
        (
            f"Resource Type: "
            f"{resource_type.value}"
        )
    )


    lines.append(
        (
            f"Name: "
            f"{clean_text(resource['name'])}"
        )
    )


    organization = clean_text(
        resource.get(
            "organization"
        )
    )


    if organization:

        lines.append(
            (
                f"Organization: "
                f"{organization}"
            )
        )


    description = clean_text(
        resource.get(
            "description"
        )
    )


    if description:

        lines.append(
            (
                f"Description: "
                f"{description}"
            )
        )


    # --------------------------------------------------------
    # COURSE
    # --------------------------------------------------------

    if (
        resource_type
        == ResourceType.COURSE
    ):

        detail = (
            load_course_detail(
                int(
                    resource[
                        "resource_id"
                    ]
                )
            )
        )


        if detail:

            lines.extend(
                [
                    (
                        f"Course Code: "
                        f"{clean_text(detail.get('course_code'))}"
                    ),

                    (
                        f"Department: "
                        f"{clean_text(detail.get('department_name'))}"
                    ),

                    (
                        f"Curriculum Year: "
                        f"{clean_text(detail.get('curriculum_year'))}"
                    ),

                    (
                        f"Year Level: "
                        f"{clean_text(detail.get('year_level'))}"
                    ),

                    (
                        f"Semester: "
                        f"{clean_text(detail.get('semester'))}"
                    ),

                    (
                        f"Completion Type: "
                        f"{clean_text(detail.get('completion_type'))}"
                    ),

                    (
                        f"Credits: "
                        f"{clean_text(detail.get('credits'))}"
                    ),
                ]
            )


    # --------------------------------------------------------
    # JOB
    # --------------------------------------------------------

    elif (
        resource_type
        == ResourceType.JOB
    ):

        detail = (
            load_job_detail(
                int(
                    resource[
                        "resource_id"
                    ]
                )
            )
        )


        if detail:

            for label, key in (
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
            ):

                value = clean_text(
                    detail.get(
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


    # --------------------------------------------------------
    # CERTIFICATE
    # --------------------------------------------------------

    elif (
        resource_type
        == ResourceType.CERTIFICATE
    ):

        detail = (
            load_certificate_detail(
                int(
                    resource[
                        "resource_id"
                    ]
                )
            )
        )


        if detail:

            for label, key in (
                (
                    "Qualification Code",
                    "qualification_code",
                ),

                (
                    "Qualification Type",
                    "qualification_type",
                ),

                (
                    "Administering Organization",
                    "administering_org",
                ),

                (
                    "Major Field",
                    "major_field_name",
                ),

                (
                    "Middle Field",
                    "middle_field_name",
                ),

                (
                    "Exam Frequency",
                    "exam_frequency",
                ),

                (
                    "Validity Period",
                    "validity_period",
                ),

                (
                    "Credit Recognition",
                    "credit_recognition",
                ),
            ):

                value = clean_text(
                    detail.get(
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


    # --------------------------------------------------------
    # PROGRAM
    # --------------------------------------------------------

    elif (
        resource_type
        == ResourceType.PROGRAM
    ):

        detail = (
            load_program_detail(
                int(
                    resource[
                        "resource_id"
                    ]
                )
            )
        )


        if detail:

            for label, key in (
                (
                    "Target Audience",
                    "target_audience",
                ),

                (
                    "Program Start",
                    "program_start",
                ),

                (
                    "Program End",
                    "program_end",
                ),

                (
                    "Location",
                    "location",
                ),

                (
                    "Application Method",
                    "application_method",
                ),
            ):

                value = clean_text(
                    detail.get(
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


    # --------------------------------------------------------
    # SourceDocument 원문
    # --------------------------------------------------------

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
# SkillMappingInput 생성
# ============================================================

def build_mapping_inputs(
    resource_type: ResourceType,
    limit: int | None,
) -> list[SkillMappingInput]:

    allowed_skills = (
        load_allowed_skills()
    )


    resources = (
        load_resources(
            resource_type,
            limit,
        )
    )


    results: list[
        SkillMappingInput
    ] = []


    for resource in resources:

        resource_id = int(
            resource[
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

            if source_document
            and source_document.get(
                "source_document_id"
            )
            is not None

            else None
        )


        source_title = (
            clean_text(
                source_document.get(
                    "title"
                )
            )

            if source_document

            else None
        )


        content = (
            build_content(
                resource,
                resource_type,
                source_document,
            )
        )


        results.append(
            SkillMappingInput(
                resource_id=(
                    resource_id
                ),

                resource_type=(
                    resource_type
                ),

                resource_name=(
                    resource[
                        "name"
                    ]
                ),

                source_document_id=(
                    source_document_id
                ),

                source_title=(
                    source_title
                    or resource[
                        "name"
                    ]
                ),

                content=(
                    content
                ),

                allowed_skills=(
                    allowed_skills
                ),
            )
        )


    return results


# ============================================================
# JSON 저장
# ============================================================

def save_json(
    inputs: list[
        SkillMappingInput
    ],

    output_path: Path,
) -> None:

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


# ============================================================
# CLI
# ============================================================

def parse_args(
) -> argparse.Namespace:

    parser = (
        argparse.ArgumentParser()
    )


    parser.add_argument(
        "--type",

        required=True,

        choices=[
            "COURSE",
            "CERTIFICATE",
            "JOB",
            "PROGRAM",
        ],
    )


    parser.add_argument(
        "--limit",

        type=int,

        default=None,
    )


    parser.add_argument(
        "--output",

        default=None,
    )


    return (
        parser.parse_args()
    )


# ============================================================
# 실행
# ============================================================

def main(
) -> None:

    args = (
        parse_args()
    )


    resource_type = (
        ResourceType(
            args.type
        )
    )


    inputs = (
        build_mapping_inputs(
            resource_type,
            args.limit,
        )
    )


    if args.output:

        output_path = Path(
            args.output
        )

    else:

        output_path = (
            DEFAULT_OUTPUT_DIR
            / (
                f"{resource_type.value.lower()}"
                "_mapping_input.json"
            )
        )


    save_json(
        inputs,
        output_path,
    )


    print(
        "=" * 72
    )

    print(
        "CareerQuest model integration export"
    )

    print(
        "=" * 72
    )


    print(
        (
            f"Resource type : "
            f"{resource_type.value}"
        )
    )


    print(
        (
            f"Resources     : "
            f"{len(inputs)}"
        )
    )


    skill_count = (
        len(
            inputs[0].allowed_skills
        )

        if inputs

        else 0
    )


    print(
        (
            f"Allowed Skill : "
            f"{skill_count}"
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


if __name__ == "__main__":
    main()