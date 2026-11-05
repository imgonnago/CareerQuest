
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from sqlalchemy import func, select

from src.collectors.curriculum.data_medicine_curriculum import (
    fetch_data_medicine_curriculum,
)
from src.collectors.curriculum.curriculum_pdf_resolver import (
    resolve_curriculum_pdf,
)
from src.collectors.curriculum.konyang_curriculum import (
    CurriculumSource,
    local_audit,
    parse_curriculum_pdf,
    validate_parser_result,
)
from src.collectors.curriculum.konyang_curriculum_index import (
    CENTRAL_CURRICULUM_URL,
    build_medical_curriculum_sources,
    fetch_central_curriculum_rows,
)
from src.collectors.curriculum.medical_departments import (
    MEDICAL_DEPARTMENTS,
    get_medical_department,
    normalize_department_name,
)
from src.db.database import SessionLocal
from src.db.models import (
    CourseDetail,
    Department,
    Resource,
    SourceDocument,
    University,
)
from src.loaders.course_loader import (
    SOURCE_NAME,
    build_course_dedup_key,
    build_course_source_key,
    normalize_name,
    resolve_department,
    upsert_course_record,
)


YEAR = 2026


@dataclass
class PreparedDepartment:
    department_name: str
    strategy: str
    records: list[dict]
    quality: str
    source_url: str
    skipped_uncoded: int = 0


def clean(value) -> str:
    return re.sub(
        r"\s+", " ", str(value or "")
    ).strip()


def check_source_url(url: str) -> None:
    parsed = urlsplit(url)

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
    ):
        raise ValueError(
            f"잘못된 교육과정 출처 URL: {url!r}"
        )


# ============================================================
# PDF 독립 Audit 검증
# ============================================================

def validate_pdf_result(
    courses: list[dict],
    validation: dict,
) -> None:
    problems = []

    required_counts = (
        "parser_course_count",
        "parser_code_count",
        "audit_code_count",
        "overlap_count",
    )

    counts = {
        key: int(validation.get(key) or 0)
        for key in required_counts
    }

    if (
        not courses
        or counts["parser_course_count"] != len(courses)
    ):
        problems.append(
            "Parser 교과목 수가 0이거나 검증 결과와 불일치"
        )

    if counts["audit_code_count"] <= 0:
        problems.append(
            "독립 PDF Audit의 교과목 코드가 0개"
        )

    if (
        counts["parser_code_count"]
        != counts["audit_code_count"]
    ):
        problems.append(
            "Parser와 Audit 코드 수 불일치"
        )

    if (
        counts["overlap_count"]
        != counts["audit_code_count"]
    ):
        problems.append(
            "Parser와 Audit 코드 집합 불일치"
        )

    for key in (
        "missing_in_parser",
        "parser_only",
        "duplicate_codes",
    ):
        if validation.get(key):
            problems.append(
                f"{key}: {validation[key]}"
            )

    if problems:
        raise ValueError(
            "PDF 품질 검증 실패: "
            + "; ".join(problems)
        )


# ============================================================
# 공통 COURSE 정규화
# ============================================================

