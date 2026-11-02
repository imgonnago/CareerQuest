from __future__ import annotations

import argparse
import json
import re
import time

from collections import (
    defaultdict,
)

from typing import Any

from pydantic import (
    BaseModel,
    Field,
)

from sqlalchemy import text

from engine.llm import (
    LLMConfig,
    LLM_MODEL,
    chat,
)

from src.db.database import engine


# ============================================================
# 설정
# ============================================================

TARGET_DEPARTMENT = (
    "인공지능학과"
)

TARGET_CURRICULUM_YEAR = (
    2026
)

MAX_RETRIES = 3

MAX_SKILLS_PER_COURSE = 4

MODEL_PROPOSE_MIN_CONFIDENCE = (
    0.80
)

MODEL_FINAL_MIN_CONFIDENCE = (
    0.90
)

DIRECT_MAPPING_METHOD = (
    "literal_title_v1"
)

QWEN_MAPPING_METHOD = (
    "qwen_2pass_title_v1"
)

# 이전 Gemini 실행 결과가 DB에 남아 있을 경우
# 새 Qwen 매핑 저장 시 함께 정리하기 위한 legacy 값
LEGACY_GEMINI_MAPPING_METHOD = (
    "gemini_2pass_title_v1"
)


QWEN_CONFIG = (
    LLMConfig(
        model=(
            LLM_MODEL
        ),

        temperature=0,

        think=False,
    )
)


GENERIC_LITERAL_TERMS = {
    "ai",
    "ml",
    "sw",
    "it",
    "데이터",
    "분석",
    "개발",
    "프로그래밍",
    "인공지능",
    "컴퓨터",
    "소프트웨어",
}


# ============================================================
# Qwen 출력 스키마
# ============================================================

class ProposedSkill(
    BaseModel
):

    skill_id: int

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    reason: str | None = None


class CourseProposal(
    BaseModel
):

    resource_id: int

    skills: list[
        ProposedSkill
    ] = Field(
        default_factory=list
    )


class ProposalBatch(
    BaseModel
):

    courses: list[
        CourseProposal
    ] = Field(
        default_factory=list
    )


class ReviewDecision(
    BaseModel
):

    resource_id: int

    skill_id: int

    keep: bool

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    reason: str | None = None


class ReviewBatch(
    BaseModel
):

    decisions: list[
        ReviewDecision
    ] = Field(
        default_factory=list
    )


# ============================================================
# 문자열
# ============================================================

def clean_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    return (
        re.sub(
            r"\s+",
            " ",
            str(
                value
            ),
        )
        .strip()
    )


def normalize_key(
    value: Any,
) -> str:

    value = (
        clean_text(
            value
        )
        .lower()
    )

    return re.sub(
        r"[^0-9a-z가-힣+#]+",
        "",
        value,
    )


# ============================================================
# DB 조회
# ============================================================

def load_courses(
    department_name: str = (
        TARGET_DEPARTMENT
    ),

    curriculum_year: int = (
        TARGET_CURRICULUM_YEAR
    ),
) -> list[dict]:

    sql = text(
        """
        SELECT
            r.resource_id,
            r.name,
            r.description,

            cd.course_code,
            cd.year_level,
            cd.semester,
            cd.completion_type,
            cd.curriculum_year,

            d.name AS department_name,

            (
                SELECT
                    sd.source_document_id

                FROM source_document sd

                WHERE
                    sd.resource_id =
                        r.resource_id

                ORDER BY
                    sd.source_document_id

                LIMIT 1
            ) AS source_document_id

        FROM resource r

        JOIN course_detail cd
            ON cd.resource_id =
               r.resource_id

        JOIN department d
            ON d.department_id =
               cd.department_id

        WHERE
            r.resource_type =
                'COURSE'

            AND r.is_active =
                TRUE

            AND d.name =
                :department_name

            AND cd.curriculum_year =
                :curriculum_year

        ORDER BY
            cd.year_level
                NULLS LAST,

            cd.semester
                NULLS LAST,

            cd.course_code
                NULLS LAST,

            r.resource_id
        """
    )

    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql,
                {
                    "department_name":
                        department_name,

                    "curriculum_year":
                        curriculum_year,
                },
            )
            .mappings()
            .all()
        )

    return [
        dict(
            row
        )

        for row in rows
    ]


