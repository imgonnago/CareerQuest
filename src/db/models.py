from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ============================================================
# Base
# ============================================================

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention=NAMING_CONVENTION
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ============================================================
# 1. UNIVERSITY
# ============================================================

class University(TimestampMixin, Base):
    __tablename__ = "university"

    university_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    # 공공데이터 API의 학교 식별값
    school_id: Mapped[str | None] = mapped_column(
        String(100),
        unique=True,
        nullable=True,
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
    )

    campus_name: Mapped[str | None] = mapped_column(
        String(200)
    )

    region: Mapped[str | None] = mapped_column(
        String(100)
    )

    official_url: Mapped[str | None] = mapped_column(
        Text
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )


# ============================================================
# 2. DEPARTMENT
# ============================================================

class Department(TimestampMixin, Base):
    __tablename__ = "department"

    department_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    university_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "university.university_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # 대학별 학과정보 API 기준
    kedi_major_id: Mapped[str | None] = mapped_column(
        String(100)
    )

    standard_major_id: Mapped[str | None] = mapped_column(
        String(100)
    )

    name: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        index=True,
    )

    college_name: Mapped[str | None] = mapped_column(
        String(300)
    )

    degree_type: Mapped[str | None] = mapped_column(
        String(100)
    )

    lesson_years: Mapped[str | None] = mapped_column(
        String(50)
    )

    department_characteristic: Mapped[str | None] = mapped_column(
        Text
    )

    curriculum_description: Mapped[str | None] = mapped_column(
        Text
    )

    major_area_code: Mapped[str | None] = mapped_column(
        String(100)
    )

    major_area_name: Mapped[str | None] = mapped_column(
        String(200)
    )

    admission_quota: Mapped[int | None] = mapped_column(
        Integer
    )

    survey_year: Mapped[int | None] = mapped_column(
        Integer,
        index=True,
    )

    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    raw_metadata: Mapped[dict | None] = mapped_column(
        JSONB
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    __table_args__ = (
        UniqueConstraint(
            "university_id",
            "kedi_major_id",
            name="uq_department_university_kedi",
        ),
    )


# ============================================================
# 3. STUDENT
# ============================================================

class Student(TimestampMixin, Base):
    __tablename__ = "student"

    student_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    # 실제 학번 대신 서비스 내부 사용자 ID 사용 가능
    external_user_id: Mapped[str | None] = mapped_column(
        String(200),
        unique=True,
    )

    university_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "university.university_id",
            ondelete="SET NULL",
        ),
        index=True,
    )

    department_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "department.department_id",
            ondelete="SET NULL",
        ),
        index=True,
    )

    school_year: Mapped[int | None] = mapped_column(
        SmallInteger
    )

    gpa: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2)
    )

    gpa_scale: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2)
    )

    graduation_year: Mapped[int | None] = mapped_column(
        Integer
    )

    # MVP에서는 대표 목표직무 1개
    target_job_role_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "job_role.job_role_id",
            ondelete="SET NULL",
        ),
        index=True,
    )

    interest_text: Mapped[str | None] = mapped_column(
        Text
    )

    __table_args__ = (
        CheckConstraint(
            "school_year IS NULL OR school_year >= 1",
            name="student_school_year_positive",
        ),
        CheckConstraint(
            "gpa IS NULL OR gpa >= 0",
            name="student_gpa_positive",
        ),
    )


# ============================================================
# 4. STUDENT EXPERIENCE
# ============================================================

class StudentExperience(TimestampMixin, Base):
    __tablename__ = "student_experience"

    experience_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    student_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "student.student_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    experience_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )

    organization: Mapped[str | None] = mapped_column(
        String(300)
    )

    description: Mapped[str | None] = mapped_column(
        Text
    )

    start_date: Mapped[date | None] = mapped_column(
        Date
    )

    end_date: Mapped[date | None] = mapped_column(
        Date
    )

    source_url: Mapped[str | None] = mapped_column(
        Text
    )

    raw_metadata: Mapped[dict | None] = mapped_column(
        JSONB
    )

    __table_args__ = (
        CheckConstraint(
            """
            experience_type IN (
                'PROJECT',
                'INTERNSHIP',
                'EXTRACURRICULAR',
                'CLUB',
                'RESEARCH',
                'AWARD',
                'WORK',
                'OTHER'
            )
            """,
            name="student_experience_type_valid",
        ),
    )


# ============================================================
# 5. SKILL
# ============================================================

