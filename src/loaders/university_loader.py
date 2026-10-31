from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.database import engine
from src.db.models import (
    Department,
    University,
)

from src.loaders.common import (
    read_jsonl,
    sha256_text,
    upsert_source_document,
)


load_dotenv()


# ============================================================
# 설정
# ============================================================

RAW_DIR = Path(
    "data/raw/university"
)

UNIVERSITY_API_URL = os.getenv(
    "DATA_GO_UNIV_DEPT_URL"
)

SOURCE_NAME = (
    "KCUE_UNIVERSITY_DEPARTMENT_API"
)


# ============================================================
# 공통 변환 함수
# ============================================================

def clean_string(
    value,
) -> str | None:

    if value is None:
        return None

    value = str(
        value
    ).strip()

    if not value:
        return None

    return value


def parse_int(
    value,
) -> int | None:

    value = clean_string(
        value
    )

    if value is None:
        return None

    # "20명" 같은 값이 혹시 들어오더라도
    # 숫자만 뽑을 수 있게 처리
    digits = "".join(
        char
        for char in value
        if char.isdigit()
        or char == "-"
    )

    if not digits:
        return None

    try:
        return int(
            digits
        )

    except ValueError:
        return None


def parse_datetime(
    value,
) -> datetime | None:
    """
    대학 API에서 나올 수 있는
    날짜 형식을 최대한 안전하게 처리.
    """

    value = clean_string(
        value
    )

    if value is None:
        return None

    formats = [
        "%Y-%m-%d",
        "%Y%m%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y%m%d%H%M%S",
    ]

    for fmt in formats:

        try:
            result = datetime.strptime(
                value,
                fmt,
            )

            return result.replace(
                tzinfo=timezone.utc
            )

        except ValueError:
            continue

    return None


def pick(
    record: dict,
    *keys: str,
):
    """
    API 필드명이 조금 달라도
    첫 번째로 존재하는 값을 사용.
    """

    for key in keys:

        value = record.get(
            key
        )

        if (
            value is not None
            and str(value).strip()
        ):
            return value

    return None


# ============================================================
# RAW 파일 탐색
# ============================================================

def find_latest_university_file() -> Path:

    files = sorted(
        RAW_DIR.glob(
            "university_departments_*.jsonl"
        ),
        reverse=True,
    )

    if not files:

        raise FileNotFoundError(
            "university_departments_*.jsonl "
            "파일을 찾을 수 없습니다."
        )

    return files[0]


# ============================================================
# 대학 상태 / 지역
# ============================================================

def build_region(
    record: dict,
) -> str | None:
    """
    API에서 확인된
    mjrAreaNm / mjrAreaSignguNm은
    전공 계열이 아니라 지역 정보로 사용.
    """

    city = clean_string(
        record.get(
            "mjrAreaNm"
        )
    )

    district = clean_string(
        record.get(
            "mjrAreaSignguNm"
        )
    )

    parts = [
        value
        for value in (
            city,
            district,
        )
        if value
    ]

    if not parts:
        return None

    return " ".join(
        parts
    )


def determine_department_active(
    record: dict,
) -> bool:
    """
    학과 상태 필드가 '폐과' 등인 경우 비활성화.
    원본 문자열은 raw_metadata에 그대로 보존.
    """

    status = clean_string(
        record.get(
            "schlMjrStatNm"
        )
    )

    if status is None:
        return True

    inactive_words = (
        "폐과",
        "폐지",
        "미운영",
    )

    return not any(
        word in status
        for word in inactive_words
    )


# ============================================================
# University
# ============================================================