def load_skills() -> list[dict]:

    sql = text(
        """
        SELECT
            s.skill_id,
            s.name,
            s.normalized_name,
            s.category,

            COALESCE(
                ARRAY_AGG(
                    sa.alias
                    ORDER BY sa.alias
                )
                FILTER (
                    WHERE
                        sa.alias
                        IS NOT NULL
                ),

                ARRAY[]::varchar[]
            ) AS aliases

        FROM skill s

        LEFT JOIN skill_alias sa
            ON sa.skill_id =
               s.skill_id

        WHERE
            s.is_active = TRUE

        GROUP BY
            s.skill_id,
            s.name,
            s.normalized_name,
            s.category

        ORDER BY
            s.skill_id
        """
    )

    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql
            )
            .mappings()
            .all()
        )

    result: list[dict] = []

    for row in rows:

        item = dict(
            row
        )

        item[
            "aliases"
        ] = list(
            item.get(
                "aliases"
            )
            or []
        )

        result.append(
            item
        )

    return result


# ============================================================
# 직접 문자열 후보
# ============================================================

def skill_terms(
    skill: dict,
) -> list[str]:

    values = [
        skill[
            "name"
        ],

        *(
            skill.get(
                "aliases"
            )
            or []
        ),
    ]

    seen: set[str] = set()

    result: list[str] = []

    for value in values:

        term = clean_text(
            value
        )

        key = normalize_key(
            term
        )

        if (
            not term
            or not key
            or key in seen
        ):
            continue

        seen.add(
            key
        )

        result.append(
            term
        )

    return result


def build_literal_candidates(
    courses: list[dict],
    skills: list[dict],
) -> dict[
    int,
    list[dict],
]:

    result: dict[
        int,
        list[dict],
    ] = defaultdict(
        list
    )

    for course in courses:

        resource_id = int(
            course[
                "resource_id"
            ]
        )

        title_key = (
            normalize_key(
                course[
                    "name"
                ]
            )
        )

        desc_key = (
            normalize_key(
                course.get(
                    "description"
                )
            )
        )

        haystack = (
            f"{title_key} "
            f"{desc_key}"
        )

        for skill in skills:

            matched_terms: list[
                str
            ] = []

            for term in (
                skill_terms(
                    skill
                )
            ):

                term_key = (
                    normalize_key(
                        term
                    )
                )

                if not term_key:
                    continue

                if (
                    term_key
                    in GENERIC_LITERAL_TERMS
                ):
                    continue

                if (
                    len(
                        term_key
                    )
                    < 2
                ):
                    continue

                if (
                    term_key
                    in haystack
                ):

                    matched_terms.append(
                        term
                    )

            if not matched_terms:
                continue

            result[
                resource_id
            ].append(
                {
                    "skill_id":
                        int(
                            skill[
                                "skill_id"
                            ]
                        ),

                    "matched_terms":
                        matched_terms,
                }
            )

    return dict(
        result
    )


# ============================================================
# Prompt
# ============================================================

def build_skill_catalog(
    skills: list[dict],
) -> str:

    lines: list[str] = []

    for skill in skills:

        aliases = (
            ", ".join(
                skill.get(
                    "aliases"
                )
                or []
            )
            or "-"
        )

        lines.append(
            (
                f"skill_id="
                f"{skill['skill_id']}"

                f" | name="
                f"{skill['name']}"

                f" | category="
                f"{skill.get('category') or '-'}"

                f" | aliases="
                f"{aliases}"
            )
        )

    return "\n".join(
        lines
    )


