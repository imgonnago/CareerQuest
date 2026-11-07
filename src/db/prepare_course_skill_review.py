
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
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
    SkillAlias,
)


YEAR = 2026

EXCLUDED_DEPARTMENTS = {
    "데이터의학과",
}

REVIEW_FILE = Path(
    "data/reports/skill_mapping/"
    "course_skill_candidates_2026.csv"
)

# 실제 2026 교육과정에서 확인된 교과목명과
# 신규 Skill 7개의 보수적인 연결 후보 규칙
SPECIAL = {
    "의약품 제형 설계":
        r"제제학|제제공학",

    "정신건강 간호":
        r"정신간호",

    "여성건강 간호":
        r"여성건강간호",

    "아동 간호":
        r"아동간호",

    "지역사회 간호":
        r"지역사회간호",

    "핵의학 영상검사":
        r"핵의학",

    "노인 재활":
        r"노인작업치료|노인물리치료",
}


FIELDS = [
    "decision",
    "review_note",
    "department",
    "course_resource_id",
    "course_name",
    "skill_id",
    "skill_name",
    "match_reason",
]


def compact(value: str) -> str:
    return re.sub(
        r"[^0-9A-Za-z가-힣]+",
        "",
        value or "",
    ).casefold()


def load_db(session):

    courses = session.execute(
        select(
            CourseDetail.resource_id,
            Department.name.label(
                "department"
            ),
            Resource.name.label(
                "course_name"
            ),
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
            CourseDetail.curriculum_year == YEAR,
            Resource.resource_type == "COURSE",
        )
        .order_by(
            Department.name,
            Resource.name,
            CourseDetail.resource_id,
        )
    ).mappings().all()

    skills = session.scalars(
        select(Skill).where(
            Skill.is_active.is_(True)
        )
    ).all()

    aliases = session.scalars(
        select(SkillAlias)
    ).all()

    mapped = set(
        session.execute(
            select(
                ResourceSkill.resource_id,
                ResourceSkill.skill_id,
            )
        ).all()
    )

    return (
        courses,
        skills,
        aliases,
        mapped,
    )


def propose(
    courses,
    skills,
    aliases,
    mapped,
):

    names = {
        skill.skill_id: {skill.name}
        for skill in skills
    }

    for alias in aliases:
        if alias.skill_id in names:
            names[alias.skill_id].add(
                alias.alias
            )

    rows = []
    seen = set()

    for course in courses:

        department = course["department"]

        if department in EXCLUDED_DEPARTMENTS:
            continue

        course_id = int(
            course["resource_id"]
        )

        title = course["course_name"]
        plain = compact(title)

        for skill in skills:

            skill_id = int(
                skill.skill_id
            )

            key = (
                course_id,
                skill_id,
            )

            if key in mapped or key in seen:
                continue

            reason = None

            pattern = SPECIAL.get(
                skill.name
            )

            if pattern and re.search(
                pattern,
                plain,
            ):
                reason = (
                    "2026_CURRICULUM_TOPIC"
                )

            elif any(
                len(compact(label)) >= 4
                and compact(label) in plain
                for label in names[skill_id]
            ):
                reason = (
                    "SKILL_NAME_OR_ALIAS_IN_TITLE"
                )

            if not reason:
                continue

            seen.add(key)

            rows.append({
                "decision": "PENDING",
                "review_note": "",
                "department": department,
                "course_resource_id": course_id,
                "course_name": title,
                "skill_id": skill_id,
                "skill_name": skill.name,
                "match_reason": reason,
            })

    return rows


def prepare(path: Path) -> None:

    # 이전 검토 결과를 덮어쓰지 않음
    if path.exists():
        raise FileExistsError(
            f"기존 검토 파일이 있습니다: {path}\n"
            "승인 기록을 보호하기 위해 "
            "덮어쓰지 않습니다."
        )

    with SessionLocal() as session:
        courses, skills, aliases, mapped = (
            load_db(session)
        )

        rows = propose(
            courses,
            skills,
            aliases,
            mapped,
        )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as output:

        writer = csv.DictWriter(
            output,
            fieldnames=FIELDS,
        )

        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(
        row["department"]
        for row in rows
    )

    print(
        f"[READ ONLY] "
        f"2026 COURSE={len(courses)}, "
        f"SKILL={len(skills)}"
    )

    print(
        "[MVP] 데이터의학과 제외"
    )

    print(
        f"[검토 후보] {len(rows)}건"
    )

    for department, count in sorted(
        counts.items()
    ):
        print(
            f"  {department}: {count}건"
        )

    print(
        f"[CSV] {path}"
    )

    print(
        "decision=PENDING"
        " / DB 변경 없음"
    )