def get_or_create_university(
    session: Session,
    record: dict,
) -> University:

    university_name = clean_string(
        pick(
            record,
            "schlKrnNm",
            "schlNm",
        )
    )

    if not university_name:

        raise ValueError(
            "학교명이 없는 대학 데이터입니다."
        )

    # 실제 API에 학교 ID가 있으면 우선 사용
    school_id = clean_string(
        pick(
            record,
            "schlId",
            "schoolId",
            "univId",
        )
    )

    campus_name = clean_string(
        pick(
            record,
            "campusNm",
            "campusName",
        )
    )

    university = None

    # --------------------------------------------
    # 1순위: 공식 school_id
    # --------------------------------------------

    if school_id:

        university = session.scalar(
            select(
                University
            ).where(
                University.school_id
                == school_id
            )
        )

    # --------------------------------------------
    # 2순위: 학교명 + 캠퍼스명
    # --------------------------------------------

    if university is None:

        query = select(
            University
        ).where(
            University.name
            == university_name
        )

        if campus_name:

            query = query.where(
                University.campus_name
                == campus_name
            )

        university = session.scalar(
            query
        )

    # --------------------------------------------
    # 신규 생성
    # --------------------------------------------

    if university is None:

        university = University(
            school_id=school_id,
            name=university_name,
            campus_name=campus_name,
            region=build_region(
                record
            ),
            is_active=True,
        )

        session.add(
            university
        )

        session.flush()

    else:

        # 없는 정보은 최신 데이터로 보완
        if (
            university.school_id is None
            and school_id
        ):
            university.school_id = (
                school_id
            )

        if (
            university.campus_name is None
            and campus_name
        ):
            university.campus_name = (
                campus_name
            )

        region = build_region(
            record
        )

        if region:
            university.region = (
                region
            )

        university.is_active = True

    return university


# ============================================================
# Department
# ============================================================

def find_existing_department(
    session: Session,
    *,
    university_id: int,
    kedi_major_id: str | None,
    department_name: str,
    college_name: str | None,
) -> Department | None:

    # --------------------------------------------
    # KEDI 학과 ID가 있으면 가장 신뢰할 수 있음
    # --------------------------------------------

    if kedi_major_id:

        department = session.scalar(
            select(
                Department
            ).where(
                Department.university_id
                == university_id,

                Department.kedi_major_id
                == kedi_major_id,
            )
        )

        if department:
            return department

    # --------------------------------------------
    # KEDI ID가 없을 때 fallback
    # --------------------------------------------

    query = select(
        Department
    ).where(
        Department.university_id
        == university_id,

        Department.name
        == department_name,
    )

    if college_name:

        query = query.where(
            Department.college_name
            == college_name
        )

    return session.scalar(
        query
    )


