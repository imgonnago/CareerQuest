from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import JobDetail, Resource
from src.loaders.common import (
    normalize_url,
    sha256_text,
    upsert_source_document,
    utc_now,
)
from src.loaders.job_loader import clean_string, parse_iso_datetime


def _job_dedup_key(url: str) -> str:
    normalized = normalize_url(url) or url
    digest = sha256_text(normalized)
    if not digest:
        raise ValueError("채용 URL hash 생성 실패")
    return f"JOB:{digest}"


def _source_record_key(record: dict) -> str:
    explicit = clean_string(record.get("source_record_key"))
    if explicit:
        return explicit

    source_url = (
        clean_string(record.get("final_url"))
        or clean_string(record.get("source_url"))
    )
    if not source_url:
        raise ValueError("source_record_key 또는 채용 URL이 필요합니다.")

    digest = sha256_text(normalize_url(source_url) or source_url)
    if not digest:
        raise ValueError("SourceDocument URL hash 생성 실패")
    return f"OFFICIAL_JOB:{digest}"


def upsert_official_job_record(
    session: Session,
    record: dict,
    *,
    source_name: str,
) -> dict:
    """
    신뢰 가능한 공식 채용 페이지 1건을 DB에 바로 적재한다.

    record 주요 필드:
    - source_url / final_url
    - title
    - organization
    - raw_text
    - published_at
    - application_start / deadline
    - location
    - career_type / employment_type
    - education_requirement
    - recruitment_count / salary_text
    - responsibilities / requirements / preferences
    - source_record_key
    - raw_metadata
    """

    source_url = (
        clean_string(record.get("final_url"))
        or clean_string(record.get("source_url"))
    )
    if not source_url:
        raise ValueError("채용공고 URL이 없습니다.")

    title = (
        clean_string(record.get("title"))
        or clean_string(record.get("page_title"))
        or "채용공고"
    )
    raw_text = clean_string(record.get("raw_text"))
    if not raw_text:
        raise ValueError(f"채용공고 본문이 없습니다: {title}")

    canonical_url = normalize_url(source_url) or source_url
    dedup_key = _job_dedup_key(canonical_url)

    published_at = parse_iso_datetime(record.get("published_at"))
    application_start = parse_iso_datetime(record.get("application_start"))
    deadline = parse_iso_datetime(record.get("deadline"))

    is_current = (
        deadline is not None
        and deadline >= datetime.now(timezone.utc)
    )

    resource = session.scalar(
        select(Resource).where(Resource.dedup_key == dedup_key)
    )

    created = resource is None

    if resource is None:
        resource = Resource(
            resource_type="JOB",
            dedup_key=dedup_key,
            name=title,
            organization=clean_string(record.get("organization")),
            description=clean_string(record.get("snippet")),
            canonical_url=canonical_url,
            published_at=published_at,
            available_from=application_start,
            expires_at=deadline,
            is_active=is_current,
        )
        session.add(resource)
        session.flush()
    else:
        resource.name = title
        resource.organization = (
            clean_string(record.get("organization"))
            or resource.organization
        )
        resource.description = (
            clean_string(record.get("snippet"))
            or resource.description
        )
        resource.canonical_url = canonical_url
        resource.last_seen_at = utc_now()
        resource.is_active = is_current

        if published_at:
            resource.published_at = published_at
        if application_start:
            resource.available_from = application_start
        if deadline:
            resource.expires_at = deadline

    detail = session.get(JobDetail, resource.resource_id)
    if detail is None:
        detail = JobDetail(
            resource_id=resource.resource_id,
            parse_status="UNPARSED",
        )
        session.add(detail)

    direct_fields = {
        "career_type": clean_string(record.get("career_type")),
        "education_requirement": clean_string(
            record.get("education_requirement")
        ),
        "employment_type": clean_string(record.get("employment_type")),
        "location": clean_string(record.get("location")),
        "salary_text": clean_string(record.get("salary_text")),
        "responsibilities": clean_string(record.get("responsibilities")),
        "requirements": clean_string(record.get("requirements")),
        "preferences": clean_string(record.get("preferences")),
        "application_start": application_start,
        "deadline": deadline,
    }
    for field, value in direct_fields.items():
        if value is not None:
            setattr(detail, field, value)

    recruitment_count = record.get("recruitment_count")
    if recruitment_count is not None:
        try:
            detail.recruitment_count = int(recruitment_count)
        except (TypeError, ValueError):
            pass

    raw_metadata = dict(record.get("raw_metadata") or {})
    raw_metadata["_ingestion"] = {
        "mode": "DIRECT_OFFICIAL_WEB",
        "source_name": source_name,
    }

    source_document = upsert_source_document(
        session,
        resource_id=resource.resource_id,
        source_name=source_name,
        source_type="OFFICIAL_WEB",
        document_type="NOTICE",
        source_record_key=_source_record_key(record),
        source_url=source_url,
        title=title,
        content=raw_text,
        raw_metadata=raw_metadata,
        published_at=published_at,
        expires_at=deadline,
        discovery_channel="DIRECT_CRAWL",
        discovery_query=None,
        http_status=record.get("http_status", 200),
        fetch_status="SUCCESS",
        validation_status="ACCEPTED",
        trust_level="A",
        evidence_eligible=True,
    )

    session.flush()

    return {
        "created": created,
        "resource_id": resource.resource_id,
        "source_document_id": source_document.source_document_id,
    }
