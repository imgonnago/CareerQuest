
from __future__ import annotations

import argparse
import csv
import json
import re

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text

from src.db.database import engine


# 메디컬캠퍼스 검토 대상 18학과
TARGET_DEPARTMENTS = (
    "의학과",
    "간호학과",
    "작업치료학과",
    "병원경영학과",
    "안경광학과",
    "임상병리학과",
    "방사선학과",
    "치위생학과",
    "물리치료학과",
    "응급구조학과",
    "의료공간디자인학과",
    "제약생명공학과",
    "데이터의학과",
    "의료인공지능학과",
    "인공지능학과",
    "의료IT공학과",
    "의공학과",
    "의료신소재학과",
)


# 추가 Skill 검토 후보
# 기존 Skill과 중복될 수 있으므로 자동 등록 금지
# 실제 수집된 자료에서 근거를 찾은 경우만 보고
TOPIC_CANDIDATES = (
    (
        "간호", "지역사회 건강관리",
        r"지역사회\s*간호|지역사회\s*보건|지역사회\s*건강",
    ),
    (
        "간호", "정신건강 간호",
        r"정신\s*간호|정신건강\s*간호",
    ),
    (
        "간호", "아동 간호",
        r"아동\s*간호|소아\s*간호",
    ),
    (
        "간호", "모성 간호",
        r"모성\s*간호|여성건강\s*간호",
    ),
    (
        "간호", "노인 간호",
        r"노인\s*간호|노인건강\s*간호",
    ),
    (
        "재활", "소아 재활평가",
        r"소아\s*(?:작업|물리)치료|소아\s*재활",
    ),
    (
        "재활", "노인 재활",
        r"노인\s*(?:작업|물리)치료|노인\s*재활",
    ),
    (
        "재활", "보행 보조기 처방 지원",
        r"보행\s*보조기|보조공학",
    ),
    (
        "임상검사", "심전도 및 생리기능검사",
        r"심전도|생리기능검사|심폐기능검사",
    ),
    (
        "임상검사", "수혈검사",
        r"수혈검사|혈액은행",
    ),
    (
        "임상검사", "조직병리검사",
        r"조직\s*병리|병리\s*조직",
    ),
    (
        "방사선", "핵의학 영상검사",
        r"핵의학|PET[/ -]?CT",
    ),
    (
        "방사선", "방사선치료 계획",
        r"방사선\s*치료|치료계획\s*시스템",
    ),
    (
        "안경광학", "안저 영상검사",
        r"안저\s*(?:영상|검사)|망막\s*영상",
    ),
    (
        "치위생", "구강 역학조사",
        r"구강\s*역학|구강보건\s*조사",
    ),
    (
        "병원행정", "의무기록 질 관리",
        r"의무\s*기록\s*질|의무기록\s*관리",
    ),
    (
        "병원행정", "의료서비스 마케팅",
        r"병원\s*마케팅|의료서비스\s*마케팅",
    ),
    (
        "제약바이오", "의약품 제형 설계",
        r"제제\s*공학|제형\s*설계|제제학",
    ),
    (
        "제약바이오", "단백질 정제",
        r"단백질\s*정제|바이오의약품\s*정제",
    ),
    (
        "제약바이오", "임상시험 운영관리",
        r"임상시험\s*관리|임상시험\s*운영|GCP",
    ),
    (
        "의공학", "의료기기 소프트웨어 검증",
        r"IEC\s*62304|의료기기\s*(?:SW|소프트웨어)\s*검증",
    ),
    (
        "의료IT", "디지털 헬스케어 UX 설계",
        r"헬스케어\s*UX|의료\s*UX|디지털\s*헬스\s*UX",
    ),
    (
        "의료공간", "감염관리형 의료공간 설계",
        r"감염관리\s*(?:시설|공간)|음압\s*병실",
    ),
)


def normalize(value):
    return re.sub(
        r"\s+", "", value or ""
    ).casefold()


def write_csv(path, fields, rows):
    with path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"[CSV] {path} | {len(rows)}행")


