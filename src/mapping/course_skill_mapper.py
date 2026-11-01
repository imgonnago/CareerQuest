from __future__ import annotations

import argparse
import os
import re
import time
from collections import defaultdict
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.db.database import engine


# ============================================================
# 기본 설정
# ============================================================

TARGET_DEPARTMENT = "인공지능학과"
TARGET_CURRICULUM_YEAR = 2026

EXPECTED_COURSE_COUNT = 66
EXPECTED_SKILL_COUNT = 45

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash",
)

MAX_RETRIES = 5

# 한 과목에 너무 많은 Skill이 붙는 것을 방지
MAX_SKILLS_PER_COURSE = 4

# Gemini 1차 후보 기준
GEMINI_PROPOSE_MIN_CONFIDENCE = 0.80

# Gemini 2차 최종 통과 기준
GEMINI_FINAL_MIN_CONFIDENCE = 0.90

# DB mapping_method 값
DIRECT_MAPPING_METHOD = "literal_title_v1"
GEMINI_MAPPING_METHOD = "gemini_2pass_title_v1"


# ============================================================
# 너무 일반적이라 문자열 직접 매칭에서 제외할 표현
#
# 예:
# "AI융합설계"에 AI라는 단어가 있다고
# Skill을 자동 확정하지 않음.
# ============================================================

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
# Gemini 1차 출력 스키마
# ============================================================

class ProposedSkill(BaseModel):
    skill_id: int

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    reason: str | None = None


class CourseProposal(BaseModel):
    resource_id: int

    skills: list[ProposedSkill] = Field(
        default_factory=list
    )


class ProposalBatch(BaseModel):
    courses: list[CourseProposal] = Field(
        default_factory=list
    )


# ============================================================
# Gemini 2차 검토 출력 스키마
# ============================================================

class ReviewDecision(BaseModel):
    resource_id: int
    skill_id: int

    keep: bool

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    reason: str | None = None


class ReviewBatch(BaseModel):
    decisions: list[ReviewDecision] = Field(
        default_factory=list
    )


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


def normalize_for_match(
    value: Any,
) -> str:

    value = clean_text(
        value
    ).lower()

    return re.sub(
        r"[^0-9a-zA-Z가-힣+#.]",
        "",
        value,
    )


# ============================================================
# COURSE 조회
# ============================================================

def load_courses() -> list[dict]:

    sql = text(
        """
        SELECT
            r.resource_id,
            r.name,
            r.description,
            r.canonical_url,

            cd.course_code,
            cd.year_level,
            cd.semester,
            cd.completion_type,
            cd.credits,
            cd.curriculum_year,

            d.department_id,
            d.name AS department_name

        FROM resource r

        JOIN course_detail cd
            ON cd.resource_id = r.resource_id

        JOIN department d
            ON d.department_id = cd.department_id

        WHERE r.resource_type = 'COURSE'
          AND cd.curriculum_year = :curriculum_year
          AND d.name = :department_name

        ORDER BY
            cd.year_level NULLS LAST,
            cd.semester NULLS LAST,
            cd.course_code NULLS LAST,
            r.resource_id
        """
    )


    with engine.connect() as conn:

        rows = conn.execute(
            sql,

            {
                "curriculum_year":
                    TARGET_CURRICULUM_YEAR,

                "department_name":
                    TARGET_DEPARTMENT,
            },
        ).mappings().all()


    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# Skill + Alias 조회
# ============================================================

