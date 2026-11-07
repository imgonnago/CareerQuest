
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from sqlalchemy import select

from src.db.database import SessionLocal
from src.db.models import (
    CourseDetail,
    Department,
    Resource,
    Skill,
    SkillAlias,
)
from src.db.seed_master import normalize_name


SOURCE = Path(
    "data/reports/skill_mapping/"
    "careerquest_new_skill_proposals_2026.csv"
)

# 교과목명 근거가 확인된 신규 Skill 28개.
# Skill 등록 자체는 숙련도나 면허 취득을 의미하지 않음.
DESCRIPTIONS = {
    "성인환자 간호":
        "성인 대상자의 건강문제를 사정하고 간호계획과 중재 원칙을 적용하는 역량",

    "간호관리 및 리더십":
        "간호 조직의 업무와 인력·환자안전 관리 및 팀 리더십을 이해하는 역량",

    "호스피스·완화간호":
        "말기 및 중증 질환 대상자의 증상 완화와 전인적 돌봄 원칙을 이해하는 역량",

    "연하재활":
        "연하 기능과 일상생활 영향을 평가하고 재활중재 원칙을 이해하는 역량",

    "정신사회적 작업치료":
        "정신사회적 기능과 작업 수행을 평가하고 중재를 계획하는 역량",

    "정형도수치료":
        "근골격계 문제에 적용하는 정형도수치료의 평가 및 중재 원칙을 이해하는 역량",

    "수중재활운동":
        "수중 환경의 물리적 특성을 고려해 재활운동을 계획하는 역량",

    "심폐 재활중재":
        "심폐기능 제한에 따른 평가와 재활중재 원칙을 이해하는 역량",

    "스포츠 재활중재":
        "스포츠 활동 관련 손상과 기능회복에 필요한 재활 원칙을 이해하는 역량",

    "수혈검사":
        "수혈 적합성 평가를 위한 검사 원리와 품질관리 절차를 이해하는 역량",

    "조직병리 검사":
        "조직 병리검사의 처리·염색·관찰 기본 원칙을 이해하는 역량",

    "진단세포 검사":
        "세포 검사에 필요한 검체 처리와 관찰 원리를 이해하는 역량",

    "임상생리기능 검사":
        "생리기능검사 원리와 측정 결과의 품질을 이해하는 역량",

    "투시조영 영상검사":
        "투시조영 영상검사의 원리·절차와 안전관리를 이해하는 역량",

    "방사선치료 원리 이해":
        "방사선치료의 기초 원리와 안전 요소를 이해하는 역량",

    "치과방사선 검사 원리":
        "치과 방사선영상의 원리와 관련 방사선 안전요건을 이해하는 역량",

    "심전도 판독 기초":
        "기본 심전도 파형과 주요 이상 소견을 구별하는 기초 역량",

    "전문심장소생술 절차 이해":
        "전문심장소생술의 절차와 팀 협력 원칙을 이해하는 역량",

    "질병 및 의료행위 분류코딩":
        "질병·의료행위의 분류체계를 이해하고 분류코딩 원칙을 적용하는 역량",

    "암등록 데이터 관리":
        "암등록의 목적과 표준 항목·자료 품질관리 원칙을 이해하는 역량",

    "의무기록정보 질관리":
        "의무기록의 완전성·정확성·일관성 개선을 위한 품질관리 역량",

    "생체신호 처리":
        "생체신호에 기초적인 필터링과 특징 추출을 적용하는 역량",

    "IoT 시스템 제어":
        "센서·통신·제어 기능을 결합한 IoT 시스템의 동작을 구현·점검하는 역량",

    "QbD 기반 제약품질 설계":
        "품질고도화(QbD) 원칙과 주요 공정변수를 고려해 품질을 설계하는 역량",

    "GMP 제조공정 운영 기초":
        "GMP 기준에 따른 제조공정 운영과 기록·품질관리의 기초를 이해하는 역량",

    "치유환경 색채디자인":
        "공간 사용자의 경험과 환경 특성을 고려한 색채디자인을 계획하는 역량",

    "강화학습":
        "보상과 정책 개념에 기반한 강화학습 문제를 모델링하고 평가하는 역량",

    "온디바이스 AI 개발":
        "단말 환경의 연산·메모리 제약을 고려한 AI 실행 구조를 이해하고 구현하는 역량",
}