def apply_reviewed(path: Path) -> None:

    if not path.exists():
        raise FileNotFoundError(path)

    with path.open(
        newline="",
        encoding="utf-8-sig",
    ) as f:

        reader = csv.DictReader(f)

        if not set(FIELDS).issubset(
            reader.fieldnames or []
        ):
            raise ValueError(
                "CSV 필수 열이 누락되었습니다."
            )

        approved = [
            row for row in reader
            if row["decision"]
            .strip()
            .upper() == "APPROVE"
        ]

    if not approved:
        print(
            "[NO OP] 승인된 항목이 없습니다."
        )
        return

    with SessionLocal() as session:

        courses, skills, _, mapped = (
            load_db(session)
        )

        course_by_id = {
            int(c["resource_id"]): c
            for c in courses
            if c["department"]
            not in EXCLUDED_DEPARTMENTS
        }

        skill_by_id = {
            int(s.skill_id): s
            for s in skills
        }

        pending = []
        seen = set()

        # 모든 승인 행을 먼저 검증
        for row in approved:

            course_id = int(
                row["course_resource_id"]
            )

            skill_id = int(
                row["skill_id"]
            )

            course = course_by_id.get(
                course_id
            )

            skill = skill_by_id.get(
                skill_id
            )

            if course is None or skill is None:
                raise ValueError(
                    f"2026 교과목/Skill 없음: "
                    f"{course_id}, {skill_id}"
                )

            if (
                course["course_name"]
                != row["course_name"]
                or course["department"]
                != row["department"]
            ):
                raise ValueError(
                    f"교과목 정보 변경: {course_id}"
                )

            if skill.name != row["skill_name"]:
                raise ValueError(
                    f"Skill 정보 변경: {skill_id}"
                )

            note = row[
                "review_note"
            ].strip()

            if len(note) < 8:
                raise ValueError(
                    f"검토 사유 최소 8자 필요: "
                    f"{course_id}, {skill_id}"
                )

            key = (
                course_id,
                skill_id,
            )

            if key in seen:
                raise ValueError(
                    f"CSV 내부 중복: {key}"
                )

            seen.add(key)

            if key in mapped:
                continue

            pending.append(
                (
                    course_id,
                    skill_id,
                    note,
                )
            )

        print(
            f"[REVIEWED] "
            f"승인={len(approved)}, "
            f"신규 RELATED={len(pending)}"
        )

        for course_id, skill_id, note in pending[:10]:
            print(
                f"  COURSE={course_id} "
                f"SKILL={skill_id}: "
                f"{note[:45]}"
            )

        # 학생 보유 Skill로 자동 전파되지 않는
        # RELATED 관계만 저장
        for course_id, skill_id, note in pending:

            session.add(
                ResourceSkill(
                    resource_id=course_id,
                    skill_id=skill_id,
                    relation_type="RELATED",
                    evidence_text=(
                        "교과목명 기반 수동 관련성 검토"
                        "(학습성과 미확인): "
                        f"{note}"
                    ),
                    mapping_method="HUMAN_TITLE_REVIEW",
                    confidence=Decimal("0.55"),
                    verified=False,
                )
            )

        session.commit()

    print(
        "[COMMIT] RELATED 연결 저장 완료"
    )

    print(
        "TAUGHT 및 StudentSkill은 "
        "변경하지 않았습니다."
    )


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--csv",
        type=Path,
        default=REVIEW_FILE,
    )

    parser.add_argument(
        "--apply-reviewed",
        action="store_true",
    )

    args = parser.parse_args()

    if args.apply_reviewed:
        apply_reviewed(args.csv)
    else:
        prepare(args.csv)


if __name__ == "__main__":
    main()