def load_skills() -> list[dict]:

    sql = text(
        """
        SELECT
            s.skill_id,
            s.name,
            s.normalized_name,
            s.category,
            s.description,

            COALESCE(
                string_agg(
                    sa.alias,
                    ', '
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
            s.normalized_name,
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


    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# 교육과정 SourceDocument 찾기
#
# 못 찾으면 NULL로 저장해도 됨.
# ============================================================

def load_curriculum_source_document_id(
    courses: list[dict],
) -> int | None:

    urls = {
        clean_text(
            course.get(
                "canonical_url"
            )
        )

        for course
        in courses

        if clean_text(
            course.get(
                "canonical_url"
            )
        )
    }


    if not urls:
        return None


    sql = text(
        """
        SELECT
            source_document_id

        FROM source_document

        WHERE source_url = :source_url

        ORDER BY
            source_document_id DESC

        LIMIT 1
        """
    )


    with engine.connect() as conn:

        for url in urls:

            source_document_id = (
                conn.execute(
                    sql,

                    {
                        "source_url":
                            url,
                    },
                ).scalar()
            )


            if (
                source_document_id
                is not None
            ):

                return int(
                    source_document_id
                )


    return None


# ============================================================
# Skill 이름 + Alias 목록
# ============================================================

def build_skill_terms(
    skill: dict,
) -> list[str]:

    terms: set[
        str
    ] = set()


    for value in (
        skill.get(
            "name"
        ),

        skill.get(
            "normalized_name"
        ),
    ):

        value = clean_text(
            value
        )

        if value:

            terms.add(
                value
            )


    aliases_raw = clean_text(
        skill.get(
            "aliases"
        )
    )


    if aliases_raw:

        for alias in aliases_raw.split(
            ","
        ):

            alias = clean_text(
                alias
            )

            if alias:

                terms.add(
                    alias
                )


    return sorted(
        terms
    )


# ============================================================
# 1차: 직접 문자열 매칭
#
# 가장 강한 근거.
# 과목명에 실제 Skill명/Alias가 들어있는 경우.
# ============================================================

def find_literal_candidates(
    courses: list[dict],
    skills: list[dict],
) -> dict[int, list[dict]]:

    result: dict[
        int,
        list[dict]
    ] = defaultdict(
        list
    )


    for course in courses:

        resource_id = int(
            course[
                "resource_id"
            ]
        )


        course_name_norm = (
            normalize_for_match(
                course[
                    "name"
                ]
            )
        )


        if not course_name_norm:
            continue


        for skill in skills:

            matched_terms: list[
                str
            ] = []


            for term in build_skill_terms(
                skill
            ):

                term_norm = (
                    normalize_for_match(
                        term
                    )
                )


                # 너무 짧으면 오탐 위험
                if len(
                    term_norm
                ) < 3:

                    continue


                # 너무 일반적인 표현 제외
                if (
                    term_norm
                    in GENERIC_LITERAL_TERMS
                ):

                    continue


                if (
                    term_norm
                    in course_name_norm
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

                    "skill_name":
                        skill[
                            "name"
                        ],

                    "matched_terms":
                        sorted(
                            set(
                                matched_terms
                            )
                        ),

                    "confidence":
                        0.98,

                    "method":
                        DIRECT_MAPPING_METHOD,

                    "reason":
                        (
                            "교과목명에 Skill 이름/alias가 "
                            "직접 포함됨: "
                            + ", ".join(
                                sorted(
                                    set(
                                        matched_terms
                                    )
                                )
                            )
                        ),
                }
            )


    return dict(
        result
    )


# ============================================================
# Skill 목록 문자열
# ============================================================

def build_skill_catalog(
    skills: list[dict],
) -> str:

    lines: list[
        str
    ] = []


    for skill in skills:

        line = (
            f"skill_id="
            f"{skill['skill_id']}"
            f" | name="
            f"{clean_text(skill['name'])}"
        )


        category = clean_text(
            skill.get(
                "category"
            )
        )


        aliases = clean_text(
            skill.get(
                "aliases"
            )
        )


        description = clean_text(
            skill.get(
                "description"
            )
        )


        if category:

            line += (
                f" | category="
                f"{category}"
            )


        if aliases:

            line += (
                f" | aliases="
                f"{aliases}"
            )


        if description:

            line += (
                f" | description="
                f"{description}"
            )


        lines.append(
            line
        )


    return "\n".join(
        lines
    )


# ============================================================
# COURSE 목록 문자열
# ============================================================

def build_course_catalog(
    courses: list[dict],
) -> str:

    lines: list[
        str
    ] = []


    for course in courses:

        line = (
            f"resource_id="
            f"{course['resource_id']}"

            f" | code="
            f"{clean_text(course.get('course_code'))}"

            f" | name="
            f"{clean_text(course['name'])}"

            f" | year="
            f"{course.get('year_level')}"

            f" | semester="
            f"{clean_text(course.get('semester'))}"

            f" | completion_type="
            f"{clean_text(course.get('completion_type'))}"
        )


        description = clean_text(
            course.get(
                "description"
            )
        )


        if description:

            line += (
                f" | description="
                f"{description}"
            )


        lines.append(
            line
        )


    return "\n".join(
        lines
    )


# ============================================================
# Gemini 1차 Prompt
# ============================================================

def build_proposal_prompt(
    courses: list[dict],
    skills: list[dict],
    literal_candidates: dict[
        int,
        list[dict]
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
        or
        "없음"
    )


    return f"""
당신은 CareerQuest의 대학 교과목 → Skill 후보 생성기입니다.

목표:
아래 COURSE가 실제로 가르친다고 볼 수 있는 Skill만,
반드시 제공된 Skill DB 안에서 선택하세요.

중요:
현재 대부분의 COURSE에는 강의계획서/상세 설명이 없습니다.
따라서 과목명만으로 알 수 없는 세부 기술은 추측하지 마세요.

규칙:

1. 반드시 아래 허용 Skill의 skill_id만 사용하세요.

2. 새로운 Skill을 만들지 마세요.

3. 과목명과 제공된 description만 근거로 사용하세요.

4. 기술적 관계가 약하면 skills=[] 로 반환하세요.

5. 일반교양, 진로, 취창업, 의사소통 과목은
   억지로 기술 Skill에 연결하지 마세요.

6. "AI"라는 단어만으로
   Python, PyTorch, TensorFlow 등을 추측하지 마세요.

7. "프로그래밍"이라는 단어만으로
   특정 프로그래밍 언어를 추측하지 마세요.

8. "데이터베이스프로그래밍"처럼
   과목명이 특정 기술 도메인을 명확히 가리키는 경우에는
   해당 도메인 Skill을 후보로 제안할 수 있습니다.

9. 한 과목당 최대 {MAX_SKILLS_PER_COURSE}개만 선택하세요.

10. confidence < {GEMINI_PROPOSE_MIN_CONFIDENCE:.2f}
    수준이면 선택하지 마세요.

11. 모든 resource_id를 정확히 한 번씩 반환하세요.

12. 아래 직접 문자열 매칭은 강한 참고 근거입니다.
    다만 명백한 오탐이라면 그대로 따를 필요는 없습니다.


====================
허용 Skill
====================

{build_skill_catalog(skills)}


====================
COURSE
====================

{build_course_catalog(courses)}


====================
직접 문자열 매칭 후보
====================

{literal_text}
""".strip()


# ============================================================
# Gemini 공통 호출
# ============================================================

def call_gemini_structured(
    prompt: str,
    schema: type[BaseModel],
) -> BaseModel:

    if not (
        os.getenv(
            "GEMINI_API_KEY"
        )
        or
        os.getenv(
            "GOOGLE_API_KEY"
        )
    ):

        raise RuntimeError(
            (
                "GEMINI_API_KEY 또는 "
                "GOOGLE_API_KEY가 "
                "설정되어 있지 않습니다."
            )
        )


    client = (
        genai.Client()
    )


    last_error: (
        Exception | None
    ) = None


    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            print(
                (
                    f"  Gemini 호출 "
                    f"{attempt}/"
                    f"{MAX_RETRIES}"
                )
            )


            response = (
                client.models.generate_content(
                    model=(
                        GEMINI_MODEL
                    ),

                    contents=[
                        prompt
                    ],

                    config=(
                        types.GenerateContentConfig(
                            response_mime_type=(
                                "application/json"
                            ),

                            response_schema=(
                                schema
                            ),

                            temperature=0,
                        )
                    ),
                )
            )


            parsed = getattr(
                response,
                "parsed",
                None,
            )


            if parsed is not None:

                return parsed


            if not response.text:

                raise RuntimeError(
                    (
                        "Gemini가 빈 응답을 "
                        "반환했습니다."
                    )
                )


            return (
                schema.model_validate_json(
                    response.text
                )
            )


        except Exception as exc:

            last_error = (
                exc
            )


            message = str(
                exc
            ).lower()


            retryable = any(
                token in message

                for token in (
                    "503",
                    "429",
                    "unavailable",
                    "resource_exhausted",
                    "high demand",
                    "temporarily",
                )
            )


            if (
                not retryable
                or attempt >= MAX_RETRIES
            ):

                raise


            wait_seconds = (
                2 ** attempt
            )


            print(
                (
                    "  일시적 API 오류 "
                    "-> "
                    f"{wait_seconds}초 후 재시도"
                )
            )


            time.sleep(
                wait_seconds
            )


    raise RuntimeError(
        "Gemini 호출 실패"
    ) from last_error


# ============================================================
# Gemini 1차 결과 검증
# ============================================================

def validate_proposals(
    result: ProposalBatch,
    courses: list[dict],
    skills: list[dict],
) -> dict[int, list[dict]]:

    valid_resource_ids = {
        int(
            course[
                "resource_id"
            ]
        )

        for course
        in courses
    }


    valid_skill_ids = {
        int(
            skill[
                "skill_id"
            ]
        )

        for skill
        in skills
    }


    returned_resource_ids = [
        int(
            item.resource_id
        )

        for item
        in result.courses
    ]


    if (
        len(
            returned_resource_ids
        )
        != len(
            set(
                returned_resource_ids
            )
        )
    ):

        raise RuntimeError(
            (
                "1차 Gemini 결과에 "
                "중복 resource_id가 있습니다."
            )
        )


    returned_set = set(
        returned_resource_ids
    )


    if (
        returned_set
        != valid_resource_ids
    ):

        missing = sorted(
            valid_resource_ids
            - returned_set
        )


        unknown = sorted(
            returned_set
            - valid_resource_ids
        )


        raise RuntimeError(
            (
                "1차 Gemini "
                "resource_id 불일치 "
                f"| missing={missing} "
                f"| unknown={unknown}"
            )
        )


    normalized: dict[
        int,
        list[dict]
    ] = {}


    for item in result.courses:

        resource_id = int(
            item.resource_id
        )


        per_skill: dict[
            int,
            dict
        ] = {}


        for choice in item.skills:

            skill_id = int(
                choice.skill_id
            )


            if (
                skill_id
                not in valid_skill_ids
            ):

                raise RuntimeError(
                    (
                        "Skill DB에 없는 "
                        "skill_id 반환: "
                        f"{skill_id}"
                    )
                )


            confidence = float(
                choice.confidence
            )


            if (
                confidence
                < GEMINI_PROPOSE_MIN_CONFIDENCE
            ):

                continue


            candidate = {
                "skill_id":
                    skill_id,

                "confidence":
                    confidence,

                "reason":
                    clean_text(
                        choice.reason
                    ),
            }


            previous = (
                per_skill.get(
                    skill_id
                )
            )


            if (
                previous is None
                or confidence
                > previous[
                    "confidence"
                ]
            ):

                per_skill[
                    skill_id
                ] = candidate


        normalized[
            resource_id
        ] = sorted(
            per_skill.values(),

            key=lambda row:
                row[
                    "confidence"
                ],

            reverse=True,
        )[
            :MAX_SKILLS_PER_COURSE
        ]


    return normalized


# ============================================================
# Gemini 2차 Prompt
# ============================================================

def build_review_prompt(
    courses: list[dict],
    skills: list[dict],

    proposals: dict[
        int,
        list[dict]
    ],

    literal_candidates: dict[
        int,
        list[dict]
    ],
) -> str:

    course_by_id = {
        int(
            course[
                "resource_id"
            ]
        ):
            course

        for course
        in courses
    }


    skill_by_id = {
        int(
            skill[
                "skill_id"
            ]
        ):
            skill

        for skill
        in skills
    }


    literal_pairs = {
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
        ) in (
            literal_candidates.items()
        )

        for item
        in items
    }


    proposal_lines: list[
        str
    ] = []


    for resource_id in sorted(
        proposals
    ):

        course = (
            course_by_id[
                resource_id
            ]
        )


        for proposal in (
            proposals[
                resource_id
            ]
        ):

            skill_id = int(
                proposal[
                    "skill_id"
                ]
            )


            skill = (
                skill_by_id[
                    skill_id
                ]
            )


            proposal_lines.append(
                (
                    f"resource_id="
                    f"{resource_id}"

                    f" | course="
                    f"{clean_text(course['name'])}"

                    f" | skill_id="
                    f"{skill_id}"

                    f" | skill="
                    f"{clean_text(skill['name'])}"

                    f" | proposal_confidence="
                    f"{proposal['confidence']:.2f}"

                    f" | direct_literal="
                    f"{'YES' if (resource_id, skill_id) in literal_pairs else 'NO'}"

                    f" | proposal_reason="
                    f"{clean_text(proposal['reason'])}"
                )
            )


    proposals_text = (
        "\n".join(
            proposal_lines
        )
        or
        "후보 없음"
    )


    return f"""
당신은 CareerQuest COURSE → Skill 매핑의 2차 검토자입니다.

1차 모델이 만든 후보를 다시 검토해서,
과목명/description만으로 충분히 방어 가능한 관계만 남기세요.

이 단계는 후보를 추가하는 단계가 아닙니다.
아래 1차 후보 각각에 대해 keep=true/false를 결정하세요.

엄격한 기준:

1. 제공된 후보만 검토하세요.
   새로운 (resource_id, skill_id) 조합을 만들지 마세요.

2. 과목명 또는 description에서 해당 Skill을 직접 확인할 수 있거나,
   기술 도메인이 사실상 동일한 경우에만 keep=true.

3. 별도의 강의계획서가 있어야 알 수 있는 구현 기술은 keep=false.

4. "AI" → Python/PyTorch/TensorFlow 같은 추론은 reject.

5. "프로그래밍" → 특정 언어 추론은 reject.

6. 일반교양/진로/취업 과목의 기술 Skill 연결은
   특별한 직접 근거가 없으면 reject.

7. direct_literal=YES는 강한 근거입니다.

8. keep=true라도 최종 confidence는
   보수적으로 다시 평가하세요.

9. 최종 confidence < {GEMINI_FINAL_MIN_CONFIDENCE:.2f}이면
   keep=false.

10. 모든 1차 후보 조합을 정확히 한 번씩 반환하세요.


====================
Skill 목록
====================

{build_skill_catalog(skills)}


====================
COURSE 목록
====================

{build_course_catalog(courses)}


====================
1차 후보
====================

{proposals_text}
""".strip()


# ============================================================
# 2차 Gemini 결과 검증
# ============================================================

def validate_review(
    result: ReviewBatch,

    proposals: dict[
        int,
        list[dict]
    ],

    skills: list[dict],
) -> dict[
    tuple[int, int],
    dict
]:

    expected_pairs = {
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
        ) in proposals.items()

        for item
        in items
    }


    valid_skill_ids = {
        int(
            skill[
                "skill_id"
            ]
        )

        for skill
        in skills
    }


    returned_pairs: set[
        tuple[int, int]
    ] = set()


    reviewed: dict[
        tuple[int, int],
        dict
    ] = {}


    for decision in (
        result.decisions
    ):

        pair = (
            int(
                decision.resource_id
            ),

            int(
                decision.skill_id
            ),
        )


        if pair in returned_pairs:

            raise RuntimeError(
                (
                    "2차 검토 결과 중복: "
                    f"{pair}"
                )
            )


        if pair not in expected_pairs:

            raise RuntimeError(
                (
                    "2차 검토가 존재하지 않는 "
                    "후보를 반환: "
                    f"{pair}"
                )
            )


        if (
            pair[1]
            not in valid_skill_ids
        ):

            raise RuntimeError(
                (
                    "2차 검토가 잘못된 "
                    "skill_id 반환: "
                    f"{pair[1]}"
                )
            )


        returned_pairs.add(
            pair
        )


        reviewed[
            pair
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
                clean_text(
                    decision.reason
                ),
        }


    if (
        returned_pairs
        != expected_pairs
    ):

        missing = sorted(
            expected_pairs
            - returned_pairs
        )


        raise RuntimeError(
            (
                "2차 검토 누락 후보: "
                f"{missing}"
            )
        )


    return reviewed


# ============================================================
# 최종 매핑 병합
# ============================================================

def build_final_mappings(
    courses: list[dict],

    literal_candidates: dict[
        int,
        list[dict]
    ],

    proposals: dict[
        int,
        list[dict]
    ],

    reviewed: dict[
        tuple[int, int],
        dict
    ],
) -> dict[int, list[dict]]:

    result: dict[
        int,
        dict[
            int,
            dict
        ]
    ] = {
        int(
            course[
                "resource_id"
            ]
        ):
            {}

        for course
        in courses
    }


    # --------------------------------------------------------
    # 1. 직접 문자열 매칭
    # --------------------------------------------------------

    for (
        resource_id,
        items,
    ) in (
        literal_candidates.items()
    ):

        for item in items:

            skill_id = int(
                item[
                    "skill_id"
                ]
            )


            result[
                resource_id
            ][
                skill_id
            ] = {
                "skill_id":
                    skill_id,

                "confidence":
                    float(
                        item[
                            "confidence"
                        ]
                    ),

                "method":
                    DIRECT_MAPPING_METHOD,

                "reason":
                    item[
                        "reason"
                    ],
            }


    # --------------------------------------------------------
    # 2. Gemini 2차 통과 후보
    # --------------------------------------------------------

    for (
        resource_id,
        items,
    ) in (
        proposals.items()
    ):

        for proposal in items:

            skill_id = int(
                proposal[
                    "skill_id"
                ]
            )


            decision = (
                reviewed[
                    (
                        resource_id,
                        skill_id,
                    )
                ]
            )


            if not decision[
                "keep"
            ]:

                continue


            if (
                decision[
                    "confidence"
                ]
                < GEMINI_FINAL_MIN_CONFIDENCE
            ):

                continue


            # 이미 직접 매칭으로 들어간 경우
            # direct가 더 강한 근거이므로 유지
            if (
                skill_id
                in result[
                    resource_id
                ]
            ):

                continue


            result[
                resource_id
            ][
                skill_id
            ] = {
                "skill_id":
                    skill_id,

                "confidence":
                    float(
                        decision[
                            "confidence"
                        ]
                    ),

                "method":
                    GEMINI_MAPPING_METHOD,

                "reason":
                    decision[
                        "reason"
                    ],
            }


    final: dict[
        int,
        list[dict]
    ] = {}


    for (
        resource_id,
        per_skill,
    ) in result.items():

        final[
            resource_id
        ] = sorted(
            per_skill.values(),

            key=lambda row:
                row[
                    "confidence"
                ],

            reverse=True,
        )[
            :MAX_SKILLS_PER_COURSE
        ]


    return final


# ============================================================
# 결과 미리보기
# ============================================================

def print_preview(
    courses: list[dict],
    skills: list[dict],

    mappings: dict[
        int,
        list[dict]
    ],
) -> None:

    course_by_id = {
        int(
            course[
                "resource_id"
            ]
        ):
            course

        for course
        in courses
    }


    skill_by_id = {
        int(
            skill[
                "skill_id"
            ]
        ):
            skill

        for skill
        in skills
    }


    mapped_courses = 0
    direct_count = 0
    gemini_count = 0


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


    for resource_id in sorted(
        course_by_id
    ):

        course = (
            course_by_id[
                resource_id
            ]
        )


        selected = mappings.get(
            resource_id,
            [],
        )


        print(
            (
                f"\n"
                f"[{course.get('course_code')}] "
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

                gemini_count += 1


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


            if item[
                "reason"
            ]:

                print(
                    (
                        "     reason: "
                        f"{item['reason']}"
                    )
                )


    total_mapping_count = (
        direct_count
        + gemini_count
    )


    print(
        "\n"
        + "-" * 80
    )


    print(
        (
            f"전체 COURSE: "
            f"{len(courses)}개"
        )
    )


    print(
        (
            f"Skill 연결 COURSE: "
            f"{mapped_courses}개"
        )
    )


    print(
        (
            f"매핑 없는 COURSE: "
            f"{len(courses) - mapped_courses}개"
        )
    )


    print(
        (
            f"직접 문자열 매핑: "
            f"{direct_count}개"
        )
    )


    print(
        (
            "Gemini 2차 검토 매핑: "
            f"{gemini_count}개"
        )
    )


    print(
        (
            "최종 RESOURCE_SKILL 후보: "
            f"{total_mapping_count}개"
        )
    )


# ============================================================
# DB 저장
# ============================================================

def save_mappings(
    courses: list[dict],

    mappings: dict[
        int,
        list[dict]
    ],
) -> int:

    source_document_id = (
        load_curriculum_source_document_id(
            courses
        )
    )


    delete_sql = text(
        """
        DELETE FROM resource_skill

        WHERE resource_id = :resource_id
          AND relation_type = 'TAUGHT'

          AND mapping_method IN (
              :direct_method,
              :gemini_method
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

        for course
        in courses
    }


    saved = 0


    with engine.begin() as conn:

        # ----------------------------------------------------
        # 이전에 이 mapper가 만든 결과만 삭제
        #
        # 수동 매핑이나 다른 mapper는 건드리지 않음.
        # ----------------------------------------------------

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

                    "gemini_method":
                        GEMINI_MAPPING_METHOD,
                },
            )


        # ----------------------------------------------------
        # 최종 결과 저장
        # ----------------------------------------------------

        for (
            resource_id,
            items,
        ) in mappings.items():

            course = (
                course_by_id[
                    resource_id
                ]
            )


            # 실제 근거는 생성 문장이 아니라
            # 공식 교육과정에 존재하는 교과목명으로 저장
            evidence_text = (
                "교과목명: "
                f"{clean_text(course['name'])}"
            )


            for item in items:

                conn.execute(
                    upsert_sql,

                    {
                        "resource_id":
                            resource_id,

                        "skill_id":
                            item[
                                "skill_id"
                            ],

                        "evidence_text":
                            evidence_text,

                        "mapping_method":
                            item[
                                "method"
                            ],

                        "confidence":
                            item[
                                "confidence"
                            ],

                        "source_document_id":
                            source_document_id,
                    },
                )


                saved += 1


    return saved