def upsert_department(
    session: Session,
    university: University,
    record: dict,
) -> Department:

    department_name = clean_string(
        record.get(
            "korMjrNm"
        )
    )

    if not department_name:

        raise ValueError(
            "korMjrNm(학과명)이 없는 데이터입니다."
        )

    kedi_major_id = clean_string(
        record.get(
            "kediMjrId"
        )
    )

    standard_major_id = clean_string(
        pick(
            record,
            "stdCltfMjrId",
            "stdClsfMjrId",
        )
    )

    college_name = clean_string(
        record.get(
            "clgNm"
        )
    )

    department = (
        find_existing_department(
            session,

            university_id=(
                university.university_id
            ),

            kedi_major_id=(
                kedi_major_id
            ),

            department_name=(
                department_name
            ),

            college_name=(
                college_name
            ),
        )
    )

    # --------------------------------------------
    # 실제 전공 계열
    #
    # API에서:
    # mjrAreaNm = 대전광역시 → 지역
    # onsfSrsClftNm = 공학계열 → 계열
    # --------------------------------------------

    major_area_name = clean_string(
        record.get(
            "onsfSrsClftNm"
        )
    )

    major_area_code = clean_string(
        pick(
            record,
            "onsfSrsClftCd",
            "onsfSrsClftCode",
        )
    )

    survey_year = parse_int(
        record.get(
            "svyYr"
        )
    )

    # 학과 자체 수정일을 우선 사용
    source_updated_at = (
        parse_datetime(
            record.get(
                "mjrUpdtDtm"
            )
        )
        or parse_datetime(
            record.get(
                "lstUpdtDtm"
            )
        )
    )

    active = (
        determine_department_active(
            record
        )
    )

    # --------------------------------------------
    # 신규
    # --------------------------------------------

    if department is None:

        department = Department(
            university_id=(
                university.university_id
            ),

            kedi_major_id=(
                kedi_major_id
            ),

            standard_major_id=(
                standard_major_id
            ),

            name=(
                department_name
            ),

            college_name=(
                college_name
            ),

            degree_type=(
                clean_string(
                    record.get(
                        "pbnfDgriCrseDivNm"
                    )
                )
            ),

            lesson_years=(
                clean_string(
                    record.get(
                        "lsnTrmNm"
                    )
                )
            ),

            department_characteristic=(
                clean_string(
                    record.get(
                        "schlMjrCharNm"
                    )
                )
            ),

            curriculum_description=(
                clean_string(
                    record.get(
                        "edcCrseLtrCntnt"
                    )
                )
            ),

            major_area_code=(
                major_area_code
            ),

            major_area_name=(
                major_area_name
            ),

            admission_quota=(
                parse_int(
                    record.get(
                        "eschlPscpNum"
                    )
                )
            ),

            survey_year=(
                survey_year
            ),

            source_updated_at=(
                source_updated_at
            ),

            raw_metadata=(
                record
            ),

            is_active=(
                active
            ),
        )

        session.add(
            department
        )

        session.flush()

        return department

    # --------------------------------------------
    # 기존 레코드 → 최신 snapshot 갱신
    # --------------------------------------------

    department.kedi_major_id = (
        kedi_major_id
        or department.kedi_major_id
    )

    department.standard_major_id = (
        standard_major_id
    )

    department.name = (
        department_name
    )

    department.college_name = (
        college_name
    )

    department.degree_type = (
        clean_string(
            record.get(
                "pbnfDgriCrseDivNm"
            )
        )
    )

    department.lesson_years = (
        clean_string(
            record.get(
                "lsnTrmNm"
            )
        )
    )

    department.department_characteristic = (
        clean_string(
            record.get(
                "schlMjrCharNm"
            )
        )
    )

    department.curriculum_description = (
        clean_string(
            record.get(
                "edcCrseLtrCntnt"
            )
        )
    )

    department.major_area_code = (
        major_area_code
    )

    department.major_area_name = (
        major_area_name
    )

    department.admission_quota = (
        parse_int(
            record.get(
                "eschlPscpNum"
            )
        )
    )

    department.survey_year = (
        survey_year
    )

    department.source_updated_at = (
        source_updated_at
    )

    department.raw_metadata = (
        record
    )

    department.is_active = (
        active
    )

    return department


# ============================================================
# RAG/원문용 텍스트 생성
# ============================================================

def build_department_content(
    record: dict,
) -> str:

    fields = [
        (
            "학교",
            pick(
                record,
                "schlKrnNm",
                "schlNm",
            ),
        ),
        (
            "단과대학",
            record.get(
                "clgNm"
            ),
        ),
        (
            "학과",
            record.get(
                "korMjrNm"
            ),
        ),
        (
            "학위과정",
            record.get(
                "pbnfDgriCrseDivNm"
            ),
        ),
        (
            "수업연한",
            record.get(
                "lsnTrmNm"
            ),
        ),
        (
            "계열",
            record.get(
                "onsfSrsClftNm"
            ),
        ),
        (
            "학과특성",
            record.get(
                "schlMjrCharNm"
            ),
        ),
        (
            "교육과정",
            record.get(
                "edcCrseLtrCntnt"
            ),
        ),
        (
            "진로 및 취업",
            record.get(
                "pwayEmplLtrCntnt"
            ),
        ),
        (
            "입학정원",
            record.get(
                "eschlPscpNum"
            ),
        ),
        (
            "조사년도",
            record.get(
                "svyYr"
            ),
        ),
    ]

    lines = []

    for label, value in fields:

        value = clean_string(
            value
        )

        if value:
            lines.append(
                f"{label}: {value}"
            )

    return "\n".join(
        lines
    )


# ============================================================
# SourceDocument Key
# ============================================================

def build_source_record_key(
    record: dict,
) -> str:

    university_name = clean_string(
        pick(
            record,
            "schlKrnNm",
            "schlNm",
        )
    ) or "UNKNOWN"

    kedi_major_id = clean_string(
        record.get(
            "kediMjrId"
        )
    )

    # KEDI ID가 있으면 가장 안정적인 키
    if kedi_major_id:

        return (
            f"DEPARTMENT:"
            f"{university_name}:"
            f"{kedi_major_id}"
        )

    # fallback
    raw_key = "|".join(
        [
            university_name,
            clean_string(
                record.get(
                    "clgNm"
                )
            )
            or "",
            clean_string(
                record.get(
                    "korMjrNm"
                )
            )
            or "",
        ]
    )

    fallback_hash = (
        sha256_text(
            raw_key
        )
    )

    return (
        f"DEPARTMENT:"
        f"{fallback_hash}"
    )


