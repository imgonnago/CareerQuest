
from __future__ import annotations

import argparse
import csv
import re

from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from src.db.database import SessionLocal
from src.db.models import (
    CourseDetail,
    Department,
    Resource,
    ResourceSkill,
    Skill,
)


SOURCE_CSV = Path(
    "data/reports/skill_mapping/"
    "careerquest_new_skill_proposals_2026.csv"
)


# 신규 28개 Skill의 2026 교과목명 검증 규칙.
# 정확히 확인되는 주제 관련성만 RELATED로 등록.
# TAUGHT와 StudentSkill은 수정하지 않음.

COURSE_TITLE_RULES = {
    "성인환자 간호": r"성인간호",
    "간호관리 및 리더십": r"간호관리",
    "호스피스·완화간호": r"호스피스",
    "연하재활": r"연하재활",
    "정신사회적 작업치료": r"정신사회.*작업치료",
    "정형도수치료": r"정형도수치료",
    "수중재활운동": r"수중재활",
    "심폐 재활중재": r"심폐.*물리치료|심폐.*재활",
    "스포츠 재활중재": r"스포츠.*물리치료|스포츠.*재활",
    "수혈검사": r"수혈의학|수혈검사",
    "조직병리 검사": r"조직검사|조직병리",
    "진단세포 검사": r"진단세포",
    "임상생리기능 검사": r"임상생리",
    "투시조영 영상검사": r"투시조영",
    "방사선치료 원리 이해": r"방사선치료",
    "치과방사선 검사 원리": r"치과방사선",
    "심전도 판독 기초": r"심전도",
    "전문심장소생술 절차 이해":
        r"전문심장구조|전문심장소생",
    "질병 및 의료행위 분류코딩":
        r"질병.*의료행위.*분류",
    "암등록 데이터 관리": r"암등록",
    "의무기록정보 질관리": r"의무기록정보.*질",
    "생체신호 처리": r"생체신호처리",
    "IoT 시스템 제어": r"iot.*시스템.*제어",
    "QbD 기반 제약품질 설계": r"qbd",
    "GMP 제조공정 운영 기초": r"gmp",
    "치유환경 색채디자인": r"치유.*색채",
    "강화학습": r"강화학습",
    "온디바이스 AI 개발": r"온디바이스ai",
}


def compact(value: str) -> str:
    return re.sub(
        r"\s+", "", value or ""
    ).casefold()


