from __future__ import annotations

import argparse

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text

from src.db.database import engine


# ============================================================
# 설정
# ============================================================

REQUIRED_WEIGHT = Decimal("1.0")
PREFERRED_WEIGHT = Decimal("0.6")
RELATED_WEIGHT = Decimal("0.3")

MIN_SKILL_CONFIDENCE = Decimal("0.80")
MIN_ROLE_CONFIDENCE = Decimal("0.80")


RELATION_PRIORITY = {
    "REQUIRED": 3,
    "PREFERRED": 2,
    "RELATED": 1,
}


# ============================================================
# 데이터 구조
# ============================================================

@dataclass
class PostingSkill:
    resource_id: int

    job_role_id: int
    role_name: str

    skill_id: int
    skill_name: str

    relation_type: str

    confidence: Decimal | None
    verified: bool


@dataclass
class RoleSkillAggregate:
    job_role_id: int
    role_name: str

    skill_id: int
    skill_name: str

    required_count: int
    preferred_count: int
    related_count: int

    analyzed_posting_count: int

    demand_score: Decimal


# ============================================================
# Role이 연결된 활성 JOB
# ============================================================

def load_role_postings(
) -> dict[int, dict]:

    sql = text(
        """
        SELECT
            rjr.job_role_id,
            jr.name AS role_name,
            r.resource_id

        FROM resource_job_role rjr

        JOIN resource r
            ON r.resource_id =
               rjr.resource_id

        JOIN job_role jr
            ON jr.job_role_id =
               rjr.job_role_id

        WHERE
            r.resource_type =
                'JOB'

          AND r.is_active =
                TRUE

          AND rjr.is_primary =
                TRUE

          AND jr.is_active =
                TRUE

          AND COALESCE(
                rjr.confidence,
                0
              ) >= :min_role_confidence

        ORDER BY
            rjr.job_role_id,
            r.resource_id
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql,
                {
                    "min_role_confidence":
                        MIN_ROLE_CONFIDENCE,
                },
            ).mappings().all()
        )


    result: dict[
        int,
        dict,
    ] = {}


    for row in rows:

        role_id = int(
            row[
                "job_role_id"
            ]
        )


        if role_id not in result:

            result[
                role_id
            ] = {
                "role_name":
                    row[
                        "role_name"
                    ],

                "posting_ids":
                    set(),
            }


        result[
            role_id
        ][
            "posting_ids"
        ].add(
            int(
                row[
                    "resource_id"
                ]
            )
        )


    return result


# ============================================================
# 실제 JOB -> Skill 데이터 로딩
# ============================================================

def load_posting_skills(
) -> list[
    PostingSkill
]:

    sql = text(
        """
        SELECT
            r.resource_id,

            rjr.job_role_id,
            jr.name AS role_name,

            rs.skill_id,
            s.name AS skill_name,

            rs.relation_type,

            rs.confidence,
            rs.verified

        FROM resource r

        JOIN resource_job_role rjr
            ON rjr.resource_id =
               r.resource_id

           AND rjr.is_primary =
               TRUE

        JOIN job_role jr
            ON jr.job_role_id =
               rjr.job_role_id

        JOIN resource_skill rs
            ON rs.resource_id =
               r.resource_id

        JOIN skill s
            ON s.skill_id =
               rs.skill_id

        WHERE
            r.resource_type =
                'JOB'

          AND r.is_active =
                TRUE

          AND jr.is_active =
                TRUE

          AND s.is_active =
                TRUE

          AND COALESCE(
                rjr.confidence,
                0
              ) >= :min_role_confidence

          AND rs.relation_type IN (
                'REQUIRED',
                'PREFERRED',
                'RELATED'
          )

          AND (
                rs.verified = TRUE

                OR

                COALESCE(
                    rs.confidence,
                    0
                ) >= :min_skill_confidence
          )

        ORDER BY
            r.resource_id,
            rjr.job_role_id,
            rs.skill_id
        """
    )


    with engine.connect() as conn:

        rows = (
            conn.execute(
                sql,
                {
                    "min_role_confidence":
                        MIN_ROLE_CONFIDENCE,

                    "min_skill_confidence":
                        MIN_SKILL_CONFIDENCE,
                },
            ).mappings().all()
        )


    result: list[
        PostingSkill
    ] = []


    for row in rows:

        confidence = (
            Decimal(
                str(
                    row[
                        "confidence"
                    ]
                )
            )

            if row[
                "confidence"
            ]
            is not None

            else None
        )


        result.append(
            PostingSkill(
                resource_id=int(
                    row[
                        "resource_id"
                    ]
                ),

                job_role_id=int(
                    row[
                        "job_role_id"
                    ]
                ),

                role_name=row[
                    "role_name"
                ],

                skill_id=int(
                    row[
                        "skill_id"
                    ]
                ),

                skill_name=row[
                    "skill_name"
                ],

                relation_type=row[
                    "relation_type"
                ],

                confidence=confidence,

                verified=bool(
                    row[
                        "verified"
                    ]
                ),
            )
        )


    return result


# ============================================================
# 한 공고에서 같은 Skill 중복 제거
#
# REQUIRED > PREFERRED > RELATED
#
# resource_id뿐 아니라 job_role_id도 key에 포함한다.
# ============================================================

def collapse_posting_skills(
    rows: list[
        PostingSkill
    ],
) -> list[
    PostingSkill
]:

    best: dict[
        tuple[
            int,
            int,
            int,
        ],
        PostingSkill,
    ] = {}


    for row in rows:

        key = (
            row.resource_id,
            row.job_role_id,
            row.skill_id,
        )


        current = (
            best.get(
                key
            )
        )


        if current is None:

            best[
                key
            ] = row

            continue


        current_priority = (
            RELATION_PRIORITY.get(
                current.relation_type,
                0,
            )
        )


        new_priority = (
            RELATION_PRIORITY.get(
                row.relation_type,
                0,
            )
        )


        if (
            new_priority
            > current_priority
        ):

            best[
                key
            ] = row


    return list(
        best.values()
    )


# ============================================================
# Demand Score
#
# REQUIRED  = 1.0
# PREFERRED = 0.6
# RELATED   = 0.3
#
# 현재는 전체 Role 공고가 아니라
# Skill 분석이 완료된 공고 수를 분모로 사용한다.
# ============================================================

def calculate_demand_score(
    required_count: int,
    preferred_count: int,
    related_count: int,
    analyzed_posting_count: int,
) -> Decimal:

    if (
        analyzed_posting_count
        <= 0
    ):

        return Decimal(
            "0.00000"
        )


    weighted = (
        Decimal(
            required_count
        )
        * REQUIRED_WEIGHT

        + Decimal(
            preferred_count
        )
        * PREFERRED_WEIGHT

        + Decimal(
            related_count
        )
        * RELATED_WEIGHT
    )


    score = (
        weighted
        / Decimal(
            analyzed_posting_count
        )
    )


    score = min(
        score,
        Decimal("1"),
    )


    return score.quantize(
        Decimal(
            "0.00001"
        )
    )


# ============================================================
# 집계
# ============================================================

def build_aggregates(
):

    role_postings = (
        load_role_postings()
    )


    raw_rows = (
        load_posting_skills()
    )


    rows = (
        collapse_posting_skills(
            raw_rows
        )
    )


    analyzed_by_role: dict[
        int,
        set[int],
    ] = defaultdict(
        set
    )


    grouped: dict[
        tuple[
            int,
            int,
        ],
        dict,
    ] = {}


    # --------------------------------------------------------
    # Role + Skill별 count
    # --------------------------------------------------------

    for row in rows:

        analyzed_by_role[
            row.job_role_id
        ].add(
            row.resource_id
        )


        key = (
            row.job_role_id,
            row.skill_id,
        )


        if key not in grouped:

            grouped[
                key
            ] = {
                "job_role_id":
                    row.job_role_id,

                "role_name":
                    row.role_name,

                "skill_id":
                    row.skill_id,

                "skill_name":
                    row.skill_name,

                "required_count":
                    0,

                "preferred_count":
                    0,

                "related_count":
                    0,
            }


        if (
            row.relation_type
            == "REQUIRED"
        ):

            grouped[
                key
            ][
                "required_count"
            ] += 1


        elif (
            row.relation_type
            == "PREFERRED"
        ):

            grouped[
                key
            ][
                "preferred_count"
            ] += 1


        elif (
            row.relation_type
            == "RELATED"
        ):

            grouped[
                key
            ][
                "related_count"
            ] += 1


    # --------------------------------------------------------
    # 최종 RoleSkill
    # --------------------------------------------------------

    aggregates: list[
        RoleSkillAggregate
    ] = []


    for data in (
        grouped.values()
    ):

        analyzed_count = len(
            analyzed_by_role[
                data[
                    "job_role_id"
                ]
            ]
        )


        if (
            analyzed_count
            <= 0
        ):

            continue


        demand_score = (
            calculate_demand_score(
                required_count=(
                    data[
                        "required_count"
                    ]
                ),

                preferred_count=(
                    data[
                        "preferred_count"
                    ]
                ),

                related_count=(
                    data[
                        "related_count"
                    ]
                ),

                analyzed_posting_count=(
                    analyzed_count
                ),
            )
        )


        aggregates.append(
            RoleSkillAggregate(
                job_role_id=(
                    data[
                        "job_role_id"
                    ]
                ),

                role_name=(
                    data[
                        "role_name"
                    ]
                ),

                skill_id=(
                    data[
                        "skill_id"
                    ]
                ),

                skill_name=(
                    data[
                        "skill_name"
                    ]
                ),

                required_count=(
                    data[
                        "required_count"
                    ]
                ),

                preferred_count=(
                    data[
                        "preferred_count"
                    ]
                ),

                related_count=(
                    data[
                        "related_count"
                    ]
                ),

                analyzed_posting_count=(
                    analyzed_count
                ),

                demand_score=(
                    demand_score
                ),
            )
        )


    aggregates.sort(
        key=lambda x: (
            x.role_name,
            -x.demand_score,
            -x.required_count,
            x.skill_name,
        )
    )


    # --------------------------------------------------------
    # Role별 분석 커버리지
    # --------------------------------------------------------

    coverage: list[
        dict
    ] = []


    for (
        role_id,
        info,
    ) in role_postings.items():

        total = len(
            info[
                "posting_ids"
            ]
        )


        analyzed = len(
            analyzed_by_role.get(
                role_id,
                set(),
            )
        )


        ratio = (
            analyzed
            / total

            if total

            else 0.0
        )


        coverage.append(
            {
                "job_role_id":
                    role_id,

                "role_name":
                    info[
                        "role_name"
                    ],

                "total_postings":
                    total,

                "analyzed_postings":
                    analyzed,

                "coverage":
                    ratio,
            }
        )


    coverage.sort(
        key=lambda x:
            x[
                "role_name"
            ]
    )


    stats = {
        "roles_with_postings":
            len(
                role_postings
            ),

        "raw_job_skill_rows":
            len(
                raw_rows
            ),

        "collapsed_job_skill_rows":
            len(
                rows
            ),

        "role_skill_rows":
            len(
                aggregates
            ),

        "total_role_postings":
            sum(
                len(
                    x[
                        "posting_ids"
                    ]
                )

                for x
                in role_postings.values()
            ),

        "total_analyzed_postings":
            sum(
                len(
                    x
                )

                for x
                in analyzed_by_role.values()
            ),
    }


    return (
        aggregates,
        coverage,
        stats,
    )


# ============================================================
# 미리보기
# ============================================================

def print_preview(
    aggregates: list[
        RoleSkillAggregate
    ],

    coverage: list[
        dict
    ],

    stats: dict,
) -> None:

    print(
        "=" * 95
    )


    print(
        "CareerQuest - RoleSkill 임시 집계 v2"
    )


    print(
        "=" * 95
    )


    print(
        "\n[Role별 분석 커버리지]"
    )


    for row in coverage:

        warning = ""


        if (
            row[
                "analyzed_postings"
            ]
            < 3
        ):

            warning = (
                "  [표본 적음]"
            )


        elif (
            row[
                "coverage"
            ]
            < 0.8
        ):

            warning = (
                "  [매핑 커버리지 낮음]"
            )


        print(
            (
                f"{row['role_name']:<30} "
                f"Role JOB="
                f"{row['total_postings']:<3} "
                f"분석 JOB="
                f"{row['analyzed_postings']:<3} "
                f"coverage="
                f"{row['coverage']:.1%}"
                f"{warning}"
            )
        )


    current_role = None


    for row in aggregates:

        if (
            row.role_name
            != current_role
        ):

            current_role = (
                row.role_name
            )


            print(
                "\n"
                + "-" * 95
            )


            print(
                (
                    f"[{row.role_name}] "
                    f"분석 표본="
                    f"{row.analyzed_posting_count}개"
                )
            )


            print(
                "-" * 95
            )


        print(
            (
                f"{row.skill_name:<28} "
                f"REQ={row.required_count:<3} "
                f"PREF={row.preferred_count:<3} "
                f"REL={row.related_count:<3} "
                f"DEMAND="
                f"{row.demand_score}"
            )
        )


    print(
        "\n"
        + "=" * 95
    )


    print(
        "집계 요약"
    )


    print(
        "=" * 95
    )


    print(
        (
            "JobRole 연결 활성 JOB : "
            f"{stats['total_role_postings']}개"
        )
    )


    print(
        (
            "Skill 분석 가능 JOB   : "
            f"{stats['total_analyzed_postings']}개"
        )
    )


    print(
        (
            "Role 수               : "
            f"{stats['roles_with_postings']}개"
        )
    )


    print(
        (
            "원 JOB-Skill 행       : "
            f"{stats['raw_job_skill_rows']}개"
        )
    )


    print(
        (
            "중복 정리 후          : "
            f"{stats['collapsed_job_skill_rows']}개"
        )
    )


    print(
        (
            "최종 RoleSkill 행     : "
            f"{stats['role_skill_rows']}개"
        )
    )


# ============================================================
# 저장
#
# 모든 RoleSkill을 무조건 DELETE하지 않고,
# 이번에 실제 집계 대상이 된 Role만 재생성한다.
# ============================================================

def save_aggregates(
    aggregates: list[
        RoleSkillAggregate
    ],

    coverage: list[
        dict
    ],
) -> int:

    role_ids = [
        row[
            "job_role_id"
        ]

        for row
        in coverage
    ]


    insert_sql = text(
        """
        INSERT INTO role_skill (
            job_role_id,
            skill_id,

            required_count,
            preferred_count,
            related_count,

            total_posting_count,

            demand_score,

            last_aggregated_at
        )

        VALUES (
            :job_role_id,
            :skill_id,

            :required_count,
            :preferred_count,
            :related_count,

            :total_posting_count,

            :demand_score,

            NOW()
        )
        """
    )


    inserted = 0


    with engine.begin() as conn:

        # ----------------------------------------------------
        # 이번에 집계하는 Role만 초기화
        # ----------------------------------------------------

        for role_id in role_ids:

            conn.execute(
                text(
                    """
                    DELETE FROM role_skill

                    WHERE job_role_id =
                        :job_role_id
                    """
                ),

                {
                    "job_role_id":
                        role_id,
                },
            )


        # ----------------------------------------------------
        # 새 집계 저장
        # ----------------------------------------------------

        for row in aggregates:

            result = (
                conn.execute(
                    insert_sql,

                    {
                        "job_role_id":
                            row.job_role_id,

                        "skill_id":
                            row.skill_id,

                        "required_count":
                            row.required_count,

                        "preferred_count":
                            row.preferred_count,

                        "related_count":
                            row.related_count,

                        # 현재 의미는
                        # '집계에 사용된 공고 표본 수'
                        "total_posting_count":
                            row.analyzed_posting_count,

                        "demand_score":
                            row.demand_score,
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
# 현재 RoleSkill 출력
# ============================================================

def show_current(
) -> None:

    sql = text(
        """
        SELECT
            jr.name AS role_name,

            s.name AS skill_name,

            rs.required_count,
            rs.preferred_count,
            rs.related_count,

            rs.total_posting_count,

            rs.demand_score

        FROM role_skill rs

        JOIN job_role jr
            ON jr.job_role_id =
               rs.job_role_id

        JOIN skill s
            ON s.skill_id =
               rs.skill_id

        ORDER BY
            jr.name,
            rs.demand_score DESC,
            rs.required_count DESC,
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
        "=" * 95
    )


    print(
        "현재 RoleSkill"
    )


    print(
        "=" * 95
    )


    current_role = None


    for row in rows:

        if (
            row[
                "role_name"
            ]
            != current_role
        ):

            current_role = (
                row[
                    "role_name"
                ]
            )


            print(
                (
                    f"\n[{current_role}]"
                )
            )


        print(
            (
                f"  {row['skill_name']:<28} "
                f"REQ="
                f"{row['required_count']:<3} "
                f"PREF="
                f"{row['preferred_count']:<3} "
                f"REL="
                f"{row['related_count']:<3} "
                f"SAMPLE="
                f"{row['total_posting_count']:<3} "
                f"DEMAND="
                f"{float(row['demand_score']):.5f}"
            )
        )


    print()


    print(
        (
            "총 RoleSkill: "
            f"{len(rows)}개"
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


    parser.add_argument(
        "--save",
        action="store_true",
    )


    parser.add_argument(
        "--show",
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


    if args.show:

        show_current()

        return


    (
        aggregates,
        coverage,
        stats,
    ) = build_aggregates()


    print_preview(
        aggregates,
        coverage,
        stats,
    )


    if not args.save:

        print()


        print(
            "[DRY RUN]"
        )


        print(
            "DB를 수정하지 않았습니다."
        )


        print(
            (
                "현재 결과는 표본이 작은 "
                "임시 RoleSkill 집계입니다."
            )
        )


        return


    inserted = (
        save_aggregates(
            aggregates,
            coverage,
        )
    )


    print()


    print(
        (
            "RoleSkill 저장 완료: "
            f"{inserted}개"
        )
    )


    show_current()


if __name__ == "__main__":

    main()