class Skill(TimestampMixin, Base):
    __tablename__ = "skill"

    skill_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    normalized_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        unique=True,
        index=True,
    )

    category: Mapped[str | None] = mapped_column(
        String(100),
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )


# ============================================================
# 6. SKILL ALIAS
# ============================================================

class SkillAlias(TimestampMixin, Base):
    __tablename__ = "skill_alias"

    skill_alias_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    skill_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "skill.skill_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    alias: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    normalized_alias: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        unique=True,
        index=True,
    )


# ============================================================
# 7. JOB ROLE
# ============================================================

class JobRole(TimestampMixin, Base):
    __tablename__ = "job_role"

    job_role_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    normalized_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        unique=True,
        index=True,
    )

    category: Mapped[str | None] = mapped_column(
        String(100),
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text
    )

    standard_code: Mapped[str | None] = mapped_column(
        String(100)
    )

    source_name: Mapped[str | None] = mapped_column(
        String(200)
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )


# ============================================================
# 8. ROLE SKILL
# ============================================================

class RoleSkill(TimestampMixin, Base):
    __tablename__ = "role_skill"

    role_skill_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    job_role_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "job_role.job_role_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    skill_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "skill.skill_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    required_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    preferred_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    related_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    total_posting_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    demand_score: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 5)
    )

    last_aggregated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        UniqueConstraint(
            "job_role_id",
            "skill_id",
            name="uq_role_skill_role_skill",
        ),
        CheckConstraint(
            "required_count >= 0",
            name="role_skill_required_positive",
        ),
        CheckConstraint(
            "preferred_count >= 0",
            name="role_skill_preferred_positive",
        ),
        CheckConstraint(
            "related_count >= 0",
            name="role_skill_related_positive",
        ),
        CheckConstraint(
            """
            demand_score IS NULL
            OR (demand_score >= 0 AND demand_score <= 1)
            """,
            name="role_skill_demand_score_range",
        ),
    )


# ============================================================
# 9. RESOURCE
# ============================================================

class Resource(TimestampMixin, Base):
    __tablename__ = "resource"

    resource_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    resource_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        index=True,
    )

    # 중복 방지용 내부 키
    # 예: CERTIFICATE:1320
    #     JOB:https://...
    dedup_key: Mapped[str | None] = mapped_column(
        String(500),
        unique=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        index=True,
    )

    organization: Mapped[str | None] = mapped_column(
        String(300),
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text
    )

    canonical_url: Mapped[str | None] = mapped_column(
        Text
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    available_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
    )

    __table_args__ = (
        CheckConstraint(
            """
            resource_type IN (
                'COURSE',
                'CERTIFICATE',
                'JOB',
                'PROGRAM'
            )
            """,
            name="resource_type_valid",
        ),
        Index(
            "ix_resource_type_active",
            "resource_type",
            "is_active",
        ),
    )


# ============================================================
# 10. COURSE DETAIL
# ============================================================

class CourseDetail(TimestampMixin, Base):
    __tablename__ = "course_detail"

    resource_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "resource.resource_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )

    department_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "department.department_id",
            ondelete="SET NULL",
        ),
        index=True,
    )

    course_code: Mapped[str | None] = mapped_column(
        String(100),
        index=True,
    )

    credits: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1)
    )

    year_level: Mapped[int | None] = mapped_column(
        SmallInteger
    )

    semester: Mapped[str | None] = mapped_column(
        String(50)
    )

    completion_type: Mapped[str | None] = mapped_column(
        String(100)
    )

    curriculum_year: Mapped[int | None] = mapped_column(
        Integer,
        index=True,
    )

    syllabus_url: Mapped[str | None] = mapped_column(
        Text
    )

    raw_metadata: Mapped[dict | None] = mapped_column(
        JSONB
    )

    __table_args__ = (
        UniqueConstraint(
            "department_id",
            "course_code",
            "curriculum_year",
            name="uq_course_department_code_year",
        ),
    )


# ============================================================
# 11. CERTIFICATE DETAIL
# ============================================================

