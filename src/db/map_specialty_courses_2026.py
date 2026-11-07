
from __future__ import annotations

import argparse
import csv
import re
import unicodedata

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
)


YEAR = 2026

REPORT_DIR = Path(
    "data/reports/skill_mapping"
)


# 학과 범위
# 데이터의학과 및 교육과정 미확인 학과는 포함하지 않음

GROUPS = {
    "MED": {"의학과"},
    "NUR": {"간호학과"},
    "OT": {"작업치료학과"},
    "HOSP": {"병원경영학과"},
    "OPT": {"안경광학과"},
    "LAB": {"임상병리학과"},
    "RAD": {"방사선학과"},
    "DENT": {"치위생학과"},
    "PT": {"물리치료학과"},
    "EMS": {"응급구조학과"},
    "SPACE": {"의료공간디자인학과"},
    "PHARMA": {"제약생명공학과"},
    "AI": {"인공지능학과"},
    "IT": {"의료IT공학과"},
    "BME": {"의공학과"},
    "MAT": {"의료신소재학과"},
}

GROUPS["BIO"] = (
    GROUPS["PHARMA"]
    | GROUPS["BME"]
    | GROUPS["MAT"]
)

GROUPS["ENG"] = (
    GROUPS["IT"]
    | GROUPS["BME"]
    | GROUPS["MAT"]
)

GROUPS["ALL"] = set().union(
    *GROUPS.values()
)


# 규칙 형식:
# GROUP;Skill 이름;정규화된 과목명 정규식
#
# 이 규칙은 주제 관련성만 인정한다.
# 학생의 실제 숙련도나 면허를 증명하지 않는다.

