from __future__ import annotations

import argparse
import json
import re

from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

from src.db.database import engine


# ============================================================
# 경로 / 설정
# ============================================================

INPUT_DIR = Path(
    "data/processed/student_experience_mapping_inputs"
)

OUTPUT_DIR = Path(
    "data/processed/student_experience_mapping_outputs"
)

MIN_MODEL_CONFIDENCE = Decimal(
    "0.75"
)

MAPPING_METHOD = (
    "EXPERIENCE_MODEL"
)


# ============================================================
# 공통
# ============================================================

def normalize_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    value = str(
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip().lower()


# ============================================================
# StudentExperience 조회
# ============================================================

def load_experience(
    experience_id: int,
) -> dict:

    sql = text(
        """
        SELECT
            se.experience_id,
            se.student_id,

            se.experience_type,
            se.title,
            se.organization,
            se.description,

            se.start_date,
            se.end_date,

            se.source_url,
            se.raw_metadata,

            st.external_user_id,

            d.name AS department_name

        FROM student_experience se

        JOIN student st
            ON st.student_id =
               se.student_id

        LEFT JOIN department d
            ON d.department_id =
               st.department_id

        WHERE
            se.experience_id =
                :experience_id
        """
    )


    with engine.connect() as conn:

        row = (
            conn.execute(
                sql,

                {
                    "experience_id":
                        experience_id,
                },
            ).mappings().first()
        )


    if row is None:

        raise RuntimeError(
            (
                "StudentExperience가 없습니다: "
                f"{experience_id}"
            )
        )


    return dict(
        row
    )


# ============================================================
# Experience 원문
# ============================================================

def build_experience_content(
    experience: dict,
) -> str:

    parts = [
        (
            f"경험 유형: "
            f"{experience['experience_type']}"
        ),

        (
            f"제목: "
            f"{experience['title']}"
        ),
    ]


    if experience[
        "organization"
    ]:

        parts.append(
            (
                "기관/조직: "
                f"{experience['organization']}"
            )
        )


    if experience[
        "description"
    ]:

        parts.append(
            (
                "설명:\n"
                f"{experience['description']}"
            )
        )


    if experience[
        "start_date"
    ]:

        parts.append(
            (
                "시작일: "
                f"{experience['start_date']}"
            )
        )


    if experience[
        "end_date"
    ]:

        parts.append(
            (
                "종료일: "
                f"{experience['end_date']}"
            )
        )


    return "\n".join(
        parts
    )


# ============================================================
# Skill 사전
# ============================================================

def load_skill_dictionary(
) -> list[dict]:

    sql = text(
        """
        SELECT
            s.skill_id,
            s.name,
            s.normalized_name,
            s.category,

            sa.alias

        FROM skill s

        LEFT JOIN skill_alias sa
            ON sa.skill_id =
               s.skill_id

        WHERE
            s.is_active =
                TRUE

        ORDER BY
            s.skill_id
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql
            ).mappings().all()
        )


    skills: dict[
        int,
        dict,
    ] = {}


    for row in rows:

        skill_id = int(
            row[
                "skill_id"
            ]
        )


        if skill_id not in skills:

            skills[
                skill_id
            ] = {
                "skill_id":
                    skill_id,

                "name":
                    row[
                        "name"
                    ],

                "normalized_name":
                    row[
                        "normalized_name"
                    ],

                "category":
                    row[
                        "category"
                    ],

                "aliases":
                    [],
            }


        alias = (
            row[
                "alias"
            ]
        )


        if (
            alias
            and alias
            not in skills[
                skill_id
            ][
                "aliases"
            ]
        ):

            skills[
                skill_id
            ][
                "aliases"
            ].append(
                alias
            )


    return list(
        skills.values()
    )


# ============================================================
# 팀원 모델 입력 JSON 생성
# ============================================================

def build_mapping_input(
    experience_id: int,
) -> dict:

    experience = (
        load_experience(
            experience_id
        )
    )


    skills = (
        load_skill_dictionary()
    )


    return {
        "contract_version":
            "student_experience_skill_v1",

        "student_id":
            int(
                experience[
                    "student_id"
                ]
            ),

        "experience_id":
            int(
                experience[
                    "experience_id"
                ]
            ),

        "experience_type":
            experience[
                "experience_type"
            ],

        "title":
            experience[
                "title"
            ],

        "content":
            build_experience_content(
                experience
            ),

        # ----------------------------------------------------
        # 모델은 반드시 아래 Skill 중에서만 선택
        # ----------------------------------------------------

        "allowed_skills":
            skills,

        # ----------------------------------------------------
        # 팀원에게 전달할 규칙
        # ----------------------------------------------------

        "instructions": {
            "rule_1":
                (
                    "allowed_skills에 존재하는 "
                    "skill_id만 사용할 것"
                ),

            "rule_2":
                (
                    "학생이 실제로 사용하거나 "
                    "수행했다고 근거가 있는 Skill만 선택"
                ),

            "rule_3":
                (
                    "evidence_text는 content의 "
                    "실제 문구를 그대로 사용"
                ),

            "rule_4":
                (
                    "단순히 기술명이 언급된 것만으로 "
                    "Skill로 판단하지 말 것"
                ),

            "rule_5":
                (
                    "confidence는 0~1"
                ),

            "rule_6":
                (
                    "Skill 사전에 없는 기술은 "
                    "unmapped_skills에 기록하고 "
                    "자동 생성하지 말 것"
                ),
        },

        "expected_output": {
            "contract_version":
                "student_experience_skill_v1",

            "student_id":
                "integer",

            "experience_id":
                "integer",

            "mappings": [
                {
                    "skill_id":
                        "integer",

                    "evidence_text":
                        "string",

                    "confidence":
                        "0.0~1.0",

                    "level":
                        (
                            "optional string "
                            "(예: basic/intermediate)"
                        ),

                    "reason":
                        "string",
                }
            ],

            "unmapped_skills": [
                "string"
            ],
        },
    }


def export_mapping_input(
    experience_id: int,
) -> Path:

    payload = (
        build_mapping_input(
            experience_id
        )
    )


    INPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


    path = (
        INPUT_DIR
        / (
            f"experience_"
            f"{experience_id}"
            f"_input.json"
        )
    )


    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


    print(
        "=" * 80
    )

    print(
        "StudentExperience -> Model Input"
    )

    print(
        "=" * 80
    )


    print(
        (
            "student_id    : "
            f"{payload['student_id']}"
        )
    )


    print(
        (
            "experience_id : "
            f"{payload['experience_id']}"
        )
    )


    print(
        (
            "title         : "
            f"{payload['title']}"
        )
    )


    print(
        (
            "allowed Skill : "
            f"{len(payload['allowed_skills'])}개"
        )
    )


    print(
        (
            "저장          : "
            f"{path}"
        )
    )


    return path


# ============================================================
# 모델 출력 로드
# ============================================================

def load_output(
    path: Path,
) -> dict:

    if not path.exists():

        raise FileNotFoundError(
            f"파일이 없습니다: {path}"
        )


    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


# ============================================================
# 모델 출력 검증
# ============================================================

def validate_output(
    payload: dict,
) -> tuple[
    dict,
    list[dict],
]:

    errors: list[str] = []


    # --------------------------------------------------------
    # 기본 필드
    # --------------------------------------------------------

    if (
        payload.get(
            "contract_version"
        )
        != "student_experience_skill_v1"
    ):

        errors.append(
            "contract_version 불일치"
        )


    student_id = (
        payload.get(
            "student_id"
        )
    )


    experience_id = (
        payload.get(
            "experience_id"
        )
    )


    if not isinstance(
        student_id,
        int,
    ):

        errors.append(
            "student_id는 integer여야 합니다."
        )


    if not isinstance(
        experience_id,
        int,
    ):

        errors.append(
            "experience_id는 integer여야 합니다."
        )


    if errors:

        raise ValueError(
            "\n".join(
                errors
            )
        )


    experience = (
        load_experience(
            experience_id
        )
    )


    if (
        int(
            experience[
                "student_id"
            ]
        )
        != student_id
    ):

        errors.append(
            (
                "experience의 student_id와 "
                "결과 student_id가 다릅니다."
            )
        )


    content = (
        build_experience_content(
            experience
        )
    )


    normalized_content = (
        normalize_text(
            content
        )
    )


    skill_dictionary = (
        load_skill_dictionary()
    )


    skill_lookup = {
        int(
            skill[
                "skill_id"
            ]
        ):
            skill

        for skill
        in skill_dictionary
    }


    mappings = (
        payload.get(
            "mappings"
        )
        or []
    )


    if not isinstance(
        mappings,
        list,
    ):

        errors.append(
            "mappings는 list여야 합니다."
        )

        mappings = []


    valid_mappings: list[
        dict
    ] = []


    seen_skill_ids: set[
        int
    ] = set()


    for index, mapping in enumerate(
        mappings,
        start=1,
    ):

        if not isinstance(
            mapping,
            dict,
        ):

            errors.append(
                (
                    f"mapping #{index}: "
                    "object가 아닙니다."
                )
            )

            continue


        skill_id = (
            mapping.get(
                "skill_id"
            )
        )


        evidence = (
            mapping.get(
                "evidence_text"
            )
        )


        confidence_raw = (
            mapping.get(
                "confidence"
            )
        )


        # ----------------------------------------------------
        # Skill
        # ----------------------------------------------------

        if not isinstance(
            skill_id,
            int,
        ):

            errors.append(
                (
                    f"mapping #{index}: "
                    "skill_id가 integer가 아닙니다."
                )
            )

            continue


        if (
            skill_id
            not in skill_lookup
        ):

            errors.append(
                (
                    f"mapping #{index}: "
                    f"허용되지 않은 skill_id="
                    f"{skill_id}"
                )
            )

            continue


        if (
            skill_id
            in seen_skill_ids
        ):

            errors.append(
                (
                    f"mapping #{index}: "
                    f"skill_id={skill_id} 중복"
                )
            )

            continue


        # ----------------------------------------------------
        # Evidence
        # ----------------------------------------------------

        if (
            not isinstance(
                evidence,
                str,
            )
            or not evidence.strip()
        ):

            errors.append(
                (
                    f"mapping #{index}: "
                    "evidence_text 없음"
                )
            )

            continue


        normalized_evidence = (
            normalize_text(
                evidence
            )
        )


        if (
            normalized_evidence
            not in normalized_content
        ):

            errors.append(
                (
                    f"mapping #{index}: "
                    "evidence_text가 Experience 원문에 없음 "
                    f"({skill_lookup[skill_id]['name']})"
                )
            )

            continue


        # ----------------------------------------------------
        # Confidence
        # ----------------------------------------------------

        try:

            confidence = (
                Decimal(
                    str(
                        confidence_raw
                    )
                )
            )


        except Exception:

            errors.append(
                (
                    f"mapping #{index}: "
                    "confidence 오류"
                )
            )

            continue


        if not (
            Decimal("0")
            <= confidence
            <= Decimal("1")
        ):

            errors.append(
                (
                    f"mapping #{index}: "
                    "confidence 범위 오류"
                )
            )

            continue


        # 보수적으로 낮은 결과는 저장하지 않음
        if (
            confidence
            < MIN_MODEL_CONFIDENCE
        ):

            print(
                (
                    "[SKIP LOW CONFIDENCE] "
                    f"{skill_lookup[skill_id]['name']} "
                    f"{confidence}"
                )
            )

            continue


        seen_skill_ids.add(
            skill_id
        )


        valid_mappings.append(
            {
                "skill_id":
                    skill_id,

                "skill_name":
                    skill_lookup[
                        skill_id
                    ][
                        "name"
                    ],

                "evidence_text":
                    evidence.strip(),

                "confidence":
                    confidence,

                "level":
                    mapping.get(
                        "level"
                    ),

                "reason":
                    mapping.get(
                        "reason"
                    ),
            }
        )


    if errors:

        print(
            "=" * 80
        )

        print(
            "MODEL OUTPUT 검증 실패"
        )

        print(
            "=" * 80
        )


        for error in errors:

            print(
                "-",
                error,
            )


        raise ValueError(
            (
                f"검증 오류 "
                f"{len(errors)}개"
            )
        )


    return (
        experience,
        valid_mappings,
    )


# ============================================================
# StudentSkill UPSERT
# ============================================================

UPSERT_STUDENT_SKILL_SQL = text(
    """
    INSERT INTO student_skill (
        student_id,
        skill_id,

        level,
        confidence,

        verified,

        acquired_at
    )

    VALUES (
        :student_id,
        :skill_id,

        :level,
        :confidence,

        FALSE,

        NOW()
    )

    ON CONFLICT (
        student_id,
        skill_id
    )

    DO UPDATE SET

        level =
            COALESCE(
                student_skill.level,
                EXCLUDED.level
            ),

        confidence =
            CASE

                WHEN student_skill.confidence
                     IS NULL
                THEN EXCLUDED.confidence

                ELSE GREATEST(
                    student_skill.confidence,
                    EXCLUDED.confidence
                )

            END,

        -- 기존 verified=True를
        -- 모델이 False로 내릴 수 없음
        verified =
            student_skill.verified,

        updated_at =
            NOW()

    RETURNING
        student_skill_id
    """
)


# ============================================================
# Evidence 저장
# ============================================================

def write_model_output(
    payload: dict,
) -> dict:

    (
        experience,
        mappings,
    ) = validate_output(
        payload
    )


    student_id = int(
        experience[
            "student_id"
        ]
    )


    experience_id = int(
        experience[
            "experience_id"
        ]
    )


    stats = {
        "processed":
            0,

        "student_skills":
            0,

        "evidence_created":
            0,

        "evidence_updated":
            0,
    }


    with engine.begin() as conn:

        for mapping in mappings:

            student_skill_id = int(
                conn.execute(
                    UPSERT_STUDENT_SKILL_SQL,

                    {
                        "student_id":
                            student_id,

                        "skill_id":
                            mapping[
                                "skill_id"
                            ],

                        "level":
                            mapping[
                                "level"
                            ],

                        "confidence":
                            mapping[
                                "confidence"
                            ],
                    },
                ).scalar_one()
            )


            stats[
                "processed"
            ] += 1


            stats[
                "student_skills"
            ] += 1


            existing = (
                conn.execute(
                    text(
                        """
                        SELECT
                            evidence_id

                        FROM student_skill_evidence

                        WHERE
                            student_skill_id =
                                :student_skill_id

                          AND evidence_type =
                                'EXPERIENCE'

                          AND experience_id =
                                :experience_id

                        LIMIT 1
                        """
                    ),

                    {
                        "student_skill_id":
                            student_skill_id,

                        "experience_id":
                            experience_id,
                    },
                ).scalar()
            )


            description = (
                (
                    f"[{MAPPING_METHOD}] "
                    f"{mapping['evidence_text']}"
                )
            )


            if existing is None:

                conn.execute(
                    text(
                        """
                        INSERT INTO student_skill_evidence (
                            student_skill_id,

                            evidence_type,

                            experience_id,

                            description,

                            source_url,

                            weight
                        )

                        VALUES (
                            :student_skill_id,

                            'EXPERIENCE',

                            :experience_id,

                            :description,

                            :source_url,

                            :weight
                        )
                        """
                    ),

                    {
                        "student_skill_id":
                            student_skill_id,

                        "experience_id":
                            experience_id,

                        "description":
                            description,

                        "source_url":
                            experience[
                                "source_url"
                            ],

                        "weight":
                            mapping[
                                "confidence"
                            ],
                    },
                )


                stats[
                    "evidence_created"
                ] += 1


            else:

                conn.execute(
                    text(
                        """
                        UPDATE student_skill_evidence

                        SET
                            description =
                                :description,

                            source_url =
                                :source_url,

                            weight =
                                :weight,

                            updated_at =
                                NOW()

                        WHERE
                            evidence_id =
                                :evidence_id
                        """
                    ),

                    {
                        "description":
                            description,

                        "source_url":
                            experience[
                                "source_url"
                            ],

                        "weight":
                            mapping[
                                "confidence"
                            ],

                        "evidence_id":
                            int(
                                existing
                            ),
                    },
                )


                stats[
                    "evidence_updated"
                ] += 1


            print(
                (
                    f"[SAVED] "
                    f"{mapping['skill_name']} "
                    f"| confidence="
                    f"{mapping['confidence']}"
                )
            )


    return stats


# ============================================================
# 학생 Experience 조회
# ============================================================

def show_experiences(
    student_id: int,
) -> None:

    sql = text(
        """
        SELECT
            experience_id,

            experience_type,
            title,
            organization,

            start_date,
            end_date

        FROM student_experience

        WHERE
            student_id =
                :student_id

        ORDER BY
            experience_id
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql,

                {
                    "student_id":
                        student_id,
                },
            ).mappings().all()
        )


    print(
        "=" * 80
    )

    print(
        (
            f"Student {student_id} "
            "Experiences"
        )
    )

    print(
        "=" * 80
    )


    if not rows:

        print(
            "경험 없음"
        )

        return


    for row in rows:

        print(
            (
                f"[{row['experience_id']}] "
                f"{row['experience_type']:<15} "
                f"| {row['title']}"
            )
        )