def build_course_catalog(
    courses: list[dict],
) -> str:

    lines: list[str] = []

    for course in courses:

        description = (
            clean_text(
                course.get(
                    "description"
                )
            )
            or "없음"
        )

        lines.append(
            (
                f"resource_id="
                f"{course['resource_id']}"

                f" | course_code="
                f"{course.get('course_code') or '-'}"

                f" | name="
                f"{course['name']}"

                f" | description="
                f"{description}"
            )
        )

    return "\n".join(
        lines
    )


def build_proposal_prompt(
    courses: list[dict],

    skills: list[dict],

    literal_candidates: dict[
        int,
        list[dict],
    ],
) -> str:

    literal_lines: list[
        str
    ] = []

    for (
        resource_id,
        items,
    ) in sorted(
        literal_candidates.items()
    ):

        for item in items:

            literal_lines.append(
                (
                    f"resource_id="
                    f"{resource_id}"

                    f" | skill_id="
                    f"{item['skill_id']}"

                    f" | direct_match="
                    f"{','.join(item['matched_terms'])}"
                )
            )

    literal_text = (
        "\n".join(
            literal_lines
        )
        or "없음"
    )

    return f"""
당신은 CareerQuest의 대학 교과목 -> Skill 후보 생성기입니다.

아래 COURSE가 실제로 가르친다고 볼 수 있는 Skill만
반드시 제공된 Skill DB에서 선택하세요.

규칙:

1. 허용된 skill_id만 사용하세요.
2. 새로운 Skill을 만들지 마세요.
3. 과목명과 description에 없는 세부 기술은 추측하지 마세요.
4. 관계가 약하면 skills=[]로 반환하세요.
5. 일반교양, 진로, 취창업 과목은 억지로 기술 Skill에 연결하지 마세요.
6. AI라는 단어만으로 Python/PyTorch/TensorFlow를 추측하지 마세요.
7. 프로그래밍이라는 단어만으로 특정 언어를 추측하지 마세요.
8. 한 과목당 최대 {MAX_SKILLS_PER_COURSE}개만 선택하세요.
9. confidence < {MODEL_PROPOSE_MIN_CONFIDENCE:.2f}이면 선택하지 마세요.
10. 모든 resource_id를 정확히 한 번씩 반환하세요.
11. 직접 문자열 매칭 후보는 강한 참고 근거지만 명백한 오탐은 제외할 수 있습니다.

[허용 Skill]

{build_skill_catalog(skills)}

[COURSE]

{build_course_catalog(courses)}

[직접 문자열 매칭 후보]

{literal_text}
""".strip()


def build_review_prompt(
    courses: list[dict],

    skills: list[dict],

    proposals: dict[
        int,
        list[dict],
    ],

    literal_candidates: dict[
        int,
        list[dict],
    ],
) -> str:

    course_by_id = {
        int(
            course[
                "resource_id"
            ]
        ):
            course

        for course in courses
    }

    skill_by_id = {
        int(
            skill[
                "skill_id"
            ]
        ):
            skill

        for skill in skills
    }

    lines: list[str] = []

    for (
        resource_id,
        items,
    ) in sorted(
        proposals.items()
    ):

        course = (
            course_by_id[
                resource_id
            ]
        )

        for item in items:

            skill = (
                skill_by_id[
                    item[
                        "skill_id"
                    ]
                ]
            )

            lines.append(
                (
                    f"resource_id="
                    f"{resource_id}"

                    f" | course="
                    f"{course['name']}"

                    f" | skill_id="
                    f"{skill['skill_id']}"

                    f" | skill="
                    f"{skill['name']}"

                    f" | proposal_confidence="
                    f"{item['confidence']:.2f}"

                    f" | reason="
                    f"{item.get('reason') or '-'}"
                )
            )

    literal_lines: list[str] = []

    for (
        resource_id,
        items,
    ) in sorted(
        literal_candidates.items()
    ):

        for item in items:

            literal_lines.append(
                (
                    f"resource_id="
                    f"{resource_id}"

                    f" | skill_id="
                    f"{item['skill_id']}"

                    f" | direct_match="
                    f"{','.join(item['matched_terms'])}"
                )
            )

    proposal_text = (
        "\n".join(
            lines
        )
        or "없음"
    )

    literal_text = (
        "\n".join(
            literal_lines
        )
        or "없음"
    )

    return f"""
당신은 CareerQuest COURSE -> Skill 2차 검토자입니다.

1차 후보가 실제 과목 내용과 직접적인 관계가 있는지
엄격하게 다시 판단하세요.

규칙:

1. 아래 1차 후보 pair만 검토하세요.
2. 새로운 resource_id/skill_id를 추가하지 마세요.
3. 과목명만으로 알 수 없는 라이브러리나 도구를 추측하지 마세요.
4. keep=true는 해당 과목이 해당 Skill을 실제로 가르친다고 충분히 볼 수 있을 때만 사용하세요.
5. 최종 confidence < {MODEL_FINAL_MIN_CONFIDENCE:.2f}이면 keep=false로 하세요.
6. 모든 1차 후보 pair를 정확히 한 번 반환하세요.

[1차 후보]

{proposal_text}

[직접 문자열 근거]

{literal_text}
""".strip()


