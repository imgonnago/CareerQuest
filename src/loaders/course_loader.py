from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    CourseDetail,
    Department,
    Resource,
    SourceDocument,
)
from src.loaders.common import (
    normalize_url,
    sha256_text,
    upsert_source_document,
    utc_now,
)


SOURCE_NAME = "KONYANG_CURRICULUM_PDF"
ORGANIZATION = "건양대학교"


def clean_string(value) -> str | None:
    if value is None:
        return None

    value = re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()

    return value or None


def normalize_name(value: str) -> str:
    return re.sub(
        r"[\s·ㆍ・\-_()/]",
        "",
        value or "",
    ).lower()


def parse_int(value) -> int | None:
    value = clean_string(value)

    if not value:
        return None

    match = re.search(
        r"-?\d+",
        value,
    )

    if not match:
        return None

    try:
        return int(
            match.group()
        )

    except ValueError:
        return None


def parse_decimal(
    value,
) -> Decimal | None:

    value = clean_string(
        value
    )

    if not value:
        return None

    match = re.search(
        r"\d+(?:\.\d+)?",
        value,
    )

    if not match:
        return None

    try:
        return Decimal(
            match.group()
        )

    except InvalidOperation:
        return None


def resolve_department(
    session: Session,
    department_name: str,
) -> Department:
    """
    DB의 Department에서 학과를 찾는다.

    1. 정확히 같은 이름
    2. 공백/기호를 제거했을 때 같은 이름
    """

    department_name = (
        clean_string(
            department_name
        )
    )

    if not department_name:
        raise ValueError(
            "department_name이 비어 있습니다."
        )

    exact = list(
        session.scalars(
            select(
                Department
            )
            .where(
                Department.name
                == department_name
            )
            .order_by(
                Department.is_active.desc(),
                Department.survey_year
                .desc()
                .nullslast(),
            )
        ).all()
    )

    if exact:
        return exact[0]

    wanted = normalize_name(
        department_name
    )

    departments = list(
        session.scalars(
            select(
                Department
            ).order_by(
                Department.is_active.desc(),
                Department.survey_year
                .desc()
                .nullslast(),
            )
        ).all()
    )

    matches = [
        department
        for department
        in departments
        if normalize_name(
            department.name
        ) == wanted
    ]

    if matches:
        return matches[0]

    similar = [
        department.name
        for department
        in departments
        if (
            wanted
            in normalize_name(
                department.name
            )
            or normalize_name(
                department.name
            )
            in wanted
        )
    ]

    hint = (
        f" 비슷한 학과: "
        f"{', '.join(similar[:10])}"
        if similar
        else ""
    )

    raise LookupError(
        f"DB에서 학과를 찾지 못했습니다: "
        f"{department_name}.{hint}"
    )


def build_course_dedup_key(
    *,
    department_id: int,
    curriculum_year: int,
    course_code: str | None,
    course_name: str,
    year_level: int | None,
    semester: str | None,
) -> str:

    course_code = clean_string(
        course_code
    )

    if course_code:

        identity = (
            f"{department_id}|"
            f"{curriculum_year}|"
            f"{course_code.upper()}"
        )

    else:

        identity = (
            f"{department_id}|"
            f"{curriculum_year}|"
            f"{normalize_name(course_name)}|"
            f"{year_level or ''}|"
            f"{normalize_name(semester or '')}"
        )

    digest = sha256_text(
        identity
    )

    if not digest:
        raise ValueError(
            "COURSE dedup hash 생성 실패"
        )

    return (
        f"COURSE:{digest}"
    )


def build_course_source_key(
    *,
    department_id: int,
    curriculum_year: int,
    course_code: str | None,
    course_name: str,
    year_level: int | None,
    semester: str | None,
) -> str:

    course_code = clean_string(
        course_code
    )

    if course_code:

        identity = (
            course_code.upper()
        )

    else:

        identity = (
            f"{normalize_name(course_name)}|"
            f"{year_level or ''}|"
            f"{normalize_name(semester or '')}"
        )

    digest = sha256_text(
        identity
    )

    if not digest:
        raise ValueError(
            "COURSE SourceDocument hash 생성 실패"
        )

    return (
        f"CURRICULUM_COURSE:"
        f"{department_id}:"
        f"{curriculum_year}:"
        f"{digest}"
    )


def decimal_to_json(
    value,
):
    if value is None:
        return None

    if isinstance(
        value,
        Decimal,
    ):
        return str(
            value
        )

    return value