def validate_and_enrich_records(
    department_name: str,
    year: int,
    strategy: str,
    source_url: str,
    courses: list[dict],
    source_page_url: str,
    source_method: str,
    skipped_uncoded: int = 0,
) -> PreparedDepartment:

    check_source_url(source_url)
    check_source_url(source_page_url)

    if not courses:
        raise ValueError(
            "교과목이 0개이므로 적재하지 않습니다."
        )

    records = []
    identities = set()
    credits_present = 0

    for idx, original in enumerate(courses, 1):

        if not isinstance(original, dict):
            raise ValueError(
                f"{idx}번째 교과목 자료가 dict가 아닙니다."
            )

        record = dict(original)

        name = clean(record.get("name"))
        code = (
            clean(record.get("course_code")).upper()
            or None
        )

        original_department = clean(
            record.get("department_name")
        )
        original_year = record.get("curriculum_year")

        if not name or len(name) > 500:
            raise ValueError(
                f"{idx}번째 교과목명이 없거나 너무 깁니다."
            )

        if original_department and (
            normalize_department_name(original_department)
            != normalize_department_name(department_name)
        ):
            raise ValueError(
                f"학과명이 다릅니다: {original_department}"
            )

        if (
            original_year is not None
            and str(original_year) != str(year)
        ):
            raise ValueError(
                f"{name}: 교육과정 연도 불일치 "
                f"({original_year})"
            )

        if strategy == "CENTRAL_PDF" and not code:
            raise ValueError(
                f"{name}: PDF 교과목 코드 누락 "
                "(사전 필터 오류)"
            )

        if code and len(code) > 100:
            raise ValueError(
                f"{name}: 교과목 코드가 너무 깁니다."
            )

        level = record.get("year_level")

        if level is not None:
            if (
                isinstance(level, bool)
                or not str(level).isdigit()
                or not 1 <= int(level) <= 6
            ):
                raise ValueError(
                    f"{name}: 잘못된 학년 {level!r}"
                )

            level = int(level)

        semester = (
            clean(record.get("semester"))
            or None
        )

        completion_type = (
            clean(record.get("completion_type"))
            or None
        )

        if semester and len(semester) > 50:
            raise ValueError(
                f"{name}: 학기 필드가 너무 깁니다."
            )

        if (
            completion_type
            and len(completion_type) > 100
        ):
            raise ValueError(
                f"{name}: 이수구분 필드가 너무 깁니다."
            )

        credit = record.get("credits")

        if credit is not None:
            try:
                numeric = Decimal(str(credit))
            except (
                InvalidOperation,
                TypeError,
                ValueError,
            ) as exc:
                raise ValueError(
                    f"{name}: 학점 형식 오류"
                ) from exc

            if (
                    not numeric.is_finite()
                    or not 0 <= numeric <= 30
                    or numeric.as_tuple().exponent < -1
            ):
                raise ValueError(
                    f"{name}: 비정상 학점 {credit!r}"
                )

            record["credits"] = str(numeric)
            credits_present += 1

        # 코드가 있다면 코드가 기본 식별자.
        # 없으면 과목명 + 학년 + 학기.
        identity = (
            (code,)
            if code
            else (
                normalize_name(name),
                level,
                semester,
            )
        )

        if identity in identities:
            raise ValueError(
                f"중복된 교과목 식별값: "
                f"{name} / {code}"
            )

        identities.add(identity)

        metadata = record.get("raw_metadata") or {}

        if not isinstance(metadata, dict):
            raise ValueError(
                f"{name}: raw_metadata 형식 오류"
            )

        metadata = dict(metadata)

        metadata.update({
            "source_strategy": strategy,
            "source_page_url": source_page_url,
            "source_resolver_method": source_method,
        })

        record.update({
            "department_name": department_name,
            "curriculum_year": year,
            "course_code": code,
            "name": name,
            "year_level": level,
            "semester": semester,
            "completion_type": completion_type,
            "source_url": source_url,
            "raw_metadata": metadata,
        })

        # 원본 source_text가 없을 때만
        # 확인된 정형 필드로 구성.
        if not clean(record.get("source_text")):

            parts = [
                f"{year}학년도",
                department_name,
            ]

            if code:
                parts.append(
                    f"교과목 코드: {code}"
                )

            parts.append(
                f"교과목명: {name}"
            )

            if level is not None:
                parts.append(
                    f"학년: {level}"
                )

            if semester:
                parts.append(
                    f"학기: {semester}"
                )

            if credit is not None:
                parts.append(
                    f"학점: {credit}"
                )

            if completion_type:
                parts.append(
                    f"이수구분: {completion_type}"
                )

            record["source_text"] = " | ".join(parts)

        # PostgreSQL JSONB 적재 가능 여부 확인.
        try:
            json.dumps(
                record,
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{name}: JSONB로 저장할 수 없는 "
                "필드가 있습니다."
            ) from exc

        records.append(record)

    grade = (
        "B"
        if (
            strategy == "HTML"
            or skipped_uncoded
            or credits_present < len(records)
        )
        else "A"
    )

    for record in records:
        record["raw_metadata"][
            "collection_quality_grade"
        ] = grade

    return PreparedDepartment(
        department_name,
        strategy,
        records,
        grade,
        source_url,
        skipped_uncoded,
    )


# ============================================================
# 공식 교육과정 실제 수집
# ============================================================

