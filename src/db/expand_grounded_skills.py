
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from sqlalchemy import select

from src.db.database import SessionLocal
from src.db.models import Skill, SkillAlias
from src.db.seed_master import normalize_name


REPORT = Path(
    "data/reports/skill_readiness/"
    "06_skill_candidate_evidence.csv"
)

# 원본 후보 이름:
# (최종 Skill 이름, 카테고리, 설명, 최소 교과목 근거)
APPROVED = {
    "의약품 제형 설계": (
        "의약품 제형 설계",
        "PHARMA_BIOTECH",
        "의약품 제형의 특성과 제조 조건을 고려하여 제형을 설계하는 역량",
        2,
    ),
    "정신건강 간호": (
        "정신건강 간호",
        "NURSING",
        "정신건강 대상자의 간호 요구를 사정하고 간호계획을 수립하는 역량",
        2,
    ),
    "모성 간호": (
        "여성건강 간호",
        "NURSING",
        "여성건강과 관련된 간호 요구를 사정하고 간호중재를 계획하는 역량",
        2,
    ),
    "아동 간호": (
        "아동 간호",
        "NURSING",
        "아동의 성장발달 및 건강 문제를 고려하여 간호를 계획하는 역량",
        2,
    ),
    "지역사회 건강관리": (
        "지역사회 간호",
        "NURSING",
        "지역사회 건강문제를 사정하고 간호·보건교육을 계획하는 역량",
        2,
    ),
    "핵의학 영상검사": (
        "핵의학 영상검사",
        "MEDICAL_IMAGING",
        "핵의학 영상검사의 원리와 검사 절차를 이해하고 적용하는 역량",
        2,
    ),
    "노인 재활": (
        "노인 재활",
        "REHABILITATION",
        "노인의 기능상태와 재활 요구를 평가하고 중재를 계획하는 역량",
        2,
    ),
}


def load_evidence(path: Path) -> dict:

    if not path.is_file():
        raise FileNotFoundError(
            f"검토 보고서가 없습니다: {path}"
        )

    with path.open(
        encoding="utf-8-sig",
        newline="",
    ) as f:
        rows = list(csv.DictReader(f))

    return {
        row["candidate_skill"]: row
        for row in rows
    }


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--report",
        type=Path,
        default=REPORT,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args = parser.parse_args()

    evidence = load_evidence(args.report)

    # DB 쓰기 전에 근거 검증
    for source_name, spec in APPROVED.items():

        row = evidence.get(source_name)

        if row is None:
            raise RuntimeError(
                f"근거 보고서에 후보 없음: {source_name}"
            )

        min_count = spec[3]

        count = int(
            row["unmapped_courses"]
        )

        if count < min_count:
            raise RuntimeError(
                f"{source_name}: 교과목 근거 부족 "
                f"({count}/{min_count})"
            )

    with SessionLocal() as db:

        skills = {
            s.normalized_name: s
            for s in db.scalars(
                select(Skill)
            ).all()
        }

        aliases = {
            a.normalized_alias: a
            for a in db.scalars(
                select(SkillAlias)
            ).all()
        }

        # 충돌이 있으면 아무것도 변경하지 않음
        for source_name, spec in APPROVED.items():

            name = spec[0]
            key = normalize_name(name)

            if key in aliases and key not in skills:
                raise RuntimeError(
                    f"기존 별칭과 충돌: {name}"
                )

        created = 0
        existing = 0

        print(
            "\n[모드]",
            "APPLY" if args.apply else "DRY RUN",
        )

        for source_name, spec in APPROVED.items():

            name, category, description, _ = spec

            key = normalize_name(name)

            row = evidence[source_name]

            if key in skills:
                status = "EXISTS"
                existing += 1
            else:
                status = "NEW"
                created += 1

                if args.apply:
                    db.add(
                        Skill(
                            name=name,
                            normalized_name=key,
                            category=category,
                            description=description,
                            is_active=True,
                        )
                    )

            print(
                f"[{status}] {name} | "
                f"COURSE={row['unmapped_courses']} "
                f"JOB={row['parsed_jobs']}"
            )

            print(
                "  근거 Resource IDs:",
                row["sample_course_resource_ids"],
            )

        if args.apply:
            db.commit()
            print(
                f"\n[COMMIT] 신규={created}, "
                f"기존={existing}"
            )
        else:
            print(
                f"\n[DRY RUN] 추가 예정={created}, "
                f"기존={existing}"
            )

        print(
            "ResourceSkill 및 StudentSkill은 "
            "변경하지 않았습니다."
        )


if __name__ == "__main__":
    main()