def upsert_curriculum_document(
    session: Session,
    *,
    department_name: str,
    curriculum_year: int,
    content: str,
    source_file_name: str,
    source_url: str | None = None,
    revision_type: str = "BASE",
    raw_metadata: dict | None = None,
) -> SourceDocument:
    """
    교육과정 PDF 전체 원문을
    SourceDocument 1건으로 저장한다.

    특정 COURSE 하나의 문서가 아니므로
    resource_id=None.
    """

    department = (
        resolve_department(
            session,
            department_name,
        )
    )

    revision_type = (
        clean_string(
            revision_type
        )
        or "BASE"
    ).upper()

    metadata = dict(
        raw_metadata
        or {}
    )

    metadata.update(
        {
            "department_id": (
                department.department_id
            ),

            "department_name": (
                department.name
            ),

            "curriculum_year": (
                curriculum_year
            ),

            "revision_type": (
                revision_type
            ),

            "source_file_name": (
                source_file_name
            ),
        }
    )

    title = (
        f"{curriculum_year}학년도 "
        f"{department.name} 교육과정"
    )

    if (
        revision_type
        == "PARTIAL_REVISION"
    ):
        title += (
            " (일부 개정)"
        )

    elif (
        revision_type
        == "REVISION"
    ):
        title += (
            " (개정)"
        )

    return (
        upsert_source_document(
            session,

            resource_id=None,

            source_name=(
                SOURCE_NAME
            ),

            source_type=(
                "UNIVERSITY_WEB"
                if source_url
                else "MANUAL"
            ),

            document_type=(
                "SYLLABUS"
            ),

            source_record_key=(
                f"CURRICULUM_DOC:"
                f"{department.department_id}:"
                f"{curriculum_year}:"
                f"{revision_type}"
            ),

            source_url=(
                source_url
            ),

            title=(
                title
            ),

            content=(
                content
            ),

            raw_metadata=(
                metadata
            ),

            published_at=None,
            expires_at=None,

            discovery_channel=(
                "LOCAL_OFFICIAL_PDF"
            ),

            discovery_query=None,

            http_status=None,

            fetch_status=(
                "SUCCESS"
            ),

            validation_status=(
                "ACCEPTED"
            ),

            trust_level="A",

            evidence_eligible=True,
        )
    )