def collect_department(
    source,
    year: int,
) -> PreparedDepartment:

    name = source.department_name

    if not source.source_url:
        raise ValueError(
            "교육과정 source_url이 없습니다."
        )

    if source.strategy == "CENTRAL_PDF":

        if source.status != "READY_PDF":
            raise ValueError(
                "PDF 수집 대상 상태가 준비되지 않음: "
                f"{source.status}"
            )

        pdf = resolve_curriculum_pdf(
            department_name=name,
            year=year,
            central_url=source.source_url,
        )

        curriculum_source = CurriculumSource(
            page_url=pdf.page_url,
            pdf_url=pdf.pdf_url,
            file_name=(
                f"konyang_{year}_{name}_curriculum.pdf"
            ),
            department_name=name,
            curriculum_year=year,
        )

        audit = local_audit(pdf.data)

        courses, _diagnostics = parse_curriculum_pdf(
            pdf.data,
            curriculum_source,
            audit,
        )

        validation = validate_parser_result(
            courses,
            audit,
            curriculum_source,
        )

        validate_pdf_result(
            courses,
            validation,
        )

        # PDF에 코드 없는 행이 섞인 경우
        # Audit를 통과한 코드 있는 행만 적재.
        skipped_uncoded = sum(
            not clean(row.get("course_code"))
            for row in courses
        )

        coded_courses = [
            row
            for row in courses
            if clean(row.get("course_code"))
        ]

        return validate_and_enrich_records(
            name,
            year,
            "CENTRAL_PDF",
            pdf.pdf_url,
            coded_courses,
            pdf.page_url,
            pdf.method,
            skipped_uncoded,
        )

    if source.strategy == "HTML":

        if (
            name != "데이터의학과"
            or source.status != "READY_HTML"
        ):
            raise ValueError(
                "HTML 수집 대상 상태 확인 필요: "
                f"{source.status}"
            )

        parsed = fetch_data_medicine_curriculum(
            target_year=year
        )

        return validate_and_enrich_records(
            name,
            year,
            "HTML",
            parsed.source_url,
            parsed.courses,
            parsed.source_url,
            "OFFICIAL_HTML",
        )

    raise ValueError(
        "지원되지 않는 source strategy: "
        f"{source.strategy}"
    )


# ============================================================
# Department 안전 매핑
# ============================================================

def select_department(
    session,
    department_name: str,
) -> Department:

    registry = get_medical_department(
        department_name
    )

    if registry is None:
        raise ValueError(
            "메디컬캠퍼스 registry에 없는 학과: "
            f"{department_name}"
        )

    allowed = {
        normalize_department_name(x)
        for x in registry.all_names
    }

    active = session.scalars(
        select(Department).where(
            Department.is_active.is_(True)
        )
    ).all()

    matched = []

    for department in active:

        if (
            normalize_department_name(department.name)
            not in allowed
        ):
            continue

        university = session.get(
            University,
            department.university_id,
        )

        if (
            university is None
            or "건양대학교" not in clean(university.name)
        ):
            continue

        if (
            normalize_department_name(
                department.college_name or ""
            )
            != normalize_department_name(
                registry.college
            )
        ):
            continue

        matched.append(department)

    if len(matched) != 1:
        raise ValueError(
            "Department 매핑 불명확: "
            f"{department_name} "
            "(활성+건양대+단과대학 일치 "
            f"{len(matched)}건)"
        )

    # 기존 로더가 선택하는 ID와 일치하는지도 확인.
    chosen = resolve_department(
        session,
        registry.name,
    )

    if (
        chosen.department_id
        != matched[0].department_id
    ):
        raise ValueError(
            "기존 course_loader의 Department "
            f"선택 불일치: {department_name} "
            f"(확인={matched[0].department_id}, "
            f"로더={chosen.department_id})"
        )

    return matched[0]


# ============================================================
# 기존 dedup/source key 재사용
# ============================================================

def identities(
    prepared: PreparedDepartment,
    department_id: int,
    year: int,
):

    rows = []

    for record in prepared.records:

        values = dict(
            department_id=department_id,
            curriculum_year=year,
            course_code=record.get("course_code"),
            course_name=record["name"],
            year_level=record.get("year_level"),
            semester=record.get("semester"),
        )

        rows.append((
            record,
            build_course_dedup_key(**values),
            build_course_source_key(**values),
        ))

    keys = [
        key
        for _record, key, _source_key in rows
    ]

    source_keys = [
        key
        for _record, _key, key in rows
    ]

    if (
        len(set(keys)) != len(rows)
        or len(set(source_keys)) != len(rows)
    ):
        raise ValueError(
            "COURSE/SourceDocument "
            "중복 해시가 발견되었습니다."
        )

    return rows


# ============================================================
# DB Preflight - 데이터 변경 없음
# ============================================================