def inspect(year, outdir):

    outdir.mkdir(
        parents=True, exist_ok=True
    )

    with engine.connect() as conn:

        resources = conn.execute(text("""
            SELECT resource_type, COUNT(*) AS n
            FROM resource
            GROUP BY resource_type
            ORDER BY resource_type
        """)).all()

        courses = conn.execute(text("""
            SELECT
                d.name AS department,
                cd.curriculum_year,
                cd.course_code,
                cd.year_level,
                cd.semester,
                r.resource_id,
                r.name AS course_name,
                EXISTS (
                    SELECT 1
                    FROM resource_skill rs
                    WHERE rs.resource_id = r.resource_id
                ) AS mapped
            FROM course_detail cd
            JOIN resource r
                ON r.resource_id = cd.resource_id
            LEFT JOIN department d
                ON d.department_id = cd.department_id
            WHERE r.resource_type = 'COURSE'
            ORDER BY
                d.name,
                cd.curriculum_year DESC,
                r.name
        """)).mappings().all()

        skills = conn.execute(text("""
            SELECT
                s.skill_id,
                s.name,
                s.category,
                s.is_active,
                COUNT(DISTINCT rs.resource_id)
                    FILTER (
                        WHERE r.resource_type='COURSE'
                    ) AS course_n,
                COUNT(DISTINCT rs.resource_id)
                    FILTER (
                        WHERE r.resource_type='JOB'
                    ) AS job_n,
                COUNT(DISTINCT rs.resource_id)
                    FILTER (
                        WHERE r.resource_type='CERTIFICATE'
                    ) AS certificate_n,
                COUNT(DISTINCT rs.resource_id)
                    FILTER (
                        WHERE r.resource_type='PROGRAM'
                    ) AS program_n
            FROM skill s
            LEFT JOIN resource_skill rs
                ON rs.skill_id=s.skill_id
            LEFT JOIN resource r
                ON r.resource_id=rs.resource_id
            GROUP BY
                s.skill_id, s.name,
                s.category, s.is_active
            ORDER BY s.skill_id
        """)).mappings().all()

        aliases = conn.execute(text("""
            SELECT alias FROM skill_alias
        """)).scalars().all()

        jobs = conn.execute(text("""
            SELECT
                jd.parse_status,
                COUNT(*) AS total,
                COUNT(*) FILTER (
                    WHERE length(
                        trim(coalesce(jd.requirements, ''))
                    ) >= 40
                ) AS with_requirements,
                COUNT(*) FILTER (
                    WHERE r.is_active
                ) AS active
            FROM job_detail jd
            JOIN resource r
                ON r.resource_id=jd.resource_id
            GROUP BY jd.parse_status
            ORDER BY total DESC
        """)).mappings().all()

        parsed_jobs = conn.execute(text("""
            SELECT
                r.resource_id,
                r.name,
                jd.requirements,
                jd.responsibilities
            FROM job_detail jd
            JOIN resource r
                ON r.resource_id=jd.resource_id
            WHERE jd.parse_status='PARSED'
              AND (
                  jd.requirements IS NOT NULL
                  OR jd.responsibilities IS NOT NULL
              )
            ORDER BY r.resource_id
        """)).mappings().all()

        source_health = conn.execute(text("""
            SELECT
                sd.source_name,
                COUNT(*) AS total,
                COUNT(*) FILTER (
                    WHERE sd.resource_id IS NULL
                ) AS unlinked,
                COUNT(*) FILTER (
                    WHERE sd.evidence_eligible
                ) AS eligible,
                COUNT(*) FILTER (
                    WHERE r.resource_type='PROGRAM'
                    AND (
                        length(
                            trim(coalesce(sd.content, ''))
                        ) < 80
                        OR trim(coalesce(sd.content, ''))
                           = trim(coalesce(sd.title, ''))
                    )
                ) AS weak_program_body
            FROM source_document sd
            LEFT JOIN resource r
                ON r.resource_id=sd.resource_id
            GROUP BY sd.source_name
            ORDER BY total DESC, sd.source_name
        """)).mappings().all()

        expired_jobs = conn.execute(text("""
            SELECT COUNT(*)
            FROM job_detail jd
            JOIN resource r
                ON r.resource_id=jd.resource_id
            WHERE r.is_active
              AND jd.deadline IS NOT NULL
              AND jd.deadline < NOW()
        """)).scalar_one()

        cert_waiting = conn.execute(text("""
            SELECT COUNT(*)
            FROM source_document
            WHERE source_name='QNET_CATALOG_CSV_20251231'
              AND resource_id IS NULL
        """)).scalar_one()

    # ------------------------------------------------
    # 1. 학과별 COURSE 및 Skill 연결 확인
    # ------------------------------------------------

    department_lookup = defaultdict(list)

    for row in courses:
        department_lookup[
            normalize(row["department"])
        ].append(row)

    department_report = []
    course_review = []

    for department in TARGET_DEPARTMENTS:

        all_rows = department_lookup[
            normalize(department)
        ]

        current = [
            row for row in all_rows
            if row["curriculum_year"] == year
        ]

        unmapped = [
            row for row in current
            if not row["mapped"]
        ]

        coverage = (
            round(
                100 * (
                    len(current) - len(unmapped)
                ) / len(current),
                1,
            )
            if current else 0
        )

        department_report.append({
            "department": department,
            "any_year": len(all_rows),
            f"courses_{year}": len(current),
            "mapped_courses": (
                len(current) - len(unmapped)
            ),
            "unmapped_courses": len(unmapped),
            "coverage_pct": coverage,
            "status": (
                "NO_OFFICIAL_COURSES_OR_NAME_MISMATCH"
                if not current
                else (
                    "SKILL_MAPPING_PENDING"
                    if unmapped else "MAPPED"
                )
            ),
            "sample_unmapped": " / ".join(
                str(row["course_name"])
                for row in unmapped[:8]
            ),
        })

        for row in current:

            course_review.append({
                "department": department,
                "resource_id": row["resource_id"],
                "course_code": row["course_code"] or "",
                "year_level": row["year_level"] or "",
                "semester": row["semester"] or "",
                "course_name": row["course_name"],
                "mapped": bool(row["mapped"]),
                "action": (
                    "REVIEW_SKILL_MAPPING"
                    if not row["mapped"]
                    else "KEEP_AND_VALIDATE"
                ),
            })

    write_csv(
        outdir / "01_department_coverage.csv",
        [
            "department",
            "any_year",
            f"courses_{year}",
            "mapped_courses",
            "unmapped_courses",
            "coverage_pct",
            "status",
            "sample_unmapped",
        ],
        department_report,
    )

    write_csv(
        outdir / "02_course_skill_review.csv",
        [
            "department", "resource_id",
            "course_code", "year_level",
            "semester", "course_name",
            "mapped", "action",
        ],
        course_review,
    )

    # ------------------------------------------------
    # 2. 기존 Skill 사전 및 데이터 품질
    # ------------------------------------------------

    write_csv(
        outdir / "03_skill_inventory.csv",
        [
            "skill_id", "name", "category",
            "is_active", "course_n",
            "job_n", "certificate_n",
            "program_n",
        ],
        [dict(row) for row in skills],
    )

    write_csv(
        outdir / "04_source_quality.csv",
        [
            "source_name", "total",
            "unlinked", "eligible",
            "weak_program_body",
        ],
        [dict(row) for row in source_health],
    )

    write_csv(
        outdir / "05_job_parse_quality.csv",
        [
            "parse_status", "total",
            "with_requirements", "active",
        ],
        [dict(row) for row in jobs],
    )

    # ------------------------------------------------
    # 3. 신규 Skill 검토 후보
    # ------------------------------------------------

    known_names = {
        normalize(row["name"])
        for row in skills
    }

    known_names.update(
        normalize(alias) for alias in aliases
    )

    grounded = []

    for category, candidate, pattern in TOPIC_CANDIDATES:

        regex = re.compile(
            pattern, re.IGNORECASE
        )

        course_hits = [
            row for row in course_review
            if not row["mapped"]
            and regex.search(
                str(row["course_name"])
            )
        ]

        job_hits = [
            row for row in parsed_jobs
            if regex.search(
                (row["requirements"] or "")[:5000]
                + " "
                + (row["responsibilities"] or "")[:5000]
            )
        ]

        if not course_hits and not job_hits:
            continue

        grounded.append({
            "category": category,
            "candidate_skill": candidate,
            "exact_name_in_skill_or_alias": (
                normalize(candidate) in known_names
            ),
            "unmapped_courses": len(course_hits),
            "parsed_jobs": len(job_hits),
            "sample_course": " / ".join(
                str(row["course_name"])
                for row in course_hits[:4]
            ),
            "sample_course_resource_ids": " / ".join(
                str(row["resource_id"])
                for row in course_hits[:4]
            ),
            "sample_job_resource_ids": " / ".join(
                str(row["resource_id"])
                for row in job_hits[:4]
            ),
            "review_status": (
                "REVIEW_DUPLICATE_AND_SCOPE"
            ),
        })

    grounded.sort(
        key=lambda row: (
            -row["unmapped_courses"],
            -row["parsed_jobs"],
            row["candidate_skill"],
        )
    )

    write_csv(
        outdir / "06_skill_candidate_evidence.csv",
        [
            "category",
            "candidate_skill",
            "exact_name_in_skill_or_alias",
            "unmapped_courses",
            "parsed_jobs",
            "sample_course",
            "sample_course_resource_ids",
            "sample_job_resource_ids",
            "review_status",
        ],
        grounded,
    )

    # ------------------------------------------------
    # 4. 전체 요약
    # ------------------------------------------------

    summary = {
        "as_of": datetime.now(
            ZoneInfo("Asia/Seoul")
        ).isoformat(timespec="seconds"),
        "curriculum_year": year,
        "resources": dict(resources),
        "skill_count": len(skills),
        "target_departments": len(TARGET_DEPARTMENTS),
        "departments_without_year_courses": [
            row["department"]
            for row in department_report
            if not row[f"courses_{year}"]
        ],
        "target_courses": len(course_review),
        "unmapped_courses": sum(
            not row["mapped"]
            for row in course_review
        ),
        "job_status": [
            dict(row) for row in jobs
        ],
        "expired_but_active_jobs": expired_jobs,
        "qnet_catalog_waiting_code": cert_waiting,
        "program_weak_docs": sum(
            row["weak_program_body"]
            for row in source_health
        ),
        "grounded_candidate_count": len(grounded),
        "notes": [
            "DB changes: none",
            "Skill candidates are not approved skills",
            "Course name matches do not prove proficiency",
            "Unmatched department names need review",
            "Existing mappings require quality verification",
        ],
    }

    path = outdir / "00_summary.json"

    path.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print("\n[SUMMARY]")
    print("Skill:", summary["skill_count"])
    print("2026 COURSE:", summary["target_courses"])
    print("미연결 COURSE:", summary["unmapped_courses"])

    print(
        "교과목 0건 또는 학과명 불일치:",
        ", ".join(
            summary["departments_without_year_courses"]
        ) or "없음",
    )

    print(
        "마감 지났지만 활성 JOB:",
        expired_jobs,
    )

    print(
        "본문 부족 PROGRAM SourceDocument:",
        summary["program_weak_docs"],
    )

    print(
        "Q-Net 종목코드 확인 대기:",
        cert_waiting,
    )

    print(
        "신규 Skill 검토 후보:",
        len(grounded),
    )

    print(
        "\nDB 변경 없음. 보고서 위치:",
        outdir,
    )


def main():

    parser = argparse.ArgumentParser(
        description=(
            "CareerQuest DB 품질 및 "
            "Skill 확장 근거 검토"
        )
    )

    parser.add_argument(
        "--year",
        type=int,
        default=2026,
    )

    parser.add_argument(
        "--out",
        type=Path,
        default=Path(
            "data/reports/skill_readiness"
        ),
    )

    args = parser.parse_args()

    if not 2020 <= args.year <= 2100:
        parser.error(
            "교육과정 연도 범위를 확인하세요."
        )

    inspect(
        args.year,
        args.out,
    )


if __name__ == "__main__":
    main()
