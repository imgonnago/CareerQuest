from __future__ import annotations

import argparse
import re

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from src.db.database import engine


# ============================================================
# 기본 설정
# ============================================================

MAPPING_METHOD = (
    "manual_certificate_rules_v1"
)

RELATION_TYPE = (
    "VERIFIED"
)


# ============================================================
# 매핑 규칙
#
# 중요한 원칙:
# - 자격증 이름만 보고 Skill을 억지로 붙이지 않음
# - 공식 SourceDocument 안에 evidence keyword가 있어야 함
# - DB에 실제 존재하는 Skill만 연결
# - Skill ID 하드코딩 없음
# ============================================================

@dataclass(frozen=True)
class SkillRule:
    skill_candidates: tuple[str, ...]

    evidence_terms: tuple[str, ...]

    confidence: float

    reason: str


@dataclass(frozen=True)
class CertificateRule:
    qualification_code: str

    expected_name: str

    skills: tuple[SkillRule, ...]


CERTIFICATE_RULES = (
    # ========================================================
    # SQLD
    # ========================================================

    CertificateRule(
        qualification_code="DATAQ-SQLD",

        expected_name="SQL 개발자",

        skills=(
            SkillRule(
                skill_candidates=(
                    "SQL",
                ),

                evidence_terms=(
                    "SQL",
                    "SQL 기본",
                    "SQL 활용",
                ),

                confidence=0.99,

                reason=(
                    "SQLD 공식 평가영역에서 "
                    "SQL 기본 및 활용을 직접 평가"
                ),
            ),

            SkillRule(
                skill_candidates=(
                    "Database",
                    "데이터베이스",
                ),

                evidence_terms=(
                    "데이터 모델링",
                    "데이터모델링",
                    "데이터베이스",
                ),

                confidence=0.96,

                reason=(
                    "SQLD 공식 평가영역에 "
                    "데이터 모델링 영역 포함"
                ),
            ),
        ),
    ),


    # ========================================================
    # SQLP
    # ========================================================

    CertificateRule(
        qualification_code="DATAQ-SQLP",

        expected_name="SQL 전문가",

        skills=(
            SkillRule(
                skill_candidates=(
                    "SQL",
                ),

                evidence_terms=(
                    "SQL",
                    "SQL 고급",
                    "SQL 튜닝",
                ),

                confidence=0.99,

                reason=(
                    "SQLP는 SQL 활용 및 "
                    "고급 SQL 역량을 직접 평가"
                ),
            ),

            SkillRule(
                skill_candidates=(
                    "Database",
                    "데이터베이스",
                ),

                evidence_terms=(
                    "데이터 모델링",
                    "데이터모델링",
                    "데이터베이스",
                ),

                confidence=0.97,

                reason=(
                    "SQLP 평가영역에 "
                    "데이터 모델링 및 DB 관련 내용 포함"
                ),
            ),
        ),
    ),


    # ========================================================
    # ADsP
    # ========================================================

    CertificateRule(
        qualification_code="DATAQ-ADSP",

        expected_name="데이터분석 준전문가",

        skills=(
            SkillRule(
                skill_candidates=(
                    "Data Analysis",
                    "데이터 분석",
                    "데이터분석",
                ),

                evidence_terms=(
                    "데이터 분석",
                    "데이터분석",
                ),

                confidence=0.99,

                reason=(
                    "ADsP가 데이터 분석 역량을 "
                    "직접 평가하는 자격"
                ),
            ),

            SkillRule(
                skill_candidates=(
                    "Statistics",
                    "통계",
                    "통계학",
                ),

                evidence_terms=(
                    "통계분석",
                    "통계 분석",
                    "통계",
                ),

                confidence=0.94,

                reason=(
                    "ADsP 데이터 분석 영역에서 "
                    "통계 분석을 평가"
                ),
            ),
        ),
    ),


    # ========================================================
    # ADP
    # ========================================================

    CertificateRule(
        qualification_code="DATAQ-ADP",

        expected_name="데이터분석 전문가",

        skills=(
            SkillRule(
                skill_candidates=(
                    "Data Analysis",
                    "데이터 분석",
                    "데이터분석",
                ),

                evidence_terms=(
                    "데이터 분석",
                    "데이터분석",
                ),

                confidence=0.99,

                reason=(
                    "ADP는 전문 수준의 "
                    "데이터 분석 역량을 평가"
                ),
            ),

            SkillRule(
                skill_candidates=(
                    "Statistics",
                    "통계",
                    "통계학",
                ),

                evidence_terms=(
                    "통계분석",
                    "통계 분석",
                    "통계",
                ),

                confidence=0.97,

                reason=(
                    "ADP 평가 범위에 "
                    "통계 분석 영역 포함"
                ),
            ),
        ),
    ),


    # ========================================================
    # 빅데이터분석기사
    # ========================================================

    CertificateRule(
        qualification_code="DATAQ-BAE",

        expected_name="빅데이터분석기사",

        skills=(
            SkillRule(
                skill_candidates=(
                    "Data Analysis",
                    "데이터 분석",
                    "데이터분석",
                ),

                evidence_terms=(
                    "빅데이터 분석",
                    "데이터 분석",
                    "데이터분석",
                ),

                confidence=0.99,

                reason=(
                    "빅데이터분석기사가 "
                    "데이터 분석 능력을 직접 평가"
                ),
            ),

            SkillRule(
                skill_candidates=(
                    "Statistics",
                    "통계",
                    "통계학",
                ),

                evidence_terms=(
                    "통계",
                    "통계 분석",
                    "통계분석",
                ),

                confidence=0.94,

                reason=(
                    "빅데이터 분석 과정의 "
                    "통계적 분석 역량 평가"
                ),
            ),

            SkillRule(
                skill_candidates=(
                    "Machine Learning",
                    "머신러닝",
                    "기계학습",
                ),

                evidence_terms=(
                    "머신러닝",
                    "기계학습",
                    "분석모형",
                    "분석 모형",
                ),

                confidence=0.90,

                reason=(
                    "분석모형 관련 공식 평가 내용이 "
                    "확인되는 경우에만 연결"
                ),
            ),
        ),
    ),


    # ========================================================
    # DAP
    # ========================================================

    CertificateRule(
        qualification_code="DATAQ-DAP",

        expected_name="데이터아키텍처 전문가",

        skills=(
            SkillRule(
                skill_candidates=(
                    "Database",
                    "데이터베이스",
                ),

                evidence_terms=(
                    "데이터베이스",
                    "데이터 아키텍처",
                    "데이터아키텍처",
                ),

                confidence=0.96,

                reason=(
                    "데이터 아키텍처 자격의 "
                    "DB 구조 관련 역량"
                ),
            ),

            SkillRule(
                skill_candidates=(
                    "Data Modeling",
                    "데이터 모델링",
                    "데이터모델링",
                ),

                evidence_terms=(
                    "데이터 모델링",
                    "데이터모델링",
                    "데이터 모델",
                ),

                confidence=0.99,

                reason=(
                    "DAP가 데이터 모델링을 "
                    "직접 다루는 자격"
                ),
            ),
        ),
    ),


    # ========================================================
    # DAsP
    # ========================================================

    CertificateRule(
        qualification_code="DATAQ-DASP",

        expected_name="데이터아키텍처 준전문가",

        skills=(
            SkillRule(
                skill_candidates=(
                    "Database",
                    "데이터베이스",
                ),

                evidence_terms=(
                    "데이터베이스",
                    "데이터 아키텍처",
                    "데이터아키텍처",
                ),

                confidence=0.93,

                reason=(
                    "데이터 아키텍처 관련 "
                    "DB 구조 역량"
                ),
            ),

            SkillRule(
                skill_candidates=(
                    "Data Modeling",
                    "데이터 모델링",
                    "데이터모델링",
                ),

                evidence_terms=(
                    "데이터 모델링",
                    "데이터모델링",
                    "데이터 모델",
                ),

                confidence=0.98,

                reason=(
                    "DAsP 공식 평가영역에서 "
                    "데이터 모델링을 다룸"
                ),
            ),
        ),
    ),


    # ========================================================
    # 정보처리기사
    # ========================================================

    CertificateRule(
        qualification_code="QNET-1320",

        expected_name="정보처리기사",

        skills=(
            SkillRule(
                skill_candidates=(
                    "Database",
                    "데이터베이스",
                ),

                evidence_terms=(
                    "데이터베이스",
                    "SQL",
                ),

                confidence=0.96,

                reason=(
                    "정보처리기사 평가영역의 "
                    "데이터베이스 및 SQL 관련 내용"
                ),
            ),

            SkillRule(
                skill_candidates=(
                    "Programming",
                    "프로그래밍",
                ),

                evidence_terms=(
                    "프로그래밍 언어",
                    "프로그래밍언어",
                    "프로그래밍",
                ),

                confidence=0.95,

                reason=(
                    "정보처리기사에서 "
                    "프로그래밍 언어 활용 능력을 평가"
                ),
            ),

            SkillRule(
                skill_candidates=(
                    "Algorithm",
                    "알고리즘",
                ),

                evidence_terms=(
                    "알고리즘",
                ),

                confidence=0.93,

                reason=(
                    "공식 시험 내용에서 "
                    "알고리즘이 확인되는 경우 연결"
                ),
            ),

            SkillRule(
                skill_candidates=(
                    "Network",
                    "Networking",
                    "네트워크",
                ),

                evidence_terms=(
                    "네트워크",
                ),

                confidence=0.93,

                reason=(
                    "정보처리기사 평가 내용에서 "
                    "네트워크 관련 영역 확인"
                ),
            ),
        ),
    ),
)