def preflight(
    session,
    prepared: PreparedDepartment,
    year: int,
):

    department = select_department(
        session,
        prepared.department_name,
    )

    department_id = department.department_id

    rows = identities(
        prepared,
        department_id,
        year,
    )

    keys = [
        key
        for _record, key, _source in rows
    ]

    by_key = {
        row.dedup_key: row
        for row in session.scalars(
            select(Resource).where(
                Resource.dedup_key.in_(keys)
            )
        ).all()
    }

    found_ids = [
        resource.resource_id
        for resource in by_key.values()
    ]

    details = (
        {
            row.resource_id: row
            for row in session.scalars(
                select(CourseDetail).where(
                    CourseDetail.resource_id.in_(
                        found_ids
                    )
                )
            ).all()
        }
        if found_ids
        else {}
    )

    # 동일 dedup_key의 다른 종류 Resource
    # 또는 학과/학년도 불일치 여부 검증.
    for record, key, _source_key in rows:

        resource = by_key.get(key)

        if resource is None:
            continue

        detail = details.get(resource.resource_id)

        if (
            resource.resource_type != "COURSE"
            or detail is None
        ):
            raise ValueError(
                "기존 COURSE Resource/Detail 불일치: "
                f"{record['name']}"
            )

        if (
            detail.department_id != department_id
            or detail.curriculum_year != year
        ):
            raise ValueError(
                "기존 COURSE의 학과/학년도 "
                f"연결이 다릅니다: {record['name']}"
            )

        incoming_code = record.get("course_code")

        old_code = (
            clean(detail.course_code).upper()
            or None
        )

        if incoming_code != old_code:
            raise ValueError(
                "기존 COURSE 코드와 불일치: "
                f"{record['name']}"
            )

    # 기존 COURSE에 같은 코드가
    # 다른 Resource ID로 등록됐는지 확인.
    codes = [
        rec["course_code"]
        for rec, _, _ in rows
        if rec["course_code"]
    ]

    if codes:

        existing_coded = session.scalars(
            select(CourseDetail).where(
                CourseDetail.department_id
                == department_id,

                CourseDetail.curriculum_year
                == year,

                func.upper(
                    CourseDetail.course_code
                ).in_(codes),
            )
        ).all()

        key_by_code = {
            record["course_code"]: key
            for record, key, _ in rows
            if record["course_code"]
        }

        for detail in existing_coded:

            actual = by_key.get(
                key_by_code[
                    clean(detail.course_code).upper()
                ]
            )

            if (
                actual is None
                or detail.resource_id
                != actual.resource_id
            ):
                raise ValueError(
                    "기존 COURSE 교과목 코드 충돌: "
                    f"{detail.course_code}"
                )

    # 데이터의학과처럼 코드가 없는 과목:
    # 과목명/학년/학기로 기존 중복 검사.
    html_records = [
        record
        for record, _, _ in rows
        if not record["course_code"]
    ]

    if html_records:

        existing_uncoded = session.execute(
            select(
                CourseDetail,
                Resource,
            )
            .join(
                Resource,
                Resource.resource_id
                == CourseDetail.resource_id,
            )
            .where(
                CourseDetail.department_id
                == department_id,

                CourseDetail.curriculum_year
                == year,

                CourseDetail.course_code.is_(None),
            )
        ).all()

        for record, key, _ in rows:

            if record["course_code"]:
                continue

            for detail, resource in existing_uncoded:

                same_name = (
                    normalize_name(resource.name)
                    == normalize_name(record["name"])
                )

                same_level = (
                    detail.year_level
                    == record["year_level"]
                )

                same_semester = (
                    clean(detail.semester)
                    .replace("학기", "")
                    == clean(record["semester"])
                    .replace("학기", "")
                )

                if (
                    same_name
                    and same_level
                    and same_semester
                    and resource.dedup_key != key
                ):
                    raise ValueError(
                        "기존 코드 없는 COURSE 중복 우려: "
                        f"{record['name']}"
                    )

    # 기존 SourceDocument 연결의 충돌 검사.
    source_keys = [
        source
        for _record, _key, source in rows
    ]

    documents = session.scalars(
        select(SourceDocument).where(
            SourceDocument.source_name
            == SOURCE_NAME,

            SourceDocument.source_record_key
            .in_(source_keys),
        )
    ).all()

    resource_id_by_source = {
        source_key: by_key[key].resource_id
        for _record, key, source_key in rows
        if key in by_key
    }

    for document in documents:

        expected_id = resource_id_by_source.get(
            document.source_record_key
        )

        if (
            expected_id is None
            or document.resource_id != expected_id
        ):
            raise ValueError(
                "기존 SourceDocument 연결 충돌: "
                f"{document.source_record_key}"
            )

    created = len(rows) - len(by_key)

    return (
        department_id,
        rows,
        created,
        len(by_key),
    )