RULE_TEXT = r"""
ALL;환자안전 관리;환자안전과질향상|의료데이터기초와환자안전|^안전한임상실습입문$
ALL;감염예방 및 관리;병원감염관리|감염관리와간호|^감염관리학$
ALL;의료질 개선;의료의질관리|의료질관리세미나|환자안전과질향상
ALL;보건의료 법규 이해;보건의료관계법규|보건의료법규|응급의료관계법규|^의료관계법규$|^의료법규$|^보건법규$
ALL;기본심폐소생술;기본소생술및응급처치

MED;의료윤리;프로페셔널리즘.*의료윤리|프로페셔널리즘.*생명윤리|직업윤리와평생학습
MED;의료 의사소통;의사소통기술[ivx12345]|의료커뮤니케이션|narrativemedicine
MED;근거기반 보건의료;근거중심의학|ebm을위한
MED;임상의학 문헌평가;^의학연구iii.*근거중심의학$
MED;보건의료 법규 이해;프로페셔널리즘v.*의료법규

NUR;간호사정;건강사정및실습
NUR;간호중재 수행;기본간호학및실습
NUR;간호관리 및 리더십;간호관리학
NUR;정신건강 간호;정신간호학
NUR;여성건강 간호;여성건강간호학
NUR;아동 간호;아동간호학
NUR;지역사회 간호;지역사회간호학
NUR;성인환자 간호;성인간호학
NUR;호스피스·완화간호;호스피스간호학

OT;일상생활활동 평가;일상생활동작학
OT;인지재활 평가;^인지재활$
OT;감각통합 평가;감각처리장애와중재
OT;작업수행 분석;활동분석및실습
OT;신경계 재활 평가;신경계작업치료학
OT;근골격계 기능평가;근골격계작업치료학
OT;정신사회적 작업치료;정신사회작업치료학
OT;연하재활;^연하재활$
OT;노인 재활;노인작업치료학

HOSP;병원 원무관리;^원무관리[0-9ivx]*$
HOSP;병원 회계관리;병원회계학|^erp회계$|병원재무관리
HOSP;건강보험 수가 이해;건강보험이론및실무
HOSP;병원 서비스 운영분석;병원서비스운영관리
HOSP;의료기관 인증 준비;의료기관인증
HOSP;의료정보 개인정보보호;건강정보보호
HOSP;의료질 개선;의료의질관리
HOSP;보건의료 통계관리;^보건의료통계
HOSP;의무기록정보 질관리;의무기록정보질향상
HOSP;질병 및 의료행위 분류코딩;질병및의료행위분류
HOSP;암등록 데이터 관리;^암등록$

OPT;콘택트렌즈 피팅;콘택트렌즈학및실습|콘택트렌즈임상사례
OPT;안경렌즈 가공;누진렌즈가공|안경조제및가공
OPT;양안시 기능평가;시기능분석및시기능훈련
OPT;시기능 검사 결과 해석;시기능분석및시기능훈련
OPT;안광학 기기 운용;^광학기기학$

LAB;검사실 정도관리;^정도관리학$
LAB;임상화학 검사;임상화학및실습
LAB;혈액학 검사;임상혈액학|진단혈액학
LAB;임상미생물 검사;임상미생물학|임상세균학|임상진균학|진단바이러스학|병원미생물학
LAB;면역혈청 검사;면역혈청학
LAB;분자진단 검사;임상분자진단학
LAB;세포배양 실무;세포배양학
LAB;수혈검사;수혈의학
LAB;조직병리 검사;조직검사학
LAB;진단세포 검사;진단세포학
LAB;임상생리기능 검사;임상생리학및실습

RAD;영상해부학 해석;^영상해부학$
RAD;일반 방사선 영상획득;방사선영상기술학
RAD;CT 영상검사 이해;전산화단층영상학
RAD;MRI 영상검사 이해;자기공명영상학
RAD;초음파 영상 원리;초음파영상학
RAD;방사선 안전관리;방사선장해방어학|^방사선관리학$
RAD;방사선 선량 평가;^방사선계측학$
RAD;핵의학 영상검사;^핵의학기술학$
RAD;투시조영 영상검사;^투시조영영상학$
RAD;방사선치료 원리 이해;^방사선치료학$

DENT;치과 감염관리;^감염관리학$|^병원감염관리$
DENT;구강보건 교육;구강보건교육학
DENT;예방치과 관리;예방치과학
DENT;치주건강 관리;^치주학$
DENT;구강건강 상태평가;환자구강위생관리학
DENT;치과방사선 검사 원리;치과방사선학

PT;근골격계 기능평가;근골격계물리치료진단|^운동손상평가론$
PT;신경계 재활 평가;신경계물리치료진단
PT;운동치료 계획;기초운동치료학|응용운동치료학|기능성트레이닝
PT;보조기기 적용 평가;보조기의수족학
PT;일상생활활동 평가;^일상생활동작
PT;근거기반 보건의료;근거중심물리치료학
PT;정형도수치료;정형도수치료학
PT;수중재활운동;^수중재활운동$
PT;심폐 재활중재;심폐계물리치료중재
PT;스포츠 재활중재;스포츠물리치료학
PT;노인 재활;노인물리치료학

EMS;응급환자 초기평가;응급환자평가및실습
EMS;기도유지 및 환기 보조;전문기도관리및실습
EMS;외상환자 평가;전문외상응급처치학
EMS;응급환자 이송 및 고정;^구조와이송$
EMS;심전도 판독 기초;심전도개론및실습
EMS;전문심장소생술 절차 이해;전문심장구조학

SPACE;치유환경 색채디자인;치유색채디자인
SPACE;병원 공간기획;^의료공간계획론$
SPACE;실내건축 도면작성;^제도및표현기법$
SPACE;CAD 설계;^cad$
SPACE;BIM 모델링;^bim$

BIO;의료기기 규제요건 이해;의료기기인허가관리|생체재료인허가|의료기기규격입문|의료기기기술규격
BIO;신소재 물성평가;^재료물성학$
BIO;생체적합성 시험 이해;^생체적합성$
BIO;세포배양 실무;^세포배양학및실습$
BIO;GMP 제조공정 운영 기초;^바이오gmp$|^cgmp$
BIO;실험 분석기기 운용;^기기분석학$|^기기분석학및실습$|기기분석및데이터처리
BIO;생체신호 계측;생체신호계측공학

PHARMA;의약품 제형 설계;^제제공학$|^제제학[0-9ivx]*$|^제형실습$
PHARMA;GMP 제조공정 운영 기초;^cgmp$|^바이오gmp$

BME;생체신호 처리;^생체신호처리$
BME;디지털 의료영상 처리;의료영상학및영상처리

ENG;의료정보시스템 분석;^병원시스템분석$

IT;의료정보시스템 분석;^병원업무정보시스템$
IT;IoT 시스템 제어;^iot시스템제어$
IT;Software Engineering;^소프트웨어공학$

AI;강화학습;^강화학습$
AI;온디바이스 AI 개발;^온디바이스ai$
AI;Natural Language Processing;딥러닝자연어처리
AI;Computer Vision;딥러닝영상처리
"""


