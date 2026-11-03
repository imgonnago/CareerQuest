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

MAPPING_METHOD = "literal_job_section_v1"

REQUIRED_CONFIDENCE = 0.98
PREFERRED_CONFIDENCE = 0.97

MAX_SECTION_DISTANCE = 1500


# ============================================================
# 섹션 키워드
# ============================================================

REQUIRED_HEADINGS = (
    "자격요건",
    "자격 요건",
    "지원자격",
    "지원 자격",
    "필수요건",
    "필수 요건",
    "필수사항",
    "필수 사항",
    "응시자격",
    "응시 자격",
    "자격조건",
    "자격 조건",
    "required qualifications",
    "requirements",
    "required skills",
)


PREFERRED_HEADINGS = (
    "우대사항",
    "우대 사항",
    "우대요건",
    "우대 요건",
    "우대조건",
    "우대 조건",
    "우대자격",
    "우대 자격",
    "preferred qualifications",
    "preferred skills",
    "preferred",
)


LOCAL_REQUIRED_TERMS = (
    "필수",
    "필수사항",
    "필수 사항",
    "필수요건",
    "필수 요건",
    "must",
    "required",
)


LOCAL_PREFERRED_TERMS = (
    "우대",
    "우대사항",
    "우대 사항",
    "우대조건",
    "우대 조건",
    "preferred",
    "plus",
)


# ============================================================
# 데이터 구조
# ============================================================

@dataclass
class SkillEntry:
    skill_id: int
    name: str
    normalized_name: str | None
    aliases: list[str]


@dataclass
class SkillMatch:
    resource_id: int
    resource_name: str
    source_document_id: int

    skill_id: int
    skill_name: str

    relation_type: str

    evidence_text: str

    matched_alias: str

    confidence: float

    position: int


# ============================================================
# 문자열 정규화
# ============================================================

def normalize_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value).strip().lower(),
    )


# ============================================================
# Skill DB 로딩
# ============================================================

def load_skills(
) -> list[SkillEntry]:

    sql = text(
        """
        SELECT
            s.skill_id,
            s.name,
            s.normalized_name,
            sa.alias

        FROM skill s

        LEFT JOIN skill_alias sa
            ON sa.skill_id =
               s.skill_id

        WHERE
            s.is_active = TRUE

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


    grouped: dict[
        int,
        SkillEntry,
    ] = {}


    for row in rows:

        skill_id = int(
            row["skill_id"]
        )


        if skill_id not in grouped:

            grouped[skill_id] = (
                SkillEntry(
                    skill_id=skill_id,

                    name=row["name"],

                    normalized_name=(
                        row[
                            "normalized_name"
                        ]
                    ),

                    aliases=[],
                )
            )


        alias = (
            row["alias"]
        )


        if (
            alias
            and alias not in
            grouped[
                skill_id
            ].aliases
        ):

            grouped[
                skill_id
            ].aliases.append(
                alias
            )


    return list(
        grouped.values()
    )


# ============================================================
# JOB + 공식 SourceDocument 로딩
# ============================================================

def load_jobs(
) -> list[dict]:

    sql = text(
        """
        SELECT
            r.resource_id,
            r.name AS resource_name,

            sd.source_document_id,
            sd.title AS source_title,
            sd.content

        FROM resource r

        JOIN LATERAL (

            SELECT
                sd2.source_document_id,
                sd2.title,
                sd2.content

            FROM source_document sd2

            WHERE
                sd2.resource_id =
                    r.resource_id

              AND sd2.fetch_status =
                    'SUCCESS'

              AND sd2.content
                    IS NOT NULL

            ORDER BY

                CASE
                    WHEN sd2.evidence_eligible = TRUE
                    THEN 0
                    ELSE 1
                END,

                CASE
                    WHEN sd2.validation_status = 'ACCEPTED'
                    THEN 0
                    ELSE 1
                END,

                CASE sd2.trust_level
                    WHEN 'A' THEN 0
                    WHEN 'B' THEN 1
                    ELSE 2
                END,

                sd2.source_document_id DESC

            LIMIT 1

        ) sd
            ON TRUE

        WHERE
            r.resource_type =
                'JOB'

          AND r.is_active =
                TRUE

        ORDER BY
            r.resource_id
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql
            ).mappings().all()
        )


    return [
        dict(
            row
        )

        for row
        in rows
    ]