# ============================================================
# DB 실제 적재 결과 검증
# ============================================================

def verify(
    session,
    department_id: int,
    year: int,
    rows,
) -> tuple[int, int, int]:

    keys = [
        key
        for _record, key, _source in rows
    ]

    linked = session.execute(
        select(
            Resource.dedup_key,
            Resource.resource_id,
            CourseDetail.department_id,
            CourseDetail.curriculum_year,
        )
        .join(
            CourseDetail,
            CourseDetail.resource_id
            == Resource.resource_id,
        )
        .where(
            Resource.dedup_key.in_(keys)
        )
    ).all()

    resource_by_key = {
        key: rid
        for key, rid, _dep, _year in linked
    }

    good = sum(
        1
        for key, rid, dep_id, cur_year in linked
        if (
            dep_id == department_id
            and cur_year == year
            and key in keys
        )
    )

    if good != len(rows):
        raise RuntimeError(
            "적재 검증 실패: "
            f"기대 {len(rows)}, "
            f"연결된 COURSE {good}"
        )

    total = session.scalar(
        select(func.count())
        .select_from(CourseDetail)
        .where(
            CourseDetail.department_id
            == department_id,

            CourseDetail.curriculum_year
            == year,
        )
    ) or 0

    doc_keys = [
        src
        for _record, _key, src in rows
    ]

    documents = session.execute(
        select(
            SourceDocument.source_record_key,
            SourceDocument.resource_id,
        )
        .where(
            SourceDocument.source_name
            == SOURCE_NAME,

            SourceDocument.source_record_key
            .in_(doc_keys),
        )
    ).all()

    expected_by_source = {
        src: resource_by_key.get(key)
        for _rec, key, src in rows
    }

    if (
        len(documents) != len(rows)
        or any(
            rid is None
            or rid != expected_by_source.get(src)
            for src, rid in documents
        )
    ):
        raise RuntimeError(
            "SourceDocument 연결 검증 실패: "
            f"기대 {len(rows)}, "
            f"확인 {len(documents)}"
        )

    return (
        good,
        total,
        len(documents),
    )


# ============================================================
# Dry Run / 실제 APPLY
# ============================================================

def process(
    prepared: PreparedDepartment,
    year: int,
    apply: bool,
) -> dict:

    # 먼저 읽기 전용 검증.
    with SessionLocal() as session:

        (
            department_id,
            rows,
            created,
            updated,
        ) = preflight(
            session,
            prepared,
            year,
        )

        session.rollback()

    if not apply:

        with SessionLocal() as session:

            current_count = session.scalar(
                select(func.count())
                .select_from(CourseDetail)
                .where(
                    CourseDetail.department_id
                    == department_id,

                    CourseDetail.curriculum_year
                    == year,
                )
            ) or 0

        return {
            "status": "DRY_RUN",
            "source_count": len(rows),
            "created": created,
            "updated": updated,
            "matched": None,
            "db_total": current_count,
            "documents": None,
        }

    # 학과별 독립 트랜잭션.
    # 예외 발생 시 해당 학과는 rollback.
    with SessionLocal() as session:

        with session.begin():

            (
                checked_id,
                checked_rows,
                created,
                updated,
            ) = preflight(
                session,
                prepared,
                year,
            )

            if checked_id != department_id:
                raise RuntimeError(
                    "Department ID가 실행 사이 "
                    "변경되었습니다."
                )

            for record, _key, _source_key in checked_rows:

                loaded = upsert_course_record(
                    session,
                    record,
                )

                doc = session.get(
                    SourceDocument,
                    loaded["source_document_id"],
                )

                if doc is None:
                    raise RuntimeError(
                        "SourceDocument가 "
                        "생성되지 않았습니다."
                    )

                doc.trust_level = prepared.quality

            session.flush()

            # 커밋 직전 검증.
            verify(
                session,
                department_id,
                year,
                checked_rows,
            )

    # 커밋 이후 별도 세션으로 재검증.
    with SessionLocal() as session:

        (
            matched,
            db_total,
            documents,
        ) = verify(
            session,
            department_id,
            year,
            rows,
        )

    return {
        "status": "APPLIED",
        "source_count": len(rows),
        "created": created,
        "updated": updated,
        "matched": matched,
        "db_total": db_total,
        "documents": documents,
    }


# ============================================================
# CLI
# ============================================================