# ============================================================
# 학생 Skill + Experience Evidence 조회
# ============================================================

def show_student_skills(
    student_id: int,
) -> None:

    sql = text(
        """
        SELECT
            s.name AS skill_name,

            ss.level,
            ss.confidence,
            ss.verified,

            sse.evidence_type,
            sse.experience_id,

            sse.description,
            sse.weight

        FROM student_skill ss

        JOIN skill s
            ON s.skill_id =
               ss.skill_id

        LEFT JOIN student_skill_evidence sse
            ON sse.student_skill_id =
               ss.student_skill_id

        WHERE
            ss.student_id =
                :student_id

        ORDER BY
            s.name,
            sse.evidence_id
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql,

                {
                    "student_id":
                        student_id,
                },
            ).mappings().all()
        )


    print(
        "=" * 90
    )

    print(
        (
            f"Student {student_id} "
            "Skill Evidence"
        )
    )

    print(
        "=" * 90
    )


    for row in rows:

        print(
            (
                f"{row['skill_name']:<25} "
                f"| confidence="
                f"{row['confidence']} "
                f"| verified="
                f"{row['verified']} "
                f"| evidence="
                f"{row['evidence_type'] or '-'} "
                f"| exp="
                f"{row['experience_id'] or '-'}"
            )
        )


# ============================================================
# DEMO Experience
#
# 팀원 모델 없이도
# 입출력 계약 전체를 검증하기 위한 데이터
# ============================================================

def seed_demo_experience(
    student_id: int,
) -> int:

    title = (
        "딥러닝 이미지 분류 프로젝트"
    )


    description = (
        "Python과 PyTorch를 사용하여 "
        "Deep Learning 기반 이미지 분류 모델을 구현했다. "
        "Computer Vision 데이터셋의 전처리와 학습 파이프라인을 "
        "구성하고 모델 성능을 비교했다. "
        "Git을 사용해 소스 코드를 관리했다."
    )


    with engine.begin() as conn:

        student_exists = (
            conn.execute(
                text(
                    """
                    SELECT
                        student_id

                    FROM student

                    WHERE
                        student_id =
                            :student_id
                    """
                ),

                {
                    "student_id":
                        student_id,
                },
            ).scalar()
        )


        if student_exists is None:

            raise RuntimeError(
                (
                    "학생이 없습니다: "
                    f"{student_id}"
                )
            )


        existing = (
            conn.execute(
                text(
                    """
                    SELECT
                        experience_id

                    FROM student_experience

                    WHERE
                        student_id =
                            :student_id

                      AND title =
                            :title

                    LIMIT 1
                    """
                ),

                {
                    "student_id":
                        student_id,

                    "title":
                        title,
                },
            ).scalar()
        )


        if existing is not None:

            experience_id = int(
                existing
            )


        else:

            experience_id = int(
                conn.execute(
                    text(
                        """
                        INSERT INTO student_experience (
                            student_id,

                            experience_type,

                            title,

                            organization,

                            description,

                            raw_metadata
                        )

                        VALUES (
                            :student_id,

                            'PROJECT',

                            :title,

                            'CareerQuest Demo',

                            :description,

                            CAST(
                                :raw_metadata
                                AS jsonb
                            )
                        )

                        RETURNING
                            experience_id
                        """
                    ),

                    {
                        "student_id":
                            student_id,

                        "title":
                            title,

                        "description":
                            description,

                        "raw_metadata":
                            json.dumps(
                                {
                                    "demo":
                                        True,

                                    "purpose":
                                        (
                                            "StudentExperience "
                                            "integration test"
                                        ),
                                },
                                ensure_ascii=False,
                            ),
                    },
                ).scalar_one()
            )


    print(
        "=" * 80
    )

    print(
        "DEMO EXPERIENCE"
    )

    print(
        "=" * 80
    )


    print(
        (
            "experience_id : "
            f"{experience_id}"
        )
    )


    print(
        (
            "title         : "
            f"{title}"
        )
    )


    print(
        (
            "description   : "
            f"{description}"
        )
    )


    return experience_id


# ============================================================
# MOCK 모델 출력
#
# 실제 팀원 모델 연동 전 writer 검증용.
# ============================================================

def build_mock_output(
    experience_id: int,
) -> Path:

    input_payload = (
        build_mapping_input(
            experience_id
        )
    )


    content = (
        input_payload[
            "content"
        ]
    )


    target_names = (
        "PyTorch",
        "Deep Learning",
        "Computer Vision",
        "Git",
    )


    skill_by_name = {
        normalize_text(
            skill[
                "name"
            ]
        ):
            skill

        for skill
        in input_payload[
            "allowed_skills"
        ]
    }


    mappings = []


    for name in target_names:

        skill = (
            skill_by_name.get(
                normalize_text(
                    name
                )
            )
        )


        if skill is None:

            continue


        if (
            normalize_text(
                name
            )
            not in normalize_text(
                content
            )
        ):

            continue


        # Experience 설명 전체를 근거로 사용.
        # 실제 content 안의 substring이므로 검증 통과.
        evidence = (
            input_payload[
                "content"
            ]
        )


        mappings.append(
            {
                "skill_id":
                    int(
                        skill[
                            "skill_id"
                        ]
                    ),

                "evidence_text":
                    evidence,

                "confidence":
                    0.95,

                "level":
                    None,

                "reason":
                    (
                        f"학생 경험에서 {name}을 "
                        "직접 사용했다고 명시됨"
                    ),
            }
        )


    output = {
        "contract_version":
            "student_experience_skill_v1",

        "student_id":
            int(
                input_payload[
                    "student_id"
                ]
            ),

        "experience_id":
            int(
                experience_id
            ),

        "mappings":
            mappings,

        "unmapped_skills":
            [],
    }


    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


    path = (
        OUTPUT_DIR
        / (
            f"experience_"
            f"{experience_id}"
            f"_output.json"
        )
    )


    path.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


    print(
        "=" * 80
    )

    print(
        "MOCK MODEL OUTPUT"
    )

    print(
        "=" * 80
    )


    print(
        (
            "Mapping 수 : "
            f"{len(mappings)}"
        )
    )


    for mapping in mappings:

        skill = next(
            (
                x

                for x
                in input_payload[
                    "allowed_skills"
                ]

                if int(
                    x[
                        "skill_id"
                    ]
                )
                == mapping[
                    "skill_id"
                ]
            ),
            None,
        )


        if skill:

            print(
                (
                    f"  -> "
                    f"{skill['name']}"
                )
            )


    print(
        (
            "저장       : "
            f"{path}"
        )
    )


    return path


# ============================================================
# APPLY
# ============================================================

def apply_output_file(
    path: Path,
) -> None:

    payload = (
        load_output(
            path
        )
    )


    stats = (
        write_model_output(
            payload
        )
    )


    print()


    print(
        "=" * 80
    )

    print(
        "StudentExperience Skill 저장 완료"
    )

    print(
        "=" * 80
    )


    print(
        (
            "processed       : "
            f"{stats['processed']}"
        )
    )


    print(
        (
            "Skill upsert    : "
            f"{stats['student_skills']}"
        )
    )


    print(
        (
            "Evidence create : "
            f"{stats['evidence_created']}"
        )
    )


    print(
        (
            "Evidence update : "
            f"{stats['evidence_updated']}"
        )
    )


# ============================================================
# CLI
# ============================================================

def parse_args(
) -> argparse.Namespace:

    parser = (
        argparse.ArgumentParser()
    )


    group = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )


    group.add_argument(
        "--export",
        action="store_true",
    )


    group.add_argument(
        "--apply",
        action="store_true",
    )


    group.add_argument(
        "--list",
        action="store_true",
    )


    group.add_argument(
        "--show",
        action="store_true",
    )


    group.add_argument(
        "--seed-demo",
        action="store_true",
    )


    group.add_argument(
        "--mock",
        action="store_true",
    )


    parser.add_argument(
        "--student-id",
        type=int,
    )


    parser.add_argument(
        "--experience-id",
        type=int,
    )


    parser.add_argument(
        "--file",
        type=str,
    )


    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================

def main(
) -> None:

    args = (
        parse_args()
    )


    if args.list:

        if args.student_id is None:

            raise ValueError(
                "--student-id 필요"
            )


        show_experiences(
            args.student_id
        )

        return


    if args.show:

        if args.student_id is None:

            raise ValueError(
                "--student-id 필요"
            )


        show_student_skills(
            args.student_id
        )

        return


    if args.seed_demo:

        if args.student_id is None:

            raise ValueError(
                "--student-id 필요"
            )


        seed_demo_experience(
            args.student_id
        )

        return


    if args.export:

        if args.experience_id is None:

            raise ValueError(
                "--experience-id 필요"
            )


        export_mapping_input(
            args.experience_id
        )

        return


    if args.mock:

        if args.experience_id is None:

            raise ValueError(
                "--experience-id 필요"
            )


        build_mock_output(
            args.experience_id
        )

        return


    if args.apply:

        if not args.file:

            raise ValueError(
                "--file 필요"
            )


        apply_output_file(
            Path(
                args.file
            )
        )

        return


if __name__ == "__main__":

    main()