# ============================================================
# Qwen
# ============================================================

def call_qwen_structured(
    prompt: str,

    schema: type[
        BaseModel
    ],
) -> BaseModel:

    last_error: Exception | None = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            print(
                (
                    f"  Qwen 호출 "
                    f"{attempt}/"
                    f"{MAX_RETRIES} "
                    f"| model="
                    f"{LLM_MODEL}"
                )
            )

            message = (
                chat(
                    [
                        {
                            "role":
                                "system",

                            "content":
                                (
                                    "당신은 CareerQuest의 "
                                    "구조화 데이터 분석 모델입니다. "
                                    "제공된 정보만 사용하고 "
                                    "JSON Schema를 정확히 따르세요."
                                ),
                        },
                        {
                            "role":
                                "user",

                            "content":
                                prompt,
                        },
                    ],

                    QWEN_CONFIG,

                    format=(
                        schema
                        .model_json_schema()
                    ),
                )
            )

            content = (
                clean_text(
                    message.get(
                        "content"
                    )
                )
            )

            if not content:

                raise RuntimeError(
                    (
                        "Qwen이 빈 응답을 "
                        "반환했습니다."
                    )
                )

            return (
                schema
                .model_validate_json(
                    content
                )
            )

        except Exception as exc:

            last_error = exc

            if (
                attempt
                >= MAX_RETRIES
            ):
                break

            wait_seconds = (
                2 ** (
                    attempt - 1
                )
            )

            print(
                (
                    "  Qwen 호출 실패 "
                    "-> "
                    f"{wait_seconds}초 후 재시도"
                )
            )

            time.sleep(
                wait_seconds
            )

    raise RuntimeError(
        "Qwen 구조화 호출 실패"
    ) from last_error


# ============================================================
# 결과 검증
# ============================================================

def validate_proposals(
    result: ProposalBatch,

    courses: list[dict],

    skills: list[dict],
) -> dict[
    int,
    list[dict],
]:

    valid_resource_ids = {
        int(
            course[
                "resource_id"
            ]
        )

        for course in courses
    }

    valid_skill_ids = {
        int(
            skill[
                "skill_id"
            ]
        )

        for skill in skills
    }

    output: dict[
        int,
        list[dict],
    ] = {
        resource_id:
            []

        for resource_id
        in valid_resource_ids
    }

    seen_courses: set[int] = (
        set()
    )

    for course_result in (
        result.courses
    ):

        resource_id = int(
            course_result.resource_id
        )

        if (
            resource_id
            not in valid_resource_ids
        ):
            continue

        if (
            resource_id
            in seen_courses
        ):
            continue

        seen_courses.add(
            resource_id
        )

        seen_skills: set[int] = (
            set()
        )

        for item in (
            course_result.skills
        ):

            skill_id = int(
                item.skill_id
            )

            if (
                skill_id
                not in valid_skill_ids
            ):
                continue

            if (
                skill_id
                in seen_skills
            ):
                continue

            if (
                item.confidence
                < MODEL_PROPOSE_MIN_CONFIDENCE
            ):
                continue

            seen_skills.add(
                skill_id
            )

            output[
                resource_id
            ].append(
                {
                    "skill_id":
                        skill_id,

                    "confidence":
                        float(
                            item.confidence
                        ),

                    "reason":
                        (
                            clean_text(
                                item.reason
                            )
                            or None
                        ),
                }
            )

    return output