def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "CareerQuest 2026 "
            "메디컬캠퍼스 COURSE 적재"
        )
    )

    parser.add_argument(
        "--year",
        type=int,
        default=YEAR,
    )

    parser.add_argument(
        "--only",
        help=(
            '일부 학과만 실행: '
            '"인공지능학과,데이터의학과"'
        ),
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 DB에 저장 (기본값: Dry-run)",
    )

    args = parser.parse_args()

    if args.year != YEAR:
        parser.error(
            "현재 검증된 교육과정 연도는 "
            "2026만 지원합니다."
        )

    if args.delay < 0:
        parser.error(
            "--delay는 0 이상이어야 합니다."
        )

    selected = None

    if args.only:

        selected = set()

        for name in args.only.split(","):

            item = get_medical_department(
                name.strip()
            )

            if item is None:
                parser.error(
                    "알 수 없는 메디컬캠퍼스 학과: "
                    f"{name!r}"
                )

            selected.add(item.name)

    official = {
        item.name
        for item in MEDICAL_DEPARTMENTS
    }

    central_rows = fetch_central_curriculum_rows(
        target_year=args.year
    )

    sources = build_medical_curriculum_sources(
        target_year=args.year,
        central_rows=central_rows,
    )

    source_names = [
        source.department_name
        for source in sources
    ]

    if (
        len(source_names) != len(official)
        or set(source_names) != official
    ):
        raise RuntimeError(
            "중앙 교육과정 Source 목록이 "
            "18학과 registry와 불일치합니다."
        )

    source_by_name = {
        source.department_name: source
        for source in sources
    }

    targets = [
        source_by_name[department.name]
        for department in MEDICAL_DEPARTMENTS
        if (
            selected is None
            or department.name in selected
        )
    ]

    print(
        f"[CareerQuest] {args.year} COURSE / "
        f"{'APPLY' if args.apply else 'DRY-RUN'}"
    )

    print(
        f"대상={len(targets)}학과 | "
        "학과별 트랜잭션 | 삭제/DDL 없음"
    )

    results = []

    for index, source in enumerate(targets, 1):

        name = source.department_name

        # 데이터의학과 HTML은 공식 자료이지만
        # 2026학년도 확정 교육과정은 아님.
        # Dry Run은 허용하고 실제 적재만 보류.
        if name == "데이터의학과":

            print(
                "  [출처주의] 데이터의학과 HTML은 "
                "학년도 미표기 교육과정 안내 자료입니다."
            )

            if args.apply:

                print(
                    f"[{index}/{len(targets)}] "
                    f"{name}: SKIPPED_PROVISIONAL "
                    "(2026 확정 교육과정 미확인)"
                )

                results.append((
                    name,
                    "SKIPPED_PROVISIONAL",
                    None,
                ))

                continue

        if source.strategy == "REVIEW":

            print(
                f"[{index}/{len(targets)}] "
                f"{name}: SKIPPED_REVIEW "
                "(2026 학부 출처 미확보)"
            )

            results.append((
                name,
                "SKIPPED_REVIEW",
                None,
            ))

            continue

        print(
            f"[{index}/{len(targets)}] "
            f"{name}: 수집 및 검증 시작"
        )

        try:

            prepared = collect_department(
                source,
                args.year,
            )

            stats = process(
                prepared,
                args.year,
                args.apply,
            )

            print(
                f"  {stats['status']} "
                f"| 품질={prepared.quality} "
                f"| 적재대상={stats['source_count']} "
                f"| PDF코드없는행보류="
                f"{prepared.skipped_uncoded} "
                f"| 신규={stats['created']} "
                f"기존재적재={stats['updated']} "
                f"| DB해당학년도총계="
                f"{stats['db_total']} "
                f"| 검증일치={stats['matched']}"
            )

            results.append((
                name,
                stats["status"],
                stats,
            ))

        except Exception as exc:

            print(
                f"  FAILED "
                f"({type(exc).__name__}): {exc}"
            )

            results.append((
                name,
                "FAILED",
                None,
            ))

        if (
            source.strategy == "CENTRAL_PDF"
            and args.delay
        ):
            time.sleep(args.delay)

    print("\n[최종 결과]")

    for name, status, stats in results:

        count = (
            "-"
            if stats is None
            else stats["source_count"]
        )

        print(
            f"  {name:<13} "
            f"{status:<16} "
            f"courses={count}"
        )

    failed = sum(
        status == "FAILED"
        for _, status, _ in results
    )

    print(
        f"실패={failed} / 대상={len(targets)}"
    )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
