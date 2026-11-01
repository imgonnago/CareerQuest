from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import SourceDocument


# ============================================================
# Time
# ============================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
# JSONL
# ============================================================

def read_jsonl(path: Path) -> list[dict]:
    """
    JSONL 파일을 읽어 dict list로 반환한다.
    빈 줄은 무시한다.
    """

    records: list[dict] = []

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:

        for line in file:
            line = line.strip()

            if not line:
                continue

            records.append(
                json.loads(line)
            )

    return records


# ============================================================
# Text / Hash
# ============================================================

def normalize_text_for_hash(
    text: str,
) -> str:
    """
    의미 없는 공백/개행 차이 때문에
    hash가 달라지는 것을 줄인다.
    """

    text = text.replace(
        "\r\n",
        "\n",
    ).replace(
        "\r",
        "\n",
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


def sha256_text(
    text: str | None,
) -> str | None:

    if text is None:
        return None

    normalized = (
        normalize_text_for_hash(
            text
        )
    )

    return hashlib.sha256(
        normalized.encode(
            "utf-8"
        )
    ).hexdigest()


# ============================================================
# URL
# ============================================================

TRACKING_QUERY_KEYS = {
    "ref",
    "refer",
    "tracking",
    "trackingcode",
    "source",
}


def normalize_url(
    url: str | None,
) -> str | None:

    if not url:
        return None

    parts = urlsplit(
        url.strip()
    )

    query_items = []

    for key, value in parse_qsl(
        parts.query,
        keep_blank_values=True,
    ):

        key_lower = key.lower()

        if key_lower.startswith(
            "utm_"
        ):
            continue

        if (
            key_lower
            in TRACKING_QUERY_KEYS
        ):
            continue

        query_items.append(
            (key, value)
        )

    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            urlencode(query_items),
            "",
        )
    )


def get_host(
    url: str | None,
) -> str | None:

    if not url:
        return None

    host = (
        urlsplit(url)
        .netloc
        .lower()
    )

    if host.startswith("www."):
        host = host[4:]

    return host or None


# ============================================================
# SourceDocument
# ============================================================

def upsert_source_document(
    session: Session,
    *,
    resource_id: int | None,
    source_name: str,
    source_type: str,
    document_type: str,
    source_record_key: str,
    source_url: str | None,
    title: str | None,
    content: str | None,
    raw_metadata: dict | None,
    published_at: datetime | None = None,
    expires_at: datetime | None = None,
    discovery_channel: str | None = None,
    discovery_query: str | None = None,
    http_status: int | None = None,
    fetch_status: str = "SUCCESS",
    validation_status: str = "ACCEPTED",
    trust_level: str = "A",
    evidence_eligible: bool = True,
) -> SourceDocument:
    """
    source_name + source_record_key 기준으로
    SourceDocument를 생성하거나 갱신한다.

    SourceDocument는 과거 모든 버전을 저장하는
    아카이브가 아니라 최신 snapshot 용도로 사용한다.
    """

    source_record_key = (
        source_record_key.strip()
    )

    if not source_record_key:
        raise ValueError(
            "source_record_key는 "
            "비어 있을 수 없습니다."
        )

    document = session.scalar(
        select(
            SourceDocument
        ).where(
            SourceDocument.source_name
            == source_name,

            SourceDocument.source_record_key
            == source_record_key,
        )
    )

    normalized_source_url = (
        normalize_url(
            source_url
        )
    )

    content_hash = (
        sha256_text(
            content
        )
    )

    # --------------------------------------------------------
    # 신규 데이터
    # --------------------------------------------------------

    if document is None:

        document = SourceDocument(
            resource_id=resource_id,

            source_name=source_name,
            source_type=source_type,
            document_type=document_type,

            source_record_key=(
                source_record_key
            ),

            discovery_channel=(
                discovery_channel
            ),

            discovery_query=(
                discovery_query
            ),

            source_url=source_url,

            normalized_url=(
                normalized_source_url
            ),

            source_host=(
                get_host(
                    source_url
                )
            ),

            title=title,
            content=content,

            raw_metadata=(
                raw_metadata
            ),

            published_at=(
                published_at
            ),

            expires_at=(
                expires_at
            ),

            fetched_at=(
                utc_now()
            ),

            content_hash=(
                content_hash
            ),

            http_status=(
                http_status
            ),

            fetch_status=(
                fetch_status
            ),

            validation_status=(
                validation_status
            ),

            trust_level=(
                trust_level
            ),

            evidence_eligible=(
                evidence_eligible
            ),
        )

        session.add(
            document
        )

        session.flush()

        return document

    # --------------------------------------------------------
    # 기존 snapshot 갱신
    # --------------------------------------------------------

    document.resource_id = (
        resource_id
    )

    document.source_type = (
        source_type
    )

    document.document_type = (
        document_type
    )

    document.discovery_channel = (
        discovery_channel
    )

    document.discovery_query = (
        discovery_query
    )

    document.source_url = (
        source_url
    )

    document.normalized_url = (
        normalized_source_url
    )

    document.source_host = (
        get_host(
            source_url
        )
    )

    document.title = (
        title
    )

    document.content = (
        content
    )

    document.raw_metadata = (
        raw_metadata
    )

    document.published_at = (
        published_at
    )

    document.expires_at = (
        expires_at
    )

    document.fetched_at = (
        utc_now()
    )

    document.content_hash = (
        content_hash
    )

    document.http_status = (
        http_status
    )

    document.fetch_status = (
        fetch_status
    )

    document.validation_status = (
        validation_status
    )

    document.trust_level = (
        trust_level
    )

    document.evidence_eligible = (
        evidence_eligible
    )

    session.flush()

    return document