# ============================================================
# 기존 JOB Skill 매핑
#
# 기존 모델/수동 매핑은 전부 보호.
# relation_type이 달라도 같은 JOB-Skill이면 새로 만들지 않음.
# ============================================================

def load_existing_job_skills(
) -> set[
    tuple[
        int,
        int,
    ]
]:

    sql = text(
        """
        SELECT
            rs.resource_id,
            rs.skill_id

        FROM resource_skill rs

        JOIN resource r
            ON r.resource_id =
               rs.resource_id

        WHERE
            r.resource_type =
                'JOB'
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql
            ).mappings().all()
        )


    return {
        (
            int(
                row[
                    "resource_id"
                ]
            ),

            int(
                row[
                    "skill_id"
                ]
            ),
        )

        for row
        in rows
    }


# ============================================================
# Alias 검색 Regex
# ============================================================

def build_alias_pattern(
    alias: str,
) -> re.Pattern | None:

    alias = (
        alias.strip()
    )


    if not alias:
        return None


    # --------------------------------------------------------
    # 영문 / 숫자 Skill
    #
    # C, R, AI, SQL 등이 다른 단어 내부에서
    # 잘못 잡히지 않도록 boundary 적용
    # --------------------------------------------------------

    if re.fullmatch(
        r"[A-Za-z0-9+#.\- ]+",
        alias,
    ):

        escaped = (
            re.escape(
                alias
            )
        )


        return re.compile(
            (
                r"(?<![A-Za-z0-9])"
                + escaped
                + r"(?![A-Za-z0-9])"
            ),
            flags=re.IGNORECASE,
        )


    # --------------------------------------------------------
    # 한글 Alias
    # --------------------------------------------------------

    return re.compile(
        re.escape(
            alias
        ),
        flags=re.IGNORECASE,
    )


# ============================================================
# Skill이 검색할 모든 이름
# ============================================================

def skill_search_terms(
    skill: SkillEntry,
) -> list[str]:

    result: list[str] = []


    for value in (
        [
            skill.name,
            skill.normalized_name,
        ]
        + skill.aliases
    ):

        if not value:
            continue


        value = (
            value.strip()
        )


        if not value:
            continue


        if value not in result:

            result.append(
                value
            )


    # 긴 alias 우선
    result.sort(
        key=len,
        reverse=True,
    )


    return result


# ============================================================
# Heading 위치 탐색
# ============================================================

def find_section_markers(
    content: str,
) -> list[
    tuple[
        int,
        str,
        str,
    ]
]:

    markers: list[
        tuple[
            int,
            str,
            str,
        ]
    ] = []


    for heading in (
        REQUIRED_HEADINGS
    ):

        pattern = re.compile(
            re.escape(
                heading
            ),
            flags=re.IGNORECASE,
        )


        for match in pattern.finditer(
            content
        ):

            markers.append(
                (
                    match.start(),
                    "REQUIRED",
                    heading,
                )
            )


    for heading in (
        PREFERRED_HEADINGS
    ):

        pattern = re.compile(
            re.escape(
                heading
            ),
            flags=re.IGNORECASE,
        )


        for match in pattern.finditer(
            content
        ):

            markers.append(
                (
                    match.start(),
                    "PREFERRED",
                    heading,
                )
            )


    markers.sort(
        key=lambda x:
            x[0]
    )


    return markers


# ============================================================
# 특정 Skill 위치가 REQUIRED / PREFERRED인지 판정
# ============================================================

def detect_relation(
    content: str,
    skill_position: int,
    markers: list[
        tuple[
            int,
            str,
            str,
        ]
    ],
) -> str | None:

    # --------------------------------------------------------
    # 1. Skill 바로 주변 문구 우선
    # --------------------------------------------------------

    local_start = max(
        0,
        skill_position - 180,
    )

    local_end = min(
        len(content),
        skill_position + 180,
    )

    local = normalize_text(
        content[
            local_start:
            local_end
        ]
    )


    required_local = [
        term

        for term
        in LOCAL_REQUIRED_TERMS

        if term.lower()
        in local
    ]


    preferred_local = [
        term

        for term
        in LOCAL_PREFERRED_TERMS

        if term.lower()
        in local
    ]


    # 한쪽만 명확히 있는 경우
    if (
        required_local
        and not preferred_local
    ):

        return (
            "REQUIRED"
        )


    if (
        preferred_local
        and not required_local
    ):

        return (
            "PREFERRED"
        )


    # --------------------------------------------------------
    # 2. 가장 가까운 이전 Section Heading
    # --------------------------------------------------------

    previous = [
        marker

        for marker
        in markers

        if marker[0]
        <= skill_position
    ]


    if not previous:

        return None


    nearest = (
        previous[-1]
    )


    distance = (
        skill_position
        - nearest[0]
    )


    if (
        distance
        > MAX_SECTION_DISTANCE
    ):

        return None


    # --------------------------------------------------------
    # Skill 앞에 다음 섹션 제목이 이미 나왔다면
    # nearest가 그것이므로 자동으로 올바른 relation.
    # --------------------------------------------------------

    return nearest[1]


# ============================================================
# 실제 evidence 추출
#
# 반드시 원문 substring을 그대로 반환.
# ============================================================

def extract_evidence(
    content: str,
    match_start: int,
    match_end: int,
) -> str:

    # --------------------------------------------------------
    # 우선 같은 줄 전체
    # --------------------------------------------------------

    line_start = (
        content.rfind(
            "\n",
            0,
            match_start,
        )
        + 1
    )


    line_end = (
        content.find(
            "\n",
            match_end,
        )
    )


    if line_end < 0:

        line_end = (
            len(
                content
            )
        )


    line = (
        content[
            line_start:
            line_end
        ].strip()
    )


    if (
        line
        and len(
            line
        )
        <= 1000
    ):

        return line


    # --------------------------------------------------------
    # 한 줄이 지나치게 긴 HTML 파싱 결과
    # --------------------------------------------------------

    start = max(
        0,
        match_start - 250,
    )

    end = min(
        len(content),
        match_end + 350,
    )


    return (
        content[
            start:end
        ].strip()
    )[
        :1000
    ]


# ============================================================
# 하나의 JOB에서 Skill 탐색
# ============================================================

def find_job_skill_matches(
    job: dict,
    skills: list[
        SkillEntry
    ],
) -> list[
    SkillMatch
]:

    content = (
        job[
            "content"
        ]
        or ""
    )


    if not content:

        return []


    markers = (
        find_section_markers(
            content
        )
    )


    matches: list[
        SkillMatch
    ] = []


    for skill in skills:

        best_match: (
            SkillMatch
            | None
        ) = None


        for alias in (
            skill_search_terms(
                skill
            )
        ):

            pattern = (
                build_alias_pattern(
                    alias
                )
            )


            if pattern is None:
                continue


            for found in pattern.finditer(
                content
            ):

                relation = (
                    detect_relation(
                        content,
                        found.start(),
                        markers,
                    )
                )


                # --------------------------------------------
                # 애매한 일반 본문 언급은 저장하지 않음
                # --------------------------------------------

                if relation is None:

                    continue


                confidence = (
                    REQUIRED_CONFIDENCE

                    if relation
                    == "REQUIRED"

                    else PREFERRED_CONFIDENCE
                )


                evidence = (
                    extract_evidence(
                        content,
                        found.start(),
                        found.end(),
                    )
                )


                candidate = (
                    SkillMatch(
                        resource_id=(
                            int(
                                job[
                                    "resource_id"
                                ]
                            )
                        ),

                        resource_name=(
                            job[
                                "resource_name"
                            ]
                        ),

                        source_document_id=(
                            int(
                                job[
                                    "source_document_id"
                                ]
                            )
                        ),

                        skill_id=(
                            skill.skill_id
                        ),

                        skill_name=(
                            skill.name
                        ),

                        relation_type=(
                            relation
                        ),

                        evidence_text=(
                            evidence
                        ),

                        matched_alias=(
                            alias
                        ),

                        confidence=(
                            confidence
                        ),

                        position=(
                            found.start()
                        ),
                    )
                )


                # --------------------------------------------
                # 동일 Skill이 REQUIRED와 PREFERRED 둘 다
                # 등장하면 REQUIRED를 우선
                # --------------------------------------------

                if best_match is None:

                    best_match = (
                        candidate
                    )


                elif (
                    best_match
                    .relation_type
                    == "PREFERRED"

                    and relation
                    == "REQUIRED"
                ):

                    best_match = (
                        candidate
                    )


                # REQUIRED를 하나 찾았다면
                # 굳이 뒤의 우대사항까지 찾을 필요 없음
                if (
                    best_match
                    .relation_type
                    == "REQUIRED"
                ):

                    break


            if (
                best_match is not None

                and best_match
                .relation_type
                == "REQUIRED"
            ):

                break


        if best_match is not None:

            matches.append(
                best_match
            )


    return matches


# ============================================================
# 전체 계획 생성
# ============================================================

def build_plan(
) -> tuple[
    list[
        SkillMatch
    ],
    dict,
]:

    jobs = (
        load_jobs()
    )

    skills = (
        load_skills()
    )

    existing = (
        load_existing_job_skills()
    )


    plan: list[
        SkillMatch
    ] = []


    stats = {
        "jobs":
            len(
                jobs
            ),

        "skills":
            len(
                skills
            ),

        "raw_matches":
            0,

        "existing_protected":
            0,

        "new_mappings":
            0,

        "jobs_with_new_mapping":
            set(),
    }


    for job in jobs:

        matches = (
            find_job_skill_matches(
                job,
                skills,
            )
        )


        stats[
            "raw_matches"
        ] += len(
            matches
        )


        for match in matches:

            key = (
                match.resource_id,
                match.skill_id,
            )


            if key in existing:

                stats[
                    "existing_protected"
                ] += 1

                continue


            plan.append(
                match
            )


            stats[
                "jobs_with_new_mapping"
            ].add(
                match.resource_id
            )


    stats[
        "new_mappings"
    ] = len(
        plan
    )


    return (
        plan,
        stats,
    )


# ============================================================
# DRY RUN 출력
# ============================================================

def print_plan(
    plan: list[
        SkillMatch
    ],
    stats: dict,
) -> None:

    print(
        "=" * 80
    )

    print(
        (
            "CareerQuest - "
            "JOB -> SKILL 보수적 문자열 매핑"
        )
    )

    print(
        "=" * 80
    )


    current_job = None


    for item in plan:

        if (
            item.resource_id
            != current_job
        ):

            current_job = (
                item.resource_id
            )


            print(
                "\n"
                + "-" * 80
            )


            print(
                (
                    f"[JOB {item.resource_id}] "
                    f"{item.resource_name}"
                )
            )


        print(
            (
                f"  -> "
                f"{item.skill_name} "
                f"| {item.relation_type} "
                f"| confidence="
                f"{item.confidence:.2f}"
            )
        )


        print(
            (
                f"     alias    : "
                f"{item.matched_alias}"
            )
        )


        print(
            (
                f"     evidence : "
                f"{item.evidence_text}"
            )
        )


    print(
        "\n"
        + "=" * 80
    )

    print(
        "검증 요약"
    )

    print(
        "=" * 80
    )


    print(
        (
            f"JOB 전체                : "
            f"{stats['jobs']}개"
        )
    )


    print(
        (
            f"Skill 사전              : "
            f"{stats['skills']}개"
        )
    )


    print(
        (
            f"명시적 Skill 후보       : "
            f"{stats['raw_matches']}개"
        )
    )


    print(
        (
            f"기존 JOB-Skill 보호     : "
            f"{stats['existing_protected']}개"
        )
    )


    print(
        (
            f"신규 저장 후보          : "
            f"{stats['new_mappings']}개"
        )
    )


    print(
        (
            f"신규 매핑 대상 JOB      : "
            f"{len(stats['jobs_with_new_mapping'])}개"
        )
    )


# ============================================================
# 저장
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

        :relation_type,

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

    DO NOTHING
    """
)