def upsert_course_record(
    session: Session,
    record: dict,
    *,
    parent_source_document_id: (
        int | None
    ) = None,
) -> dict:
    """
    표준화된 교과목 dict 1건을

    Resource(COURSE)
    + CourseDetail
    + SourceDocument

    로 적재한다.
    """

    department_name = (
        clean_string(
            record.get(
                "department_name"
            )
            or record.get(
                "department"
            )
        )
    )

    course_name = (
        clean_string(
            record.get(
                "name"
            )
            or record.get(
                "course_name"
            )
        )
    )

    curriculum_year = (
        parse_int(
            record.get(
                "curriculum_year"
            )
        )
    )

    if not department_name:
        raise ValueError(
            "department_name이 없습니다."
        )

    if not course_name:
        raise ValueError(
            "course name이 없습니다."
        )

    if not curriculum_year:
        raise ValueError(
            "curriculum_year가 없습니다."
        )

    department = (
        resolve_department(
            session,
            department_name,
        )
    )

    course_code = (
        clean_string(
            record.get(
                "course_code"
            )
        )
    )

    # PDF_GLYPH_SGD_2026
    # 확인된 PDF 글리프 오류만 교과목명에서 보정
    if course_name:
        course_name = re.sub(
            r"SGD\s*柴\s*(?=지역사회문제해결)",
            "SGD·",
            course_name,
        )

        # 앞서 공식 원본과 대조한 방사선학과 3건
        if (
            department_name == "방사선학과"
            and curriculum_year == 2026
        ):
            verified_fixes = {
                ("50953B", "치과掊유방영상학"):
                    "치과·유방영상학",
                ("50966B", "상掊하지영상진단학 및 실습"):
                    "상·하지영상진단학 및 실습",
                ("50967B", "흉掊복부掊척추영상진단학 및 실습"):
                    "흉·복부·척추영상진단학 및 실습",
            }

            course_name = verified_fixes.get(
                (course_code, course_name),
                course_name,
            )

    year_level = (
        parse_int(
            record.get(
                "year_level"
            )
        )
    )

    semester = (
        clean_string(
            record.get(
                "semester"
            )
        )
    )

    credits = (
        parse_decimal(
            record.get(
                "credits"
            )
        )
    )

    completion_type = (
        clean_string(
            record.get(
                "completion_type"
            )
        )
    )

    source_url = (
        clean_string(
            record.get(
                "source_url"
            )
        )
    )

    dedup_key = (
        build_course_dedup_key(
            department_id=(
                department.department_id
            ),

            curriculum_year=(
                curriculum_year
            ),

            course_code=(
                course_code
            ),

            course_name=(
                course_name
            ),

            year_level=(
                year_level
            ),

            semester=(
                semester
            ),
        )
    )

    resource = (
        session.scalar(
            select(
                Resource
            ).where(
                Resource.dedup_key
                == dedup_key
            )
        )
    )

    created = (
        resource is None
    )

    # --------------------------------------------------------
    # Resource
    # --------------------------------------------------------

    if resource is None:

        resource = (
            Resource(
                resource_type=(
                    "COURSE"
                ),

                dedup_key=(
                    dedup_key
                ),

                name=(
                    course_name
                ),

                organization=(
                    f"{ORGANIZATION} "
                    f"{department.name}"
                ),

                description=(
                    clean_string(
                        record.get(
                            "description"
                        )
                    )
                ),

                canonical_url=(
                    normalize_url(
                        source_url
                    )
                    if source_url
                    else None
                ),

                is_active=True,
            )
        )

        session.add(
            resource
        )

        session.flush()

    else:

        resource.name = (
            course_name
        )

        resource.organization = (
            f"{ORGANIZATION} "
            f"{department.name}"
        )

        description = (
            clean_string(
                record.get(
                    "description"
                )
            )
        )

        if description:

            resource.description = (
                description
            )

        if source_url:

            resource.canonical_url = (
                normalize_url(
                    source_url
                )
                or source_url
            )

        resource.last_seen_at = (
            utc_now()
        )

        resource.is_active = True

    # --------------------------------------------------------
    # CourseDetail
    # --------------------------------------------------------

    detail = (
        session.get(
            CourseDetail,
            resource.resource_id,
        )
    )

    if detail is None:

        detail = (
            CourseDetail(
                resource_id=(
                    resource.resource_id
                ),
            )
        )

        session.add(
            detail
        )

    detail.department_id = (
        department.department_id
    )

    detail.course_code = (
        course_code
    )

    detail.credits = (
        credits
    )

    detail.year_level = (
        year_level
    )

    detail.semester = (
        semester
    )

    detail.completion_type = (
        completion_type
    )

    detail.curriculum_year = (
        curriculum_year
    )

    detail.syllabus_url = (
        normalize_url(
            source_url
        )
        if source_url
        else None
    )

    detail_metadata = dict(
        record.get(
            "raw_metadata"
        )
        or {}
    )

    theory_hours = (
        record.get(
            "theory_hours"
        )
    )

    practice_hours = (
        record.get(
            "practice_hours"
        )
    )

    detail_metadata.update(
        {
            "parent_source_document_id": (
                parent_source_document_id
            ),

            "source_file_name": (
                clean_string(
                    record.get(
                        "source_file_name"
                    )
                )
            ),

            "source_page": (
                record.get(
                    "source_page"
                )
            ),

            "source_table": (
                record.get(
                    "source_table"
                )
            ),

            "source_row": (
                record.get(
                    "source_row"
                )
            ),

            "revision_type": (
                clean_string(
                    record.get(
                        "revision_type"
                    )
                )
                or "BASE"
            ),

            "theory_hours": (
                decimal_to_json(
                    theory_hours
                )
            ),

            "practice_hours": (
                decimal_to_json(
                    practice_hours
                )
            ),
        }
    )

    detail.raw_metadata = (
        detail_metadata
    )

    # --------------------------------------------------------
    # Course SourceDocument
    # --------------------------------------------------------

    source_text = (
        clean_string(
            record.get(
                "source_text"
            )
        )
        or course_name
    )

    course_document = (
        upsert_source_document(
            session,

            resource_id=(
                resource.resource_id
            ),

            source_name=(
                SOURCE_NAME
            ),

            source_type=(
                "UNIVERSITY_WEB"
                if source_url
                else "MANUAL"
            ),

            document_type=(
                "SYLLABUS"
            ),

            source_record_key=(
                build_course_source_key(
                    department_id=(
                        department.department_id
                    ),

                    curriculum_year=(
                        curriculum_year
                    ),

                    course_code=(
                        course_code
                    ),

                    course_name=(
                        course_name
                    ),

                    year_level=(
                        year_level
                    ),

                    semester=(
                        semester
                    ),
                )
            ),

            source_url=(
                source_url
            ),

            title=(
                f"{curriculum_year} "
                f"{department.name} - "
                f"{course_name}"
            ),

            content=(
                source_text
            ),

            raw_metadata={
                **detail_metadata,

                "course_code": (
                    course_code
                ),

                "course_name": (
                    course_name
                ),

                "year_level": (
                    year_level
                ),

                "semester": (
                    semester
                ),

                "credits": (
                    str(
                        credits
                    )
                    if credits
                    is not None
                    else None
                ),

                "completion_type": (
                    completion_type
                ),
            },

            published_at=None,
            expires_at=None,

            discovery_channel=(
                "CURRICULUM_PDF"
            ),

            discovery_query=None,

            http_status=None,

            fetch_status=(
                "SUCCESS"
            ),

            validation_status=(
                "ACCEPTED"
            ),

            trust_level="A",

            evidence_eligible=True,
        )
    )

    session.flush()

    return {
        "created": (
            created
        ),

        "resource_id": (
            resource.resource_id
        ),

        "course_detail_id": (
            resource.resource_id
        ),

        "source_document_id": (
            course_document
            .source_document_id
        ),

        "department_id": (
            department.department_id
        ),
    }