def parse_ids(value: str) -> set[int]:
    try:
        return {
            int(token.strip())
            for token in value.split("/")
            if token.strip()
        }
    except ValueError as exc:
        raise ValueError(
            f"잘못된 교과목 ID: {value!r}"
        ) from exc


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--csv",
        type=Path,
        default=SOURCE,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.csv.is_file():
        parser.error(
            f"후보 CSV가 없습니다: {args.csv}"
        )

    with args.csv.open(
        encoding="utf-8-sig",
        newline="",
    ) as f:
        reader = csv.DictReader(f)

        required = {
            "신규 Skill 후보",
            "분야",
            "course_resource_ids",
            "근거 교과목 수",
        }

        headers = set(
            reader.fieldnames or []
        )

        if not required.issubset(headers):
            parser.error(
                f"필수 열 누락: "
                f"{required - headers}"
            )

        catalog = {
            row["신규 Skill 후보"].strip(): row
            for row in reader
        }

    absent = (
        DESCRIPTIONS.keys()
        - catalog.keys()
    )

    if absent:
        raise ValueError(
            f"CSV에 없는 후보: {sorted(absent)}"
        )

    with SessionLocal() as db:

        existing = {
            item.normalized_name: item
            for item in db.scalars(
                select(Skill)
            ).all()
        }

        aliases = {
            item.normalized_alias: item.skill_id
            for item in db.scalars(
                select(SkillAlias)
            ).all()
        }

        # 2026년 공식 교과목만 검증
        # 데이터의학과는 MVP에서 제외
        course_ids = set(
            db.scalars(
                select(CourseDetail.resource_id)
                .join(
                    Resource,
                    Resource.resource_id
                    == CourseDetail.resource_id,
                )
                .join(
                    Department,
                    Department.department_id
                    == CourseDetail.department_id,
                )
                .where(
                    CourseDetail.curriculum_year
                    == 2026,

                    Resource.resource_type
                    == "COURSE",

                    Department.name
                    != "데이터의학과",
                )
            ).all()
        )

        created = 0
        skipped = 0

        print(
            "[모드]",
            "APPLY" if args.apply
            else "DRY RUN",
        )

        for name, description in (
            DESCRIPTIONS.items()
        ):

            row = catalog[name]

            evidence_ids = parse_ids(
                row["course_resource_ids"]
            )

            valid_ids = (
                evidence_ids & course_ids
            )

            if not valid_ids:
                raise ValueError(
                    f"공식 교과목 근거 없음: {name}"
                )

            category = row["분야"].strip()

            if not category or not description:
                raise ValueError(
                    f"카테고리/설명 누락: {name}"
                )

            key = normalize_name(name)

            alias_owner = aliases.get(key)
            skill = existing.get(key)

            if (
                alias_owner is not None
                and (
                    skill is None
                    or skill.skill_id
                    != alias_owner
                )
            ):
                raise ValueError(
                    f"기존 별칭과 충돌: {name}"
                )

            if skill is not None:
                skipped += 1
                status = "EXISTS"

            else:
                created += 1
                status = "NEW"

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
                f"[{status}] {name} "
                f"| {category} "
                f"| 근거 교과목="
                f"{len(valid_ids)}"
            )

        if args.apply:
            db.commit()

            print(
                f"[COMMIT] 신규={created}, "
                f"기존={skipped}"
            )

        else:
            print(
                f"[DRY RUN] 추가 예정="
                f"{created}, 기존={skipped}"
            )

        print(
            "ResourceSkill, StudentSkill, "
            "Evidence 변경 없음"
        )


if __name__ == "__main__":
    main()