def validate_review(
    result: ReviewBatch,

    proposals: dict[
        int,
        list[dict],
    ],
) -> dict[
    tuple[
        int,
        int,
    ],
    dict,
]:

    allowed = {
        (
            resource_id,
            int(
                item[
                    "skill_id"
                ]
            ),
        )

        for (
            resource_id,
            items,
        )
        in proposals.items()

        for item in items
    }

    output: dict[
        tuple[
            int,
            int,
        ],
        dict,
    ] = {}

    for decision in (
        result.decisions
    ):

        key = (
            int(
                decision.resource_id
            ),

            int(
                decision.skill_id
            ),
        )

        if key not in allowed:
            continue

        if key in output:
            continue

        output[
            key
        ] = {
            "keep":
                bool(
                    decision.keep
                ),

            "confidence":
                float(
                    decision.confidence
                ),

            "reason":
                (
                    clean_text(
                        decision.reason
                    )
                    or None
                ),
        }

    return output


# ============================================================
# 최종 매핑
# ============================================================

def build_final_mappings(
    courses: list[dict],

    literal_candidates: dict[
        int,
        list[dict],
    ],

    proposals: dict[
        int,
        list[dict],
    ],

    reviewed: dict[
        tuple[
            int,
            int,
        ],
        dict,
    ],
) -> dict[
    int,
    list[dict],
]:

    result: dict[
        int,
        list[dict],
    ] = {
        int(
            course[
                "resource_id"
            ]
        ):
            []

        for course in courses
    }

    # 직접 문자열 매핑
    for (
        resource_id,
        items,
    ) in (
        literal_candidates.items()
    ):

        for item in items:

            result[
                resource_id
            ].append(
                {
                    "skill_id":
                        int(
                            item[
                                "skill_id"
                            ]
                        ),

                    "confidence":
                        0.98,

                    "reason":
                        (
                            "교과목명/설명에서 "
                            "Skill 이름 또는 alias "
                            "직접 확인"
                        ),

                    "method":
                        DIRECT_MAPPING_METHOD,
                }
            )

    # Qwen 2차 통과
    for (
        resource_id,
        items,
    ) in proposals.items():

        existing = {
            item[
                "skill_id"
            ]

            for item
            in result[
                resource_id
            ]
        }

        for item in items:

            skill_id = int(
                item[
                    "skill_id"
                ]
            )

            if (
                skill_id
                in existing
            ):
                continue

            decision = (
                reviewed.get(
                    (
                        resource_id,
                        skill_id,
                    )
                )
            )

            if not decision:
                continue

            if not decision[
                "keep"
            ]:
                continue

            if (
                decision[
                    "confidence"
                ]
                < MODEL_FINAL_MIN_CONFIDENCE
            ):
                continue

            result[
                resource_id
            ].append(
                {
                    "skill_id":
                        skill_id,

                    "confidence":
                        float(
                            decision[
                                "confidence"
                            ]
                        ),

                    "reason":
                        (
                            decision.get(
                                "reason"
                            )
                            or item.get(
                                "reason"
                            )
                        ),

                    "method":
                        QWEN_MAPPING_METHOD,
                }
            )

            existing.add(
                skill_id
            )

        result[
            resource_id
        ] = (
            result[
                resource_id
            ][
                :
                MAX_SKILLS_PER_COURSE
            ]
        )

    return result


