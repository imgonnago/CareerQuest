from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


ResourceType = Literal[
    "JOB",
    "COURSE",
    "CERTIFICATE",
    "PROGRAM",
]


class RawResource(BaseModel):
    """
    모든 수집기가 공통으로 반환하는 원본 데이터 형식.

    아직 RESOURCE DB 테이블에 바로 넣는 데이터가 아니라,
    수집 단계에서 원문을 최대한 보존하기 위한 RAW 형식이다.
    """

    resource_type: ResourceType

    source_site: str
    source_url: str
    source_host: str

    title: str
    organization: str | None = None

    # 검색 결과 snippet 또는 실제 페이지 원문
    raw_text: str = ""

    published_at: datetime | None = None
    expires_at: datetime | None = None
    fetched_at: datetime

    # 출처별 추가 데이터를 그대로 보존
    raw_metadata: dict[str, Any] = Field(default_factory=dict)