class CertificateDetail(TimestampMixin, Base):
    __tablename__ = "certificate_detail"

    resource_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "resource.resource_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )

    qualification_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )

    qualification_type: Mapped[str | None] = mapped_column(
        String(100)
    )

    administering_org: Mapped[str | None] = mapped_column(
        String(300)
    )

    major_field_code: Mapped[str | None] = mapped_column(
        String(100)
    )

    major_field_name: Mapped[str | None] = mapped_column(
        String(200)
    )

    middle_field_code: Mapped[str | None] = mapped_column(
        String(100)
    )

    middle_field_name: Mapped[str | None] = mapped_column(
        String(200)
    )

    exam_frequency: Mapped[str | None] = mapped_column(
        String(200)
    )

    validity_period: Mapped[str | None] = mapped_column(
        String(200)
    )

    credit_recognition: Mapped[str | None] = mapped_column(
        Text
    )

    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    raw_metadata: Mapped[dict | None] = mapped_column(
        JSONB
    )


# ============================================================
# 12. CERTIFICATE SECTION
# ============================================================

class CertificateSection(TimestampMixin, Base):
    __tablename__ = "certificate_section"

    certificate_section_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    resource_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "resource.resource_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    section_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    section_order: Mapped[int | None] = mapped_column(
        Integer
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    source_document_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "source_document.source_document_id",
            ondelete="SET NULL",
        ),
    )

    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        UniqueConstraint(
            "resource_id",
            "section_type",
            name="uq_certificate_section_resource_type",
        ),
    )


# ============================================================
# 13. JOB DETAIL
# ============================================================

class JobDetail(TimestampMixin, Base):
    __tablename__ = "job_detail"

    resource_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "resource.resource_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )

    career_type: Mapped[str | None] = mapped_column(
        String(100)
    )

    career_min_years: Mapped[int | None] = mapped_column(
        SmallInteger
    )

    career_max_years: Mapped[int | None] = mapped_column(
        SmallInteger
    )

    education_requirement: Mapped[str | None] = mapped_column(
        String(300)
    )

    employment_type: Mapped[str | None] = mapped_column(
        String(100)
    )

    location: Mapped[str | None] = mapped_column(
        String(500),
        index=True,
    )

    recruitment_count: Mapped[int | None] = mapped_column(
        Integer
    )

    salary_text: Mapped[str | None] = mapped_column(
        String(300)
    )

    responsibilities: Mapped[str | None] = mapped_column(
        Text
    )

    requirements: Mapped[str | None] = mapped_column(
        Text
    )

    preferences: Mapped[str | None] = mapped_column(
        Text
    )

    application_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )

    parse_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="UNPARSED",
        server_default="UNPARSED",
        index=True,
    )

    parsed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        CheckConstraint(
            """
            career_min_years IS NULL
            OR career_min_years >= 0
            """,
            name="job_career_min_positive",
        ),
        CheckConstraint(
            """
            career_max_years IS NULL
            OR career_max_years >= 0
            """,
            name="job_career_max_positive",
        ),
        CheckConstraint(
            """
            career_min_years IS NULL
            OR career_max_years IS NULL
            OR career_min_years <= career_max_years
            """,
            name="job_career_range_valid",
        ),
        CheckConstraint(
            """
            parse_status IN (
                'UNPARSED',
                'PARSED',
                'FAILED',
                'REVIEW_REQUIRED'
            )
            """,
            name="job_parse_status_valid",
        ),
    )


# ============================================================
# 14. PROGRAM DETAIL
# ============================================================

class ProgramDetail(TimestampMixin, Base):
    __tablename__ = "program_detail"

    resource_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "resource.resource_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )

    target_audience: Mapped[str | None] = mapped_column(
        Text
    )

    application_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    application_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )

    program_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    program_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    location: Mapped[str | None] = mapped_column(
        String(500)
    )

    cost_text: Mapped[str | None] = mapped_column(
        String(300)
    )

    capacity: Mapped[int | None] = mapped_column(
        Integer
    )

    application_method: Mapped[str | None] = mapped_column(
        Text
    )

    raw_metadata: Mapped[dict | None] = mapped_column(
        JSONB
    )


# ============================================================
# 15. RESOURCE RELATION
# ============================================================

class ResourceRelation(TimestampMixin, Base):
    __tablename__ = "resource_relation"

    resource_relation_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    resource_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "resource.resource_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    related_resource_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "resource.resource_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    relation_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    evidence_text: Mapped[str | None] = mapped_column(
        Text
    )

    mapping_method: Mapped[str | None] = mapped_column(
        String(50)
    )

    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 5)
    )

    __table_args__ = (
        UniqueConstraint(
            "resource_id",
            "related_resource_id",
            "relation_type",
            name="uq_resource_relation",
        ),
        CheckConstraint(
            "resource_id <> related_resource_id",
            name="resource_relation_not_self",
        ),
        CheckConstraint(
            """
            confidence IS NULL
            OR (confidence >= 0 AND confidence <= 1)
            """,
            name="resource_relation_confidence_range",
        ),
    )