# ============================================================
# 출력
# ============================================================

def print_summary(
    courses: list[dict],

    skills: list[dict],

    mappings: dict[
        int,
        list[dict],
    ],
) -> None:

    skill_by_id = {
        int(
            skill[
                "skill_id"
            ]
        ):
            skill

        for skill in skills
    }

    mapped_courses = 0
    direct_count = 0
    qwen_count = 0

    print(
        "\n"
        + "=" * 80
    )

    print(
        "최종 COURSE -> SKILL 후보"
    )

    print(
        "=" * 80
    )

    for course in courses:

        resource_id = int(
            course[
                "resource_id"
            ]
        )

        selected = (
            mappings.get(
                resource_id,
                [],
            )
        )

        print(
            (
                f"\n["
                f"{course.get('course_code') or '-'}"
                f"] "
                f"{course['name']}"
            )
        )

        if not selected:

            print(
                "  -> Skill 매핑 없음"
            )

            continue

        mapped_courses += 1

        for item in selected:

            skill = (
                skill_by_id[
                    item[
                        "skill_id"
                    ]
                ]
            )

            if (
                item[
                    "method"
                ]
                == DIRECT_MAPPING_METHOD
            ):

                direct_count += 1

            else:

                qwen_count += 1

            print(
                (
                    f"  -> "
                    f"{skill['name']} "
                    f"| confidence="
                    f"{item['confidence']:.2f} "
                    f"| method="
                    f"{item['method']}"
                )
            )

            if item.get(
                "reason"
            ):

                print(
                    (
                        "     reason: "
                        f"{item['reason']}"
                    )
                )

    print(
        "\n"
        + "-" * 80
    )

    print(
        f"전체 COURSE: "
        f"{len(courses)}개"
    )

    print(
        (
            "Skill 연결 COURSE: "
            f"{mapped_courses}개"
        )
    )

    print(
        (
            "매핑 없는 COURSE: "
            f"{len(courses) - mapped_courses}개"
        )
    )

    print(
        (
            "직접 문자열 매핑: "
            f"{direct_count}개"
        )
    )

    print(
        (
            "Qwen 2차 검토 매핑: "
            f"{qwen_count}개"
        )
    )

    print(
        (
            "최종 RESOURCE_SKILL 후보: "
            f"{direct_count + qwen_count}개"
        )
    )


# ============================================================
# DB 저장
# ============================================================