# 추가 확인이 필요한 과목.
# 자동 RELATED 등록 대상이 아님.

REVIEW_PATTERNS = (
    (
        r"^임상실습|현장실습|인턴실습",
        "실습 대상 업무를 제목만으로 확정할 수 없음",
    ),
    (
        r"캡스톤디자인|종합설계|^창의설계",
        "실제 적용 기술은 강의계획서 확인 필요",
    ),
    (
        r"^인공지능기초$|^인공지능의이해$|^인공지능개론$",
        "인공지능 개론으로 특정 기술 Skill을 확정하기 어려움",
    ),
    (
        r"^병리학$|^생리학$|^심리학$",
        "기초학문명으로 임상 수행 역량을 확정하기 어려움",
    ),
)


def norm(value: str) -> str:
    value = unicodedata.normalize(
        "NFKC",
        value or "",
    ).lower()

    return re.sub(
        r"[^0-9a-z가-힣]+",
        "",
        value,
    )


def load_rules():

    rules = []
    seen = set()

    for number, raw in enumerate(
        RULE_TEXT.splitlines(), 1
    ):

        line = raw.strip()

        if not line or line.startswith("#"):
            continue

        parts = line.split(";", 2)

        if len(parts) != 3:
            raise ValueError(
                f"규칙 형식 오류: {number}"
            )

        group, skill, pattern = (
            part.strip()
            for part in parts
        )

        if group not in GROUPS:
            raise ValueError(
                f"학과 그룹 오류: {group}"
            )

        key = (
            group,
            skill,
            pattern,
        )

        if key in seen:
            continue

        seen.add(key)

        rules.append((
            group,
            skill,
            re.compile(
                pattern,
                re.IGNORECASE,
            ),
        ))

    return rules


def write_csv(path, fields, rows):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()
        writer.writerows(rows)