# ============================================================
# Loader
# ============================================================

def load_university_data(
    session: Session,
    path: Path,
) -> dict:

    records = read_jsonl(
        path
    )

    stats = {
        "records": 0,
        "universities_created": 0,
        "departments_created": 0,
        "documents": 0,
        "skipped": 0,
    }

    university_ids_before = set(
        session.scalars(
            select(
                University.university_id
            )
        ).all()
    )

    department_ids_before = set(
        session.scalars(
            select(
                Department.department_id
            )
        ).all()
    )

    for record in records:

        try:

            university = (
                get_or_create_university(
                    session,
                    record,
                )
            )

            department = (
                upsert_department(
                    session,
                    university,
                    record,
                )
            )

            # ----------------------------------------
            # SourceDocument
            # ----------------------------------------

            content = (
                build_department_content(
                    record
                )
            )

            upsert_source_document(
                session,

                # 대학/학과는 추천 Resource가 아니므로
                # resource_id를 연결하지 않는다.
                resource_id=None,

                source_name=(
                    SOURCE_NAME
                ),

                source_type=(
                    "PUBLIC_DATA"
                ),

                document_type=(
                    "API_RECORD"
                ),

                source_record_key=(
                    build_source_record_key(
                        record
                    )
                ),

                source_url=(
                    UNIVERSITY_API_URL
                ),

                title=(
                    f"{university.name} "
                    f"{department.name}"
                ),

                content=(
                    content
                ),

                raw_metadata=(
                    record
                ),

                published_at=None,

                http_status=200,

                fetch_status=(
                    "SUCCESS"
                ),

                validation_status=(
                    "ACCEPTED"
                ),

                trust_level="A",

                evidence_eligible=True,
            )

            stats[
                "documents"
            ] += 1

            stats[
                "records"
            ] += 1

        except Exception as error:

            stats[
                "skipped"
            ] += 1

            print()
            print(
                "[학과 데이터 건너뜀]"
            )

            print(
                record.get(
                    "korMjrNm"
                )
            )

            print(
                type(error).__name__,
                error,
            )

    session.flush()

    university_ids_after = set(
        session.scalars(
            select(
                University.university_id
            )
        ).all()
    )

    department_ids_after = set(
        session.scalars(
            select(
                Department.department_id
            )
        ).all()
    )

    stats[
        "universities_created"
    ] = len(
        university_ids_after
        - university_ids_before
    )

    stats[
        "departments_created"
    ] = len(
        department_ids_after
        - department_ids_before
    )

    return stats


# ============================================================
# Main
# ============================================================

def main() -> None:

    if not RAW_DIR.exists():

        raise FileNotFoundError(
            f"{RAW_DIR} "
            f"폴더가 없습니다."
        )

    raw_file = (
        find_latest_university_file()
    )

    print(
        "=" * 70
    )

    print(
        "CareerQuest University Loader"
    )

    print(
        "=" * 70
    )

    print()

    print(
        "RAW 파일:",
        raw_file.name,
    )

    with Session(
        engine
    ) as session:

        try:

            stats = (
                load_university_data(
                    session,
                    raw_file,
                )
            )

            session.commit()

        except Exception:

            session.rollback()

            print()
            print(
                "오류 발생 → "
                "전체 Transaction rollback"
            )

            raise

    print()

    print(
        "=" * 70
    )

    print(
        "대학/학과 적재 완료"
    )

    print(
        "=" * 70
    )

    print(
        "처리 레코드:",
        stats["records"],
    )

    print(
        "신규 대학:",
        stats[
            "universities_created"
        ],
    )

    print(
        "신규 학과:",
        stats[
            "departments_created"
        ],
    )

    print(
        "SourceDocument 처리:",
        stats["documents"],
    )

    print(
        "건너뜀:",
        stats["skipped"],
    )


if __name__ == "__main__":
    main()