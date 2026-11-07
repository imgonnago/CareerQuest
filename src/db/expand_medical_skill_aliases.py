
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from src.db.database import SessionLocal
from src.db.models import Skill, SkillAlias
from src.db.seed_master import normalize_name


# 대표 Skill 이름 → 같은 의미로 사용할 수 있는 표현
# 넓은 관련어는 별칭에 넣지 않는다.
ALIASES = {
    "의약품 제형 설계": [
        "제형 설계",
        "의약품 제형설계",
    ],
    "정신건강 간호": [
        "정신간호",
        "정신건강간호",
    ],
    "여성건강 간호": [
        "여성건강간호",
        "모성간호",
    ],
    "아동 간호": [
        "아동간호",
        "소아 간호",
    ],
    "지역사회 간호": [
        "지역사회간호",
    ],
    "핵의학 영상검사": [
        "핵의학영상검사",
        "핵의학 영상 검사",
    ],
    "노인 재활": [
        "노인재활",
    ],

    "성인환자 간호": [
        "성인간호",
        "성인 간호",
    ],
    "간호관리 및 리더십": [
        "간호관리",
        "간호 리더십",
    ],
    "호스피스·완화간호": [
        "호스피스간호",
        "완화간호",
    ],
    "연하재활": [
        "연하 재활",
        "연하장애 재활",
    ],
    "정신사회적 작업치료": [
        "정신사회작업치료",
    ],
    "정형도수치료": [
        "정형 도수치료",
    ],
    "수중재활운동": [
        "수중 재활 운동",
        "수중재활",
    ],
    "심폐 재활중재": [
        "심폐재활",
        "심폐 재활",
    ],
    "스포츠 재활중재": [
        "스포츠재활",
        "스포츠 재활",
    ],
    "수혈검사": [
        "수혈 검사",
        "수혈 적합성 검사",
    ],
    "조직병리 검사": [
        "조직병리검사",
        "조직 병리 검사",
    ],
    "진단세포 검사": [
        "진단세포검사",
        "진단 세포 검사",
    ],
    "임상생리기능 검사": [
        "임상생리기능검사",
        "생리기능검사",
    ],
    "투시조영 영상검사": [
        "투시조영검사",
        "투시 조영 영상검사",
    ],
    "방사선치료 원리 이해": [
        "방사선 치료 원리",
        "방사선치료 기초",
    ],
    "치과방사선 검사 원리": [
        "치과 방사선 검사 원리",
    ],
    "심전도 판독 기초": [
        "심전도 판독",
        "기본 심전도 해석",
    ],
    "전문심장소생술 절차 이해": [
        "전문심장소생술 절차",
    ],
    "질병 및 의료행위 분류코딩": [
        "질병분류코딩",
        "의료행위 분류코딩",
    ],
    "암등록 데이터 관리": [
        "암등록",
        "암 등록 데이터 관리",
    ],
    "의무기록정보 질관리": [
        "의무기록 질 관리",
        "의무기록정보 질 관리",
    ],
    "생체신호 처리": [
        "생체신호처리",
        "생체 신호 처리",
    ],
    "IoT 시스템 제어": [
        "IoT시스템제어",
        "사물인터넷 시스템 제어",
    ],
    "QbD 기반 제약품질 설계": [
        "QbD",
        "의약품 품질고도화 설계",
    ],
    "GMP 제조공정 운영 기초": [
        "GMP 제조공정 운영",
    ],
    "치유환경 색채디자인": [
        "치유색채디자인",
        "치유 색채 디자인",
    ],
    "강화학습": [
        "Reinforcement Learning",
    ],
    "온디바이스 AI 개발": [
        "온디바이스AI",
        "On-device AI",
    ],
}


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    parser.add_argument(
        "--report",
        type=Path,
        default=Path(
            "data/reports/skill_mapping/"
            "medical_alias_review.csv"
        ),
    )

    args = parser.parse_args()

    with SessionLocal() as db:

        skills = db.scalars(
            select(Skill)
        ).all()

        existing_aliases = db.scalars(
            select(SkillAlias)
        ).all()

        skill_by_name = {
            normalize_name(s.name): s
            for s in skills
        }

        alias_by_name = {
            a.normalized_alias: a
            for a in existing_aliases
        }

        results = []
        additions = []
        planned = {}

        for skill_name, aliases in ALIASES.items():

            owner = skill_by_name.get(
                normalize_name(skill_name)
            )

            if owner is None:
                raise ValueError(
                    f"기존 Skill 없음: {skill_name}"
                )

            for alias in aliases:

                normalized = normalize_name(alias)

                canonical = skill_by_name.get(
                    normalized
                )

                existing = alias_by_name.get(
                    normalized
                )

                status = "ADD"

                if normalized == normalize_name(
                    skill_name
                ):
                    status = "SAME_NAME"

                elif canonical is not None:
                    status = "CONFLICT_SKILL"

                elif existing is not None:

                    status = (
                        "EXISTS"
                        if existing.skill_id
                        == owner.skill_id
                        else "CONFLICT_ALIAS"
                    )

                elif normalized in planned:

                    status = (
                        "DUPLICATE"
                        if planned[normalized]
                        == owner.skill_id
                        else "CONFLICT_PLANNED"
                    )

                elif (
                    not normalized
                    or len(normalized) < 3
                ):
                    status = "TOO_SHORT"

                if status == "ADD":

                    additions.append((
                        owner.skill_id,
                        alias,
                        normalized,
                    ))

                    planned[normalized] = (
                        owner.skill_id
                    )

                results.append({
                    "skill_id": owner.skill_id,
                    "skill": skill_name,
                    "alias": alias,
                    "normalized_alias": normalized,
                    "status": status,
                })

        counts = Counter(
            row["status"]
            for row in results
        )

        print(
            "[MODE]",
            "APPLY" if args.apply
            else "DRY RUN",
        )

        print(
            f"[SKILLS] {len(skills)}"
        )

        for status, count in sorted(
            counts.items()
        ):
            print(
                f"  {status}: {count}"
            )

        for row in results:

            if row["status"].startswith(
                "CONFLICT"
            ):
                print(
                    "[REVIEW]",
                    row["skill"],
                    "→",
                    row["alias"],
                    row["status"],
                )

        args.report.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with args.report.open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as file:

            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "skill_id",
                    "skill",
                    "alias",
                    "normalized_alias",
                    "status",
                ],
            )

            writer.writeheader()
            writer.writerows(results)

        print(
            "[REPORT]",
            args.report,
        )

        if not args.apply:

            print(
                "[DRY RUN] DB 변경 없음"
            )

            return

        # 충돌하는 이름은 추가하지 않고,
        # 명확히 새로운 별칭만 저장한다.
        for skill_id, alias, normalized in additions:

            db.add(
                SkillAlias(
                    skill_id=skill_id,
                    alias=alias,
                    normalized_alias=normalized,
                )
            )

        db.commit()

        print(
            f"[COMMIT] 신규 별칭 "
            f"{len(additions)}건"
        )

        print(
            "Skill ID와 ResourceSkill은 "
            "변경하지 않았습니다."
        )


if __name__ == "__main__":
    main()