# ============================================================
# 16. RESOURCE JOB ROLE
# ============================================================

class ResourceJobRole(TimestampMixin, Base):
    __tablename__ = "resource_job_role"

    resource_job_role_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    resource_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "resource.resource_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    job_role_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "job_role.job_role_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    mapping_method: Mapped[str | None] = mapped_column(
        String(50)
    )

    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 5)
    )

    evidence_text: Mapped[str | None] = mapped_column(
        Text
    )

    __table_args__ = (
        UniqueConstraint(
            "resource_id",
            "job_role_id",
            name="uq_resource_job_role",
        ),
        CheckConstraint(
            """
            confidence IS NULL
            OR (confidence >= 0 AND confidence <= 1)
            """,
            name="resource_job_role_confidence_range",
        ),
    )


# ============================================================
# 17. RESOURCE SKILL
# ============================================================

class ResourceSkill(TimestampMixin, Base):
    __tablename__ = "resource_skill"

    resource_skill_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    resource_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "resource.resource_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    skill_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "skill.skill_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    relation_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    evidence_text: Mapped[str | None] = mapped_column(
        Text
    )

    mapping_method: Mapped[str | None] = mapped_column(
        String(50)
    )

    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 5)
    )

    verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    source_document_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "source_document.source_document_id",
            ondelete="SET NULL",
        ),
    )

    __table_args__ = (
        UniqueConstraint(
            "resource_id",
            "skill_id",
            "relation_type",
            name="uq_resource_skill_relation",
        ),
        CheckConstraint(
            """
            relation_type IN (
                'REQUIRED',
                'PREFERRED',
                'TAUGHT',
                'VERIFIED',
                'RELATED',
                'EXPERIENCED'
            )
            """,
            name="resource_skill_relation_type_valid",
        ),
        CheckConstraint(
            """
            confidence IS NULL
            OR (confidence >= 0 AND confidence <= 1)
            """,
            name="resource_skill_confidence_range",
        ),
    )


# ============================================================
# 18. STUDENT RESOURCE
# ============================================================

class StudentResource(TimestampMixin, Base):
    __tablename__ = "student_resource"

    student_resource_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    student_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "student.student_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    resource_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "resource.resource_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    grade_text: Mapped[str | None] = mapped_column(
        String(100)
    )

    evidence_text: Mapped[str | None] = mapped_column(
        Text
    )

    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "resource_id",
            name="uq_student_resource",
        ),
        CheckConstraint(
            """
            status IN (
                'PLANNED',
                'BOOKMARKED',
                'IN_PROGRESS',
                'COMPLETED',
                'APPLIED',
                'REJECTED',
                'WITHDRAWN'
            )
            """,
            name="student_resource_status_valid",
        ),
    )


# ============================================================
# 19. STUDENT SKILL
# ============================================================

class StudentSkill(TimestampMixin, Base):
    __tablename__ = "student_skill"

    student_skill_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    student_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "student.student_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    skill_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "skill.skill_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # 과도한 숫자화는 피하고 필요할 때만 사용
    level: Mapped[str | None] = mapped_column(
        String(100)
    )

    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 5)
    )

    verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    acquired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "skill_id",
            name="uq_student_skill",
        ),
        CheckConstraint(
            """
            confidence IS NULL
            OR (confidence >= 0 AND confidence <= 1)
            """,
            name="student_skill_confidence_range",
        ),
    )


# ============================================================
# 20. STUDENT SKILL EVIDENCE
# ============================================================

class StudentSkillEvidence(TimestampMixin, Base):
    __tablename__ = "student_skill_evidence"

    evidence_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    student_skill_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "student_skill.student_skill_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    evidence_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # 과목/자격증 등 Resource 기반 근거
    resource_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "resource.resource_id",
            ondelete="SET NULL",
        ),
    )

    # 프로젝트/인턴 등 개인 경험 근거
    experience_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "student_experience.experience_id",
            ondelete="SET NULL",
        ),
    )

    # 업로드 문서 등의 원문 근거
    source_document_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "source_document.source_document_id",
            ondelete="SET NULL",
        ),
    )

    description: Mapped[str | None] = mapped_column(
        Text
    )

    source_url: Mapped[str | None] = mapped_column(
        Text
    )

    weight: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 5)
    )

    __table_args__ = (
        CheckConstraint(
            """
            NOT (
                (resource_id IS NOT NULL AND experience_id IS NOT NULL)
                OR
                (resource_id IS NOT NULL AND source_document_id IS NOT NULL)
                OR
                (experience_id IS NOT NULL AND source_document_id IS NOT NULL)
            )
            """,
            name="student_skill_evidence_single_source",
        ),
        CheckConstraint(
            """
            weight IS NULL
            OR (weight >= 0 AND weight <= 1)
            """,
            name="student_skill_evidence_weight_range",
        ),
    )