# ============================================================
# DB 저장 결과 출력
# ============================================================

def print_saved_result(
) -> None:

    sql = text(
        """
        SELECT
            cd.course_code,

            r.name AS course_name,

            s.name AS skill_name,

            rs.confidence,
            rs.mapping_method,
            rs.verified

        FROM resource_skill rs

        JOIN resource r
            ON r.resource_id = rs.resource_id

        JOIN course_detail cd
            ON cd.resource_id = r.resource_id

        JOIN department d
            ON d.department_id = cd.department_id

        JOIN skill s
            ON s.skill_id = rs.skill_id

        WHERE r.resource_type = 'COURSE'

          AND cd.curriculum_year =
              :curriculum_year

          AND d.name =
              :department_name

          AND rs.relation_type =
              'TAUGHT'

        ORDER BY
            cd.course_code,
            rs.confidence DESC NULLS LAST,
            s.name
        """
    )


    with engine.connect() as conn:

        rows = conn.execute(
            sql,

            {
                "curriculum_year":
                    TARGET_CURRICULUM_YEAR,

                "department_name":
                    TARGET_DEPARTMENT,
            },
        ).mappings().all()


    print(
        "\n"
        + "=" * 80
    )


    print(
        "DB RESOURCE_SKILL(TAUGHT)"
    )


    print(
        "=" * 80
    )


    current_code = None


    for row in rows:

        code = row[
            "course_code"
        ]


        if (
            code
            != current_code
        ):

            print(
                (
                    f"\n"
                    f"[{code}] "
                    f"{row['course_name']}"
                )
            )


            current_code = (
                code
            )


        confidence = (
            float(
                row[
                    "confidence"
                ]
            )

            if row[
                "confidence"
            ]
            is not None

            else 0.0
        )


        print(
            (
                f"  -> "
                f"{row['skill_name']} "
                f"| confidence="
                f"{confidence:.2f} "
                f"| method="
                f"{row['mapping_method']} "
                f"| verified="
                f"{row['verified']}"
            )
        )


    print(
        (
            f"\n총 TAUGHT 매핑: "
            f"{len(rows)}개"
        )
    )