# ============================================================
# 문자열 정규화
# ============================================================

def normalize(
    value: Any,
) -> str:

    if value is None:

        return ""

    value = str(
        value
    ).strip().lower()

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value


# ============================================================
# DB Skill 로딩
# ============================================================

def load_skills(
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

        WHERE s.is_active = TRUE

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


        if (
            skill_id
            not in skills
        ):

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


        if alias:

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
# Skill 이름 / alias → 실제 DB Skill
# ============================================================

def resolve_skill(
    candidates: tuple[
        str,
        ...
    ],

    skills: list[
        dict
    ],
) -> dict | None:

    candidate_values = {
        normalize(
            candidate
        )

        for candidate
        in candidates
    }


    # --------------------------------------------------------
    # canonical name 우선
    # --------------------------------------------------------

    for skill in skills:

        names = {
            normalize(
                skill[
                    "name"
                ]
            ),

            normalize(
                skill[
                    "normalized_name"
                ]
            ),
        }


        if (
            names
            & candidate_values
        ):

            return skill


    # --------------------------------------------------------
    # alias
    # --------------------------------------------------------

    for skill in skills:

        aliases = {
            normalize(
                alias
            )

            for alias
            in skill[
                "aliases"
            ]
        }


        if (
            aliases
            & candidate_values
        ):

            return skill


    return None


# ============================================================
# 자격증 Resource 조회
# ============================================================

def load_certificate(
    qualification_code: str,
) -> dict | None:

    sql = text(
        """
        SELECT
            r.resource_id,
            r.name,
            r.organization,

            cd.qualification_code,
            cd.qualification_type,
            cd.administering_org

        FROM certificate_detail cd

        JOIN resource r
            ON r.resource_id =
               cd.resource_id

        WHERE
            cd.qualification_code =
                :qualification_code

          AND r.resource_type =
                'CERTIFICATE'
        """
    )


    with engine.connect() as conn:

        row = (
            conn.execute(
                sql,

                {
                    "qualification_code":
                        qualification_code,
                },
            ).mappings().first()
        )


    if row is None:

        return None


    return dict(
        row
    )


# ============================================================
# 자격증 공식 SourceDocument 조회
# ============================================================

def load_certificate_source(
    resource_id: int,
) -> dict | None:

    sql = text(
        """
        SELECT
            source_document_id,
            source_name,
            title,
            content,
            validation_status,
            trust_level,
            evidence_eligible

        FROM source_document

        WHERE
            resource_id =
                :resource_id

          AND fetch_status =
                'SUCCESS'

        ORDER BY

            CASE
                WHEN evidence_eligible = TRUE
                THEN 0
                ELSE 1
            END,

            CASE
                WHEN validation_status = 'ACCEPTED'
                THEN 0
                ELSE 1
            END,

            CASE trust_level
                WHEN 'A' THEN 0
                WHEN 'B' THEN 1
                ELSE 2
            END,

            source_document_id DESC

        LIMIT 1
        """
    )


    with engine.connect() as conn:

        row = (
            conn.execute(
                sql,

                {
                    "resource_id":
                        resource_id,
                },
            ).mappings().first()
        )


    if row is None:

        return None


    return dict(
        row
    )


# ============================================================
# 원문에서 실제 Evidence 추출
#
# 반환 문자열은 SourceDocument.content 안에
# 실제로 존재하는 substring이어야 한다.
# ============================================================

def extract_evidence(
    content: str,
    evidence_terms: tuple[
        str,
        ...
    ],
) -> str | None:

    if not content:

        return None


    # --------------------------------------------------------
    # 1. 줄 단위
    # --------------------------------------------------------

    lines = (
        content.splitlines()
    )


    for term in evidence_terms:

        normalized_term = (
            normalize(
                term
            )
        )


        for line in lines:

            if (
                normalized_term
                in normalize(
                    line
                )
            ):

                evidence = (
                    line.strip()
                )


                if evidence:

                    return evidence[
                        :1000
                    ]


    # --------------------------------------------------------
    # 2. PDF 추출 결과 등이 한 줄로 뭉친 경우
    #
    # 실제 원문 substring을 잘라 반환
    # --------------------------------------------------------

    content_lower = (
        content.lower()
    )


    for term in evidence_terms:

        pos = (
            content_lower.find(
                term.lower()
            )
        )


        if pos < 0:

            continue


        start = max(
            0,
            pos - 120,
        )


        end = min(
            len(
                content
            ),
            pos + len(
                term
            ) + 180,
        )


        evidence = (
            content[
                start:end
            ].strip()
        )


        if evidence:

            return evidence


    return None


# ============================================================
# 기존 매핑 확인
# ============================================================

def load_existing_mapping(
    resource_id: int,
    skill_id: int,
) -> dict | None:

    sql = text(
        """
        SELECT
            resource_skill_id,
            relation_type,
            mapping_method,
            confidence,
            verified,
            evidence_text

        FROM resource_skill

        WHERE
            resource_id =
                :resource_id

          AND skill_id =
                :skill_id

          AND relation_type =
                'VERIFIED'
        """
    )


    with engine.connect() as conn:

        row = (
            conn.execute(
                sql,

                {
                    "resource_id":
                        resource_id,

                    "skill_id":
                        skill_id,
                },
            ).mappings().first()
        )


    if row is None:

        return None


    return dict(
        row
    )


# ============================================================
# 전체 매핑 계획 생성
# ============================================================

def build_plan(
) -> tuple[
    list[dict],
    list[dict],
]:

    skills = (
        load_skills()
    )


    plan: list[
        dict
    ] = []


    skipped: list[
        dict
    ] = []


    for certificate_rule in (
        CERTIFICATE_RULES
    ):

        certificate = (
            load_certificate(
                certificate_rule
                .qualification_code
            )
        )


        # ----------------------------------------------------
        # 자격증 자체가 DB에 없음
        # ----------------------------------------------------

        if certificate is None:

            skipped.append(
                {
                    "certificate":
                        certificate_rule
                        .expected_name,

                    "skill":
                        "-",

                    "reason":
                        "CERTIFICATE_NOT_FOUND",
                }
            )

            continue


        # ----------------------------------------------------
        # SourceDocument
        # ----------------------------------------------------

        source = (
            load_certificate_source(
                certificate[
                    "resource_id"
                ]
            )
        )


        if source is None:

            skipped.append(
                {
                    "certificate":
                        certificate[
                            "name"
                        ],

                    "skill":
                        "-",

                    "reason":
                        "SOURCE_DOCUMENT_NOT_FOUND",
                }
            )

            continue


        content = (
            source[
                "content"
            ]
            or ""
        )


        # ----------------------------------------------------
        # Skill 규칙
        # ----------------------------------------------------

        for skill_rule in (
            certificate_rule
            .skills
        ):

            skill = (
                resolve_skill(
                    skill_rule
                    .skill_candidates,
                    skills,
                )
            )


            if skill is None:

                skipped.append(
                    {
                        "certificate":
                            certificate[
                                "name"
                            ],

                        "skill":
                            "/".join(
                                skill_rule
                                .skill_candidates
                            ),

                        "reason":
                            "SKILL_NOT_FOUND",
                    }
                )

                continue


            evidence = (
                extract_evidence(
                    content,
                    skill_rule
                    .evidence_terms,
                )
            )


            if evidence is None:

                skipped.append(
                    {
                        "certificate":
                            certificate[
                                "name"
                            ],

                        "skill":
                            skill[
                                "name"
                            ],

                        "reason":
                            "EVIDENCE_NOT_FOUND",
                    }
                )

                continue


            existing = (
                load_existing_mapping(
                    resource_id=(
                        certificate[
                            "resource_id"
                        ]
                    ),

                    skill_id=(
                        skill[
                            "skill_id"
                        ]
                    ),
                )
            )


            # ------------------------------------------------
            # 기존 다른 VERIFIED 매핑은 보호
            # ------------------------------------------------

            if (
                existing is not None

                and existing[
                    "mapping_method"
                ]
                != MAPPING_METHOD
            ):

                action = (
                    "SKIP_EXISTING"
                )


            elif (
                existing is not None
            ):

                action = (
                    "UPDATE"
                )


            else:

                action = (
                    "INSERT"
                )


            plan.append(
                {
                    "action":
                        action,

                    "resource_id":
                        certificate[
                            "resource_id"
                        ],

                    "certificate":
                        certificate[
                            "name"
                        ],

                    "qualification_code":
                        certificate[
                            "qualification_code"
                        ],

                    "skill_id":
                        skill[
                            "skill_id"
                        ],

                    "skill":
                        skill[
                            "name"
                        ],

                    "confidence":
                        skill_rule
                        .confidence,

                    "reason":
                        skill_rule
                        .reason,

                    "evidence_text":
                        evidence,

                    "source_document_id":
                        source[
                            "source_document_id"
                        ],

                    "source_name":
                        source[
                            "source_name"
                        ],
                }
            )


    return (
        plan,
        skipped,
    )


# ============================================================
# 출력
# ============================================================

def print_plan(
    plan: list[
        dict
    ],

    skipped: list[
        dict
    ],
) -> None:

    print(
        "\n"
        + "=" * 80
    )


    print(
        "CERTIFICATE -> SKILL 검증 결과"
    )


    print(
        "=" * 80
    )


    if not plan:

        print(
            "\n저장 가능한 매핑 없음"
        )


    for item in plan:

        print(
            (
                f"\n[{item['action']}] "
                f"{item['certificate']}"
            )
        )


        print(
            (
                f"  -> "
                f"{item['skill']} "
                f"(skill_id="
                f"{item['skill_id']})"
            )
        )


        print(
            (
                f"     confidence : "
                f"{item['confidence']:.2f}"
            )
        )


        print(
            (
                f"     source     : "
                f"{item['source_name']} "
                f"(document="
                f"{item['source_document_id']})"
            )
        )


        print(
            (
                f"     evidence   : "
                f"{item['evidence_text']}"
            )
        )


        print(
            (
                f"     reason     : "
                f"{item['reason']}"
            )
        )


    print(
        "\n"
        + "-" * 80
    )


    print(
        "SKIPPED"
    )


    print(
        "-" * 80
    )


    if not skipped:

        print(
            "없음"
        )


    else:

        for item in skipped:

            print(
                (
                    f"{item['certificate']} "
                    f"| {item['skill']} "
                    f"| {item['reason']}"
                )
            )


    print(
        "\n"
        + "-" * 80
    )


    print(
        (
            "INSERT 후보        : "
            f"{sum(1 for x in plan if x['action'] == 'INSERT')}"
        )
    )


    print(
        (
            "UPDATE 후보        : "
            f"{sum(1 for x in plan if x['action'] == 'UPDATE')}"
        )
    )


    print(
        (
            "기존 매핑 보호     : "
            f"{sum(1 for x in plan if x['action'] == 'SKIP_EXISTING')}"
        )
    )


    print(
        (
            "근거/Skill 부족     : "
            f"{len(skipped)}"
        )
    )


# ============================================================
# DB 저장
# ============================================================

INSERT_SQL = text(
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

        'VERIFIED',

        :evidence_text,

        :mapping_method,

        :confidence,

        TRUE,

        :source_document_id
    )

    ON CONFLICT (
        resource_id,
        skill_id,
        relation_type
    )

    DO NOTHING
    """
)


UPDATE_SQL = text(
    """
    UPDATE resource_skill

    SET
        evidence_text =
            :evidence_text,

        confidence =
            :confidence,

        verified =
            TRUE,

        source_document_id =
            :source_document_id,

        updated_at =
            NOW()

    WHERE
        resource_id =
            :resource_id

      AND skill_id =
            :skill_id

      AND relation_type =
            'VERIFIED'

      AND mapping_method =
            :mapping_method
    """
)


def save_plan(
    plan: list[
        dict
    ],
) -> dict:

    counts = {
        "inserted":
            0,

        "updated":
            0,

        "protected":
            0,
    }


    with engine.begin() as conn:

        for item in plan:

            action = (
                item[
                    "action"
                ]
            )


            params = {
                "resource_id":
                    item[
                        "resource_id"
                    ],

                "skill_id":
                    item[
                        "skill_id"
                    ],

                "evidence_text":
                    item[
                        "evidence_text"
                    ],

                "mapping_method":
                    MAPPING_METHOD,

                "confidence":
                    item[
                        "confidence"
                    ],

                "source_document_id":
                    item[
                        "source_document_id"
                    ],
            }


            if action == "INSERT":

                result = (
                    conn.execute(
                        INSERT_SQL,
                        params,
                    )
                )


                if (
                    result.rowcount
                    > 0
                ):

                    counts[
                        "inserted"
                    ] += 1


            elif action == "UPDATE":

                result = (
                    conn.execute(
                        UPDATE_SQL,
                        params,
                    )
                )


                if (
                    result.rowcount
                    > 0
                ):

                    counts[
                        "updated"
                    ] += 1


            else:

                counts[
                    "protected"
                ] += 1


    return counts


# ============================================================
# 저장 후 검증
# ============================================================

def print_saved_mappings(
) -> None:

    sql = text(
        """
        SELECT
            r.name AS certificate,

            cd.qualification_code,

            s.name AS skill,

            rs.relation_type,

            rs.confidence,

            rs.verified,

            rs.mapping_method

        FROM resource_skill rs

        JOIN resource r
            ON r.resource_id =
               rs.resource_id

        JOIN certificate_detail cd
            ON cd.resource_id =
               r.resource_id

        JOIN skill s
            ON s.skill_id =
               rs.skill_id

        WHERE
            r.resource_type =
                'CERTIFICATE'

          AND rs.relation_type =
                'VERIFIED'

        ORDER BY
            r.name,
            s.name
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql
            ).mappings().all()
        )


    print(
        "\n"
        + "=" * 80
    )


    print(
        "현재 CERTIFICATE -> SKILL"
    )


    print(
        "=" * 80
    )


    for row in rows:

        print(
            (
                f"{row['certificate']:<20} "
                f"-> "
                f"{row['skill']:<25} "
                f"| {row['relation_type']} "
                f"| {float(row['confidence']):.2f} "
                f"| verified="
                f"{row['verified']}"
            )
        )


    print(
        "\n총 매핑:",
        len(
            rows
        ),
        "개",
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
            "검증된 매핑을 실제 "
            "RESOURCE_SKILL에 저장"
        ),
    )


    parser.add_argument(
        "--show",

        action="store_true",

        help=(
            "현재 저장된 자격증 "
            "VERIFIED 매핑만 조회"
        ),
    )


    return (
        parser.parse_args()
    )