def load_catalog(path: Path):

    if not path.is_file():
        raise FileNotFoundError(
            f"후보 CSV 없음: {path}"
        )

    data = {}

    with path.open(
        encoding="utf-8-sig",
        newline="",
    ) as f:

        reader = csv.DictReader(f)

        required = {
            "신규 Skill 후보",
            "course_resource_ids",
        }

        if not required.issubset(
            set(reader.fieldnames or [])
        ):
            raise ValueError(
                f"CSV 열 불일치: {reader.fieldnames}"
            )

        for row in reader:

            name = row[
                "신규 Skill 후보"
            ].strip()

            if name not in COURSE_TITLE_RULES:
                continue

            if name in data:
                raise ValueError(
                    f"중복 Skill 후보: {name}"
                )

            parts = [
                part.strip()
                for part in row[
                    "course_resource_ids"
                ].split("/")
                if part.strip()
            ]

            if (
                not parts
                or not all(
                    part.isdigit()
                    for part in parts
                )
            ):
                raise ValueError(
                    f"근거 ID 오류: {name}: {parts}"
                )

            ids = [
                int(part)
                for part in parts
            ]

            if len(ids) != len(set(ids)):
                raise ValueError(
                    f"근거 ID 중복: {name}"
                )

            data[name] = ids

    missing = (
        set(COURSE_TITLE_RULES)
        - set(data)
    )

    if missing:
        raise ValueError(
            f"CSV에 없는 Skill: {sorted(missing)}"
        )

    return data


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "신규 Skill 28개와 교과목 "
            "RELATED 매핑"
        )
    )

    parser.add_argument(
        "--csv",
        type=Path,
        default=SOURCE_CSV,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args = parser.parse_args()

    catalog = load_catalog(
        args.csv
    )

    all_ids = {
        rid
        for ids in catalog.values()
        for rid in ids
    }

    with SessionLocal() as db:

        courses = db.execute(
            select(
                CourseDetail.resource_id,
                Resource.name,
                Department.name,
            )
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
                CourseDetail.resource_id.in_(
                    sorted(all_ids)
                ),
                CourseDetail.curriculum_year
                == 2026,
                Resource.resource_type
                == "COURSE",
                Department.name
                != "데이터의학과",
            )
        ).all()

        courses_by_id = {
            int(resource_id): (
                name,
                department,
            )
            for resource_id, name, department
            in courses
        }

        skills = db.scalars(
            select(Skill).where(
                Skill.name.in_(
                    list(catalog)
                ),
                Skill.is_active.is_(True),
            )
        ).all()

        skills_by_name = {
            skill.name: skill
            for skill in skills
        }

        existing_links = set(
            db.execute(
                select(
                    ResourceSkill.resource_id,
                    ResourceSkill.skill_id,
                ).where(
                    ResourceSkill.resource_id.in_(
                        sorted(all_ids)
                    ),
                    ResourceSkill.relation_type
                    == "RELATED",
                )
            ).all()
        )

        problems = []
        proposed = []

        for skill_name, ids in catalog.items():

            skill = skills_by_name.get(
                skill_name
            )

            if skill is None:
                problems.append(
                    f"Skill 누락: {skill_name}"
                )
                continue

            pattern = re.compile(
                COURSE_TITLE_RULES[skill_name],
                re.IGNORECASE,
            )

            for resource_id in ids:

                course = courses_by_id.get(
                    resource_id
                )

                if course is None:
                    problems.append(
                        f"COURSE 미확인: "
                        f"{skill_name}/{resource_id}"
                    )
                    continue

                course_title, department = course

                if not pattern.search(
                    compact(course_title)
                ):
                    problems.append(
                        f"명칭 검증 실패: "
                        f"{skill_name} / "
                        f"{course_title}"
                    )
                    continue

                key = (
                    resource_id,
                    int(skill.skill_id),
                )

                if key in existing_links:
                    continue

                proposed.append((
                    resource_id,
                    skill.skill_id,
                    skill_name,
                    course_title,
                    department,
                ))

        print(
            f"[검증] Skill={len(catalog)}개, "
            f"교과목={len(all_ids)}개"
        )

        # 검증에 실패한 항목이 하나라도 있으면
        # 전체 DB 저장을 취소
        if problems:

            for problem in problems:
                print("[ERROR]", problem)

            raise SystemExit(
                "DB 변경 없이 중단"
            )

        print(
            f"[매핑 후보] 신규 RELATED="
            f"{len(proposed)}건"
        )

        for (
            resource_id,
            skill_id,
            skill_name,
            course_title,
            department,
        ) in proposed:

            print(
                f"  {department} | "
                f"{course_title}({resource_id}) "
                f"→ {skill_name}({skill_id})"
            )

        if not args.apply:

            print(
                "[DRY RUN] DB 변경 없음"
            )

            return

        for (
            resource_id,
            skill_id,
            skill_name,
            course_title,
            department,
        ) in proposed:

            db.add(
                ResourceSkill(
                    resource_id=resource_id,
                    skill_id=skill_id,
                    relation_type="RELATED",
                    evidence_text=(
                        f"2026년 {department} "
                        f"교과목명({course_title})과 "
                        f"Skill({skill_name})의 "
                        "주제 관련성 확인. "
                        "학습성과 및 숙련도 미확인."
                    ),
                    mapping_method=(
                        "CURRICULUM_TITLE_REVIEW"
                    ),
                    confidence=Decimal("0.60"),
                    verified=False,
                )
            )

        db.commit()

        print(
            f"[COMMIT] RELATED "
            f"{len(proposed)}건 추가"
        )

        print(
            "TAUGHT / StudentSkill "
            "변경 없음"
        )


if __name__ == "__main__":
    main()