def main():

    parser = argparse.ArgumentParser(
        description=(
            "CareerQuest 2026 전공별 "
            "RELATED 매핑"
        )
    )

    parser.add_argument(
        "--department",
        default="all",
        choices=[
            "all",
            *sorted(GROUPS["ALL"]),
        ],
    )

    parser.add_argument(
        "--apply-safe",
        action="store_true",
    )

    args = parser.parse_args()

    rules = load_rules()

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
                CourseDetail.curriculum_year == YEAR,
                Resource.resource_type == "COURSE",
                Department.name != "데이터의학과",
            )
        ).all()

        skills = {
            skill.name: skill
            for skill in db.scalars(
                select(Skill).where(
                    Skill.is_active.is_(True)
                )
            ).all()
        }

        missing_skills = sorted(
            {
                name
                for _, name, _ in rules
            } - set(skills)
        )

        if missing_skills:
            raise RuntimeError(
                "DB에 없는 Skill: "
                + ", ".join(missing_skills)
            )

        existing = set(
            db.execute(
                select(
                    ResourceSkill.resource_id,
                    ResourceSkill.skill_id,
                )
            ).all()
        )

        proposals = []
        seen = set()

        for resource_id, title, department in courses:

            if (
                args.department != "all"
                and department != args.department
            ):
                continue

            normalized_title = norm(title)

            for group, skill_name, pattern in rules:

                if department not in GROUPS[group]:
                    continue

                match = pattern.search(
                    normalized_title
                )

                if not match:
                    continue

                skill_id = int(
                    skills[skill_name].skill_id
                )

                pair = (
                    int(resource_id),
                    skill_id,
                )

                if (
                    pair in existing
                    or pair in seen
                ):
                    continue

                seen.add(pair)

                proposals.append({
                    "course_resource_id":
                        int(resource_id),
                    "department": department,
                    "course_name": title,
                    "skill_id": skill_id,
                    "skill_name": skill_name,
                    "matched_phrase":
                        match.group(0),
                    "rule_group": group,
                    "relation_type": "RELATED",
                    "verified": False,
                })

        proposals.sort(
            key=lambda row: (
                row["department"],
                row["course_name"],
                row["skill_name"],
            )
        )

        new_course_ids = {
            row["course_resource_id"]
            for row in proposals
        }

        existing_course_ids = {
            int(resource_id)
            for resource_id, _ in existing
        }

        unmapped = []

        for resource_id, title, department in courses:

            if (
                args.department != "all"
                and department != args.department
            ):
                continue

            if (
                int(resource_id) in existing_course_ids
                or int(resource_id) in new_course_ids
            ):
                continue

            normalized_title = norm(title)

            notes = [
                reason
                for pattern, reason in REVIEW_PATTERNS
                if re.search(
                    pattern,
                    normalized_title,
                )
            ]

            unmapped.append({
                "course_resource_id": resource_id,
                "department": department,
                "course_name": title,
                "status": (
                    "NEEDS_FURTHER_EVIDENCE"
                    if notes else "UNMAPPED"
                ),
                "note": " / ".join(notes),
            })

        out = REPORT_DIR / (
            f"batch3_specialty_"
            f"{args.department}_2026.csv"
        )

        pending_out = REPORT_DIR / (
            f"batch3_unmapped_"
            f"{args.department}_2026.csv"
        )

        write_csv(
            out,
            [
                "course_resource_id",
                "department",
                "course_name",
                "skill_id",
                "skill_name",
                "matched_phrase",
                "rule_group",
                "relation_type",
                "verified",
            ],
            proposals,
        )

        write_csv(
            pending_out,
            [
                "course_resource_id",
                "department",
                "course_name",
                "status",
                "note",
            ],
            unmapped,
        )

        print(
            "[MODE]",
            (
                "APPLY SAFE"
                if args.apply_safe
                else "DRY RUN"
            ),
        )

        print(
            f"[RULES] {len(rules)}"
            f" / [COURSES] {len(courses)}"
            f" / [SKILLS] {len(skills)}"
        )

        print(
            f"[NEW RELATED] "
            f"{len(proposals)}건 / "
            f"교과목 {len(new_course_ids)}개"
        )

        print(
            f"[STILL UNMAPPED] "
            f"{len(unmapped)}과목"
        )

        for department, count in sorted(
            Counter(
                row["department"]
                for row in proposals
            ).items()
        ):
            print(
                f"  {department}: {count}"
            )

        for row in proposals[:25]:
            print(
                f"  {row['department']} | "
                f"{row['course_name']} "
                f"→ {row['skill_name']}"
            )

        print(
            f"[REPORT] {out}"
        )
        print(
            f"[UNMAPPED] {pending_out}"
        )

        if not args.apply_safe:

            print(
                "[DRY RUN] DB 변경 없음"
            )

            return

        for row in proposals:

            db.add(
                ResourceSkill(
                    resource_id=(
                        row["course_resource_id"]
                    ),
                    skill_id=row["skill_id"],
                    relation_type="RELATED",

                    evidence_text=(
                        f"2026년 {row['department']} "
                        f"교과목 제목({row['course_name']})과 "
                        f"관련 Skill({row['skill_name']})의 "
                        "주제 관련성 확인. "
                        "강의계획서 및 숙련도 미확인."
                    ),

                    mapping_method=(
                        "CURRICULUM_TOPIC_RULE"
                    ),

                    confidence=Decimal("0.60"),
                    verified=False,
                )
            )

        db.commit()

        print(
            f"[COMMIT] RELATED "
            f"{len(proposals)}건 추가"
        )

        print(
            "TAUGHT / StudentSkill 변경 없음"
        )


if __name__ == "__main__":
    main()