def save_mappings(
    courses: list[dict],

    mappings: dict[
        int,
        list[dict],
    ],
) -> int:

    delete_sql = text(
        """
        DELETE FROM resource_skill

        WHERE
            resource_id =
                :resource_id

            AND relation_type =
                'TAUGHT'

            AND mapping_method IN (
                :direct_method,
                :qwen_method,
                :legacy_gemini_method
            )
        """
    )

    upsert_sql = text(
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

            'TAUGHT',

            :evidence_text,
            :mapping_method,
            :confidence,
            FALSE,

            :source_document_id
        )

        ON CONFLICT (
            resource_id,
            skill_id,
            relation_type
        )

        DO UPDATE SET

            evidence_text =
                EXCLUDED.evidence_text,

            mapping_method =
                EXCLUDED.mapping_method,

            confidence =
                EXCLUDED.confidence,

            verified =
                FALSE,

            source_document_id =
                COALESCE(
                    EXCLUDED.source_document_id,
                    resource_skill.source_document_id
                ),

            updated_at =
                NOW()
        """
    )

    course_by_id = {
        int(
            course[
                "resource_id"
            ]
        ):
            course

        for course in courses
    }

    saved = 0

    with engine.begin() as conn:

        for resource_id in (
            course_by_id
        ):

            conn.execute(
                delete_sql,
                {
                    "resource_id":
                        resource_id,

                    "direct_method":
                        DIRECT_MAPPING_METHOD,

                    "qwen_method":
                        QWEN_MAPPING_METHOD,

                    "legacy_gemini_method":
                        LEGACY_GEMINI_MAPPING_METHOD,
                },
            )

        for (
            resource_id,
            items,
        ) in mappings.items():

            course = (
                course_by_id[
                    resource_id
                ]
            )

            for item in items:

                conn.execute(
                    upsert_sql,
                    {
                        "resource_id":
                            resource_id,

                        "skill_id":
                            int(
                                item[
                                    "skill_id"
                                ]
                            ),

                        "evidence_text":
                            (
                                f"COURSE: "
                                f"{course['name']} "
                                f"| "
                                f"{item.get('reason') or ''}"
                            ).strip(),

                        "mapping_method":
                            item[
                                "method"
                            ],

                        "confidence":
                            item[
                                "confidence"
                            ],

                        "source_document_id":
                            course.get(
                                "source_document_id"
                            ),
                    },
                )

                saved += 1

    return saved


# ============================================================
# 실행
# ============================================================

def run(
    *,
    save: bool = False,
) -> dict:

    print(
        "=" * 80
    )

    print(
        (
            "CareerQuest - "
            "COURSE -> Skill Mapping "
            "(Qwen)"
        )
    )

    print(
        "=" * 80
    )

    courses = (
        load_courses()
    )

    skills = (
        load_skills()
    )

    if not courses:

        raise RuntimeError(
            "대상 COURSE가 없습니다."
        )

    if not skills:

        raise RuntimeError(
            "활성 Skill이 없습니다."
        )

    print(
        (
            f"COURSE: "
            f"{len(courses)}개"
        )
    )

    print(
        (
            f"Skill: "
            f"{len(skills)}개"
        )
    )

    print(
        (
            f"Model: "
            f"{LLM_MODEL}"
        )
    )

    literal_candidates = (
        build_literal_candidates(
            courses,
            skills,
        )
    )

    literal_count = sum(
        len(
            items
        )

        for items
        in literal_candidates.values()
    )

    print(
        (
            "직접 문자열 후보: "
            f"{literal_count}개"
        )
    )

    proposal_result = (
        call_qwen_structured(
            build_proposal_prompt(
                courses,
                skills,
                literal_candidates,
            ),

            ProposalBatch,
        )
    )

    proposals = (
        validate_proposals(
            proposal_result,
            courses,
            skills,
        )
    )

    proposal_count = sum(
        len(
            items
        )

        for items
        in proposals.values()
    )

    print(
        (
            "Qwen 1차 후보: "
            f"{proposal_count}개"
        )
    )

    review_result = (
        call_qwen_structured(
            build_review_prompt(
                courses,
                skills,
                proposals,
                literal_candidates,
            ),

            ReviewBatch,
        )
    )

    reviewed = (
        validate_review(
            review_result,
            proposals,
        )
    )

    mappings = (
        build_final_mappings(
            courses,
            literal_candidates,
            proposals,
            reviewed,
        )
    )

    print_summary(
        courses,
        skills,
        mappings,
    )

    saved = 0

    if save:

        saved = (
            save_mappings(
                courses,
                mappings,
            )
        )

        print(
            (
                "\nDB 저장 완료: "
                f"{saved}개 "
                "RESOURCE_SKILL"
            )
        )

    else:

        print(
            (
                "\nDry run입니다. "
                "DB에는 저장하지 않았습니다. "
                "--save 사용 시 저장합니다."
            )
        )

    return {
        "course_count":
            len(
                courses
            ),

        "skill_count":
            len(
                skills
            ),

        "mapping_count":
            sum(
                len(
                    items
                )

                for items
                in mappings.values()
            ),

        "saved_count":
            saved,
    }


def parse_args() -> argparse.Namespace:

    parser = (
        argparse.ArgumentParser()
    )

    parser.add_argument(
        "--save",
        action="store_true",
    )

    return parser.parse_args()


def main() -> None:

    args = (
        parse_args()
    )

    result = (
        run(
            save=(
                args.save
            )
        )
    )

    print(
        "\n"
        + json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()