def save_plan(
    plan: list[
        SkillMatch
    ],
) -> int:

    inserted = 0


    with engine.begin() as conn:

        for item in plan:

            result = (
                conn.execute(
                    INSERT_SQL,

                    {
                        "resource_id":
                            item.resource_id,

                        "skill_id":
                            item.skill_id,

                        "relation_type":
                            item.relation_type,

                        "evidence_text":
                            item.evidence_text,

                        "mapping_method":
                            MAPPING_METHOD,

                        "confidence":
                            item.confidence,

                        "source_document_id":
                            item.source_document_id,
                    },
                )
            )


            if (
                result.rowcount
                > 0
            ):

                inserted += 1


    return inserted


# ============================================================
# 저장 후 JOB Skill 현황
# ============================================================

def print_current_job_mappings(
) -> None:

    sql = text(
        """
        SELECT
            r.resource_id,
            r.name AS job_name,

            s.name AS skill_name,

            rs.relation_type,

            rs.confidence,

            rs.mapping_method,

            rs.verified

        FROM resource_skill rs

        JOIN resource r
            ON r.resource_id =
               rs.resource_id

        JOIN skill s
            ON s.skill_id =
               rs.skill_id

        WHERE
            r.resource_type =
                'JOB'

        ORDER BY
            r.resource_id,
            rs.relation_type,
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
        "현재 JOB -> SKILL"
    )

    print(
        "=" * 80
    )


    current_job = None


    for row in rows:

        if (
            row[
                "resource_id"
            ]
            != current_job
        ):

            current_job = (
                row[
                    "resource_id"
                ]
            )


            print(
                (
                    f"\n[{current_job}] "
                    f"{row['job_name']}"
                )
            )


        print(
            (
                f"  -> "
                f"{row['skill_name']} "
                f"| {row['relation_type']} "
                f"| confidence="
                f"{float(row['confidence']):.2f} "
                f"| method="
                f"{row['mapping_method']} "
                f"| verified="
                f"{row['verified']}"
            )
        )


    print(
        "\n총 JOB Skill 매핑:",
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
            "검증된 신규 JOB-Skill "
            "매핑을 실제 저장"
        ),
    )


    parser.add_argument(
        "--show",

        action="store_true",

        help=(
            "현재 JOB-Skill "
            "저장 상태만 출력"
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


    if args.show:

        print_current_job_mappings()

        return


    plan, stats = (
        build_plan()
    )


    print_plan(
        plan,
        stats,
    )


    if not args.save:

        print(
            "\n[DRY RUN]"
        )

        print(
            "DB를 수정하지 않았습니다."
        )

        print(
            (
                "evidence와 REQUIRED/PREFERRED가 "
                "올바른지 확인한 뒤 "
                "--save를 사용하세요."
            )
        )

        return


    inserted = (
        save_plan(
            plan
        )
    )


    print(
        "\n"
        + "=" * 80
    )

    print(
        (
            f"신규 JOB-Skill 저장: "
            f"{inserted}개"
        )
    )

    print(
        "=" * 80
    )


    print_current_job_mappings()


if __name__ == "__main__":
    main()