# ============================================================
# 실행
# ============================================================

def run(
    save_db: bool = False,
) -> None:

    print(
        "=" * 80
    )


    print(
        (
            "CareerQuest - "
            "COURSE -> RESOURCE_SKILL "
            "보수적 매핑"
        )
    )


    print(
        "=" * 80
    )


    # --------------------------------------------------------
    # 1. COURSE
    # --------------------------------------------------------

    print(
        "\n[1/7] COURSE 로딩"
    )


    courses = (
        load_courses()
    )


    print(
        (
            f"  COURSE: "
            f"{len(courses)}개"
        )
    )


    # --------------------------------------------------------
    # 2. Skill
    # --------------------------------------------------------

    print(
        "\n[2/7] Skill DB 로딩"
    )


    skills = (
        load_skills()
    )


    print(
        (
            f"  Skill: "
            f"{len(skills)}개"
        )
    )


    # --------------------------------------------------------
    # 데이터 수 안전장치
    # --------------------------------------------------------

    if (
        len(courses)
        != EXPECTED_COURSE_COUNT
    ):

        raise RuntimeError(
            (
                "COURSE 수가 기대값과 "
                "다릅니다: "
                f"{len(courses)} "
                f"!= "
                f"{EXPECTED_COURSE_COUNT}"
            )
        )


    if (
        len(skills)
        != EXPECTED_SKILL_COUNT
    ):

        raise RuntimeError(
            (
                "Skill 수가 기대값과 "
                "다릅니다: "
                f"{len(skills)} "
                f"!= "
                f"{EXPECTED_SKILL_COUNT}"
            )
        )


    # --------------------------------------------------------
    # 3. 직접 후보
    # --------------------------------------------------------

    print(
        "\n[3/7] 직접 문자열 후보"
    )


    literal_candidates = (
        find_literal_candidates(
            courses,
            skills,
        )
    )


    literal_pair_count = sum(
        len(
            items
        )

        for items
        in (
            literal_candidates.values()
        )
    )


    print(
        (
            f"  직접 근거 후보: "
            f"{literal_pair_count}개"
        )
    )


    # --------------------------------------------------------
    # 4. Gemini 1차
    # --------------------------------------------------------

    print(
        (
            f"\n[4/7] "
            f"{GEMINI_MODEL} "
            "1차 후보 생성"
        )
    )


    proposal_prompt = (
        build_proposal_prompt(
            courses,
            skills,
            literal_candidates,
        )
    )


    proposal_result = (
        call_gemini_structured(
            proposal_prompt,
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
            f"  1차 Gemini 후보: "
            f"{proposal_count}개"
        )
    )


    # --------------------------------------------------------
    # 5. Gemini 2차
    # --------------------------------------------------------

    print(
        (
            f"\n[5/7] "
            f"{GEMINI_MODEL} "
            "2차 재검토"
        )
    )


    review_prompt = (
        build_review_prompt(
            courses,
            skills,
            proposals,
            literal_candidates,
        )
    )


    review_result = (
        call_gemini_structured(
            review_prompt,
            ReviewBatch,
        )
    )


    reviewed = (
        validate_review(
            review_result,
            proposals,
            skills,
        )
    )


    final_mappings = (
        build_final_mappings(
            courses,
            literal_candidates,
            proposals,
            reviewed,
        )
    )


    # --------------------------------------------------------
    # 6. 결과
    # --------------------------------------------------------

    print(
        "\n[6/7] 최종 미리보기"
    )


    print_preview(
        courses,
        skills,
        final_mappings,
    )


    # --------------------------------------------------------
    # 7. 저장
    # --------------------------------------------------------

    if save_db:

        print(
            "\n[7/7] RESOURCE_SKILL 저장"
        )


        saved = (
            save_mappings(
                courses,
                final_mappings,
            )
        )


        print(
            (
                f"  저장 완료: "
                f"{saved}개"
            )
        )


        print_saved_result()


    else:

        print(
            "\n[7/7] DB 저장 생략"
        )


        print(
            (
                "  현재는 검토 실행입니다. "
                "결과가 괜찮으면 "
                "--save를 붙여 저장하세요."
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


# ============================================================
# CLI
# ============================================================

def parse_args(
) -> argparse.Namespace:

    parser = (
        argparse.ArgumentParser()
    )


    parser.add_argument(
        "--save",

        action="store_true",

        help=(
            "검증된 매핑을 "
            "RESOURCE_SKILL에 저장"
        ),
    )


    return (
        parser.parse_args()
    )


# ============================================================
# main
# ============================================================

if __name__ == "__main__":

    args = (
        parse_args()
    )


    run(
        save_db=(
            args.save
        )
    )