# ============================================================
# 21. SOURCE DOCUMENT
# ============================================================

class SourceDocument(TimestampMixin, Base):
    __tablename__ = "source_document"

    source_document_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    # 검증 전에는 NULL 가능
    resource_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "resource.resource_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    source_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
    )

    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    document_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    # 원 제공기관 내부 ID 등
    source_record_key: Mapped[str | None] = mapped_column(
        String(500)
    )

    discovery_channel: Mapped[str | None] = mapped_column(
        String(100)
    )

    discovery_query: Mapped[str | None] = mapped_column(
        String(500)
    )

    source_url: Mapped[str | None] = mapped_column(
        Text
    )

    normalized_url: Mapped[str | None] = mapped_column(
        Text
    )

    source_host: Mapped[str | None] = mapped_column(
        String(300),
        index=True,
    )

    title: Mapped[str | None] = mapped_column(
        Text
    )

    # 정제한 원문 텍스트
    content: Mapped[str | None] = mapped_column(
        Text
    )

    # API 원본 필드 / 검색 metadata
    raw_metadata: Mapped[dict | None] = mapped_column(
        JSONB
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    content_hash: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
    )

    http_status: Mapped[int | None] = mapped_column(
        Integer
    )

    fetch_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
        index=True,
    )

    validation_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
        index=True,
    )

    trust_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="UNKNOWN",
        server_default="UNKNOWN",
        index=True,
    )

    evidence_eligible: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "source_name",
            "source_record_key",
            name="uq_source_document_source_record",
        ),
        CheckConstraint(
            """
            source_type IN (
                'OFFICIAL_API',
                'OFFICIAL_WEB',
                'PUBLIC_DATA',
                'UNIVERSITY_WEB',
                'JOB_BOARD',
                'SEARCH_API',
                'MANUAL',
                'OTHER'
            )
            """,
            name="source_document_source_type_valid",
        ),
        CheckConstraint(
            """
            document_type IN (
                'SEARCH_RESULT',
                'WEB_PAGE',
                'API_RECORD',
                'SYLLABUS',
                'NOTICE',
                'CERTIFICATE_INFO',
                'OTHER'
            )
            """,
            name="source_document_document_type_valid",
        ),
        CheckConstraint(
            """
            fetch_status IN (
                'PENDING',
                'SUCCESS',
                'HTTP_ERROR',
                'NO_TEXT',
                'PARSE_ERROR',
                'UNSUPPORTED_CONTENT_TYPE',
                'SKIPPED'
            )
            """,
            name="source_document_fetch_status_valid",
        ),
        CheckConstraint(
            """
            validation_status IN (
                'PENDING',
                'ACCEPTED',
                'REJECTED',
                'ERROR'
            )
            """,
            name="source_document_validation_status_valid",
        ),
        CheckConstraint(
            """
            trust_level IN (
                'A',
                'B',
                'C',
                'UNKNOWN'
            )
            """,
            name="source_document_trust_level_valid",
        ),
    )


# ============================================================
# 22. DOCUMENT CHUNK
# ============================================================

class DocumentChunk(TimestampMixin, Base):
    __tablename__ = "document_chunk"

    chunk_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    source_document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "source_document.source_document_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    token_count: Mapped[int | None] = mapped_column(
        Integer
    )

    # 현재 팀 기본 임베딩 모델 bge-m3 기준 1024차원
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1024)
    )

    embedding_model: Mapped[str | None] = mapped_column(
        String(200)
    )

    embedding_dim: Mapped[int | None] = mapped_column(
        Integer
    )

    extra_metadata: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
    )

    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "chunk_index",
            name="uq_document_chunk_index",
        ),
        CheckConstraint(
            "chunk_index >= 0",
            name="document_chunk_index_positive",
        ),
        CheckConstraint(
            """
            token_count IS NULL
            OR token_count >= 0
            """,
            name="document_chunk_token_count_positive",
        ),
    )