# ============================================================
# MAIN
# ============================================================

def main(
) -> None:

    args = (
        parse_args()
    )


    print(
        "=" * 80
    )


    print(
        (
            "CareerQuest - "
            "CERTIFICATE -> SKILL "
            "보수적 매핑"
        )
    )


    print(
        "=" * 80
    )


    if args.show:

        print_saved_mappings()

        return


    plan, skipped = (
        build_plan()
    )


    print_plan(
        plan,
        skipped,
    )


    if not args.save:

        print(
            "\n[DRY RUN]"
        )


        print(
            (
                "DB를 수정하지 않았습니다."
            )
        )


        print(
            (
                "매핑과 evidence를 확인한 후 "
                "--save를 사용하세요."
            )
        )


        return


    result = (
        save_plan(
            plan
        )
    )


    print(
        "\n"
        + "=" * 80
    )


    print(
        "DB 저장 결과"
    )


    print(
        "=" * 80
    )


    print(
        (
            f"INSERTED : "
            f"{result['inserted']}"
        )
    )


    print(
        (
            f"UPDATED  : "
            f"{result['updated']}"
        )
    )


    print(
        (
            f"PROTECTED: "
            f"{result['protected']}"
        )
    )


    print_saved_mappings()


if __name__ == "__main__":
    main()