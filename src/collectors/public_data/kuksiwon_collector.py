
from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select

from src.db.database import SessionLocal
from src.db.models import (
    CertificateDetail,
    CertificateSection,
    Resource,
)
from src.loaders.common import upsert_source_document, utc_now

INDEX_URL = "https://www.kuksiwon.or.kr/cnt/c_1/view.do?seq=7"
SOURCE = "KUKSIWON_OCCUPATION_INFO"
ALLOWED_HOST = "www.kuksiwon.or.kr"

# 시험 방식(실기·예비시험)은 자격증 자체가 아니므로 제외한다.
# 등급이 나뉘는 직종은 현재 '상위 직종'으로만 수집하고
# 등급별 면허/자격 구분은 후속 검증에서 처리한다.
NAMES = (
    "의사", "치과의사", "한의사", "약사", "한약사",
    "간호사", "조산사", "임상병리사", "방사선사", "물리치료사",
    "작업치료사", "치과기공사", "치과위생사", "보건의료정보관리사",
    "안경사", "영양사", "위생사", "응급구조사", "의지·보조기 기사",
    "언어재활사", "보건교육사", "요양보호사", "간호조무사",
    "장애인재활상담사", "보조공학사",
)


@dataclass(frozen=True)
class Entry:
    name: str
    url: str


def fetch(session: requests.Session, url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        raise ValueError(f"허용되지 않은 주소: {url}")

    response = session.get(
        url,
        timeout=(10, 30),
        allow_redirects=True,
    )
    response.raise_for_status()

    final = urlsplit(response.url)
    if final.scheme != "https" or final.hostname != ALLOWED_HOST:
        raise ValueError(f"외부 주소로 이동: {response.url}")

    response.encoding = (
        response.apparent_encoding or response.encoding
    )
    return response.text


def discover(html: str) -> list[Entry]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, Entry] = {}

    for link in soup.find_all("a", href=True):
        name = re.sub(
            r"\s+",
            " ",
            link.get_text(" ", strip=True),
        ).strip()

        if name not in NAMES or name in found:
            continue

        url = urljoin(INDEX_URL, link["href"])
        parts = urlsplit(url)

        if (
            parts.scheme != "https"
            or parts.hostname != ALLOWED_HOST
            or not parts.path.startswith("/subcnt/")
            or not parts.path.endswith("/view.do")
        ):
            continue

        found[name] = Entry(name, url)

    return [
        found[name]
        for name in NAMES
        if name in found
    ]


def occupation_profile(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for item in soup.select("script,style,noscript"):
        item.decompose()

    lines = [
        re.sub(r"\s+", " ", t).strip()
        for t in soup.stripped_strings
    ]

    starts = [
        i
        for i, line in enumerate(lines)
        if line == "개요"
    ]

    for start in starts:
        stops = [
            i
            for i in range(
                start + 1,
                min(start + 100, len(lines)),
            )
            if lines[i] == "시험일정"
        ]

        if not stops:
            continue

        excerpt = lines[start:stops[0]]

        if "수행직무" not in excerpt:
            continue

        value = "\n".join(excerpt)

        if 100 <= len(value) <= 16000:
            return value

    return ""


def key_for(name: str) -> str:
    return "KUKSIWON:" + re.sub(
        r"\s+", "", name
    )


def save(entry: Entry, profile: str) -> str:
    key = key_for(entry.name)
    eligible = len(profile) >= 100

    metadata = {
        "official_exam_name": entry.name,
        "official_index": INDEX_URL,
        "profile_extracted": eligible,
        "exam_schedule_verified": False,
        "eligibility_verified": False,
        "credential_subtype_verified": False,
        "source_version": "kuksiwon_occupation_v1",
    }

    with SessionLocal() as db:
        with db.begin():

            cert = db.scalar(
                select(CertificateDetail).where(
                    CertificateDetail.qualification_code == key
                )
            )

            resource = (
                db.get(Resource, cert.resource_id)
                if cert
                else None
            )

            if resource is None:
                resource = db.scalar(
                    select(Resource).where(
                        Resource.dedup_key
                        == f"CERTIFICATE:{key}"
                    )
                )

            status = (
                "UPDATED" if resource else "CREATED"
            )

            if resource is None:
                resource = Resource(
                    resource_type="CERTIFICATE",
                    dedup_key=f"CERTIFICATE:{key}",
                    name=entry.name,
                    organization="한국보건의료인국가시험원",
                    is_active=True,
                )
                db.add(resource)
                db.flush()

            elif resource.resource_type != "CERTIFICATE":
                raise RuntimeError(
                    f"리소스 타입 충돌: {key}"
                )

            resource.name = entry.name
            resource.canonical_url = entry.url
            resource.last_seen_at = utc_now()

            if eligible:
                resource.description = profile[:1500]

            if cert is None:
                cert = CertificateDetail(
                    resource_id=resource.resource_id,
                    qualification_code=key,
                )
                db.add(cert)

            cert.qualification_type = (
                "보건의료인 국가시험 관련"
            )
            cert.administering_org = (
                "한국보건의료인국가시험원"
            )
            cert.raw_metadata = metadata
            db.flush()

            doc = upsert_source_document(
                db,
                resource_id=resource.resource_id,
                source_name=SOURCE,
                source_type="OFFICIAL_WEB",
                document_type="CERTIFICATE_INFO",
                source_record_key=key,
                source_url=entry.url,
                title=(
                    f"{entry.name} - "
                    "국시원 직종별 시험정보"
                ),
                content=(
                    profile
                    or f"국시원 직종별 시험정보 목록: {entry.name}"
                ),
                raw_metadata=metadata,
                discovery_channel=(
                    "KUKSIWON_OFFICIAL_INDEX"
                ),
                fetch_status="SUCCESS",
                validation_status=(
                    "ACCEPTED" if eligible else "PENDING"
                ),
                trust_level="A",
                evidence_eligible=eligible,
                http_status=200,
            )

            if eligible:
                section = db.scalar(
                    select(CertificateSection).where(
                        CertificateSection.resource_id
                        == resource.resource_id,
                        CertificateSection.section_type
                        == "OCCUPATION_OVERVIEW",
                    )
                )

                if section is None:
                    section = CertificateSection(
                        resource_id=resource.resource_id,
                        section_type="OCCUPATION_OVERVIEW",
                    )
                    db.add(section)

                section.content = profile
                section.section_order = 1
                section.source_document_id = (
                    doc.source_document_id
                )

    return status


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--limit", type=int, default=None
    )
    parser.add_argument(
        "--delay", type=float, default=1.5
    )
    parser.add_argument(
        "--apply", action="store_true"
    )
    parser.add_argument(
        "--policy-reviewed", action="store_true"
    )

    args = parser.parse_args()

    if (
        args.delay < 1
        or (
            args.limit is not None
            and args.limit < 1
        )
    ):
        parser.error(
            "delay >= 1, limit >= 1 이어야 합니다."
        )

    if args.apply and not args.policy_reviewed:
        parser.error(
            "공식 이용조건 확인 후 "
            "--policy-reviewed를 사용하세요."
        )

    session = requests.Session()
    session.headers.update({
        "User-Agent": "CareerQuest-AcademicProject/0.1"
    })

    try:
        entries = discover(
            fetch(session, INDEX_URL)
        )

        print(
            f"[공식 직종 목록] "
            f"발견={len(entries)}, "
            f"기대={len(NAMES)}"
        )

        found_names = {
            entry.name for entry in entries
        }

        missing = [
            name
            for name in NAMES
            if name not in found_names
        ]

        if missing:
            print(
                "[목록에서 확인되지 않음]",
                ", ".join(missing),
            )

        if len(entries) < 15:
            raise RuntimeError(
                "직종 링크 파싱 결과가 "
                "너무 적어 중단합니다."
            )

        if args.limit:
            entries = entries[:args.limit]

        ok = 0
        errors = 0

        for i, entry in enumerate(
            entries, 1
        ):
            try:
                time.sleep(args.delay)

                profile = occupation_profile(
                    fetch(session, entry.url)
                )

                if not profile:
                    print(
                        f"[{i}/{len(entries)}] "
                        "SKIP 본문 검증 실패 "
                        f"| {entry.name} | {entry.url}"
                    )
                    errors += 1
                    continue

                print(
                    f"[{i}/{len(entries)}] "
                    f"BODY {len(profile)}자 "
                    f"| {entry.name} | {entry.url}"
                )

                if not args.apply:
                    print(
                        "  미리보기:",
                        repr(profile[:250]),
                    )
                else:
                    print(
                        "  DB:",
                        save(entry, profile),
                    )

                ok += 1

            except (
                requests.RequestException,
                ValueError,
                RuntimeError,
            ) as exc:
                errors += 1

                print(
                    f"[{i}/{len(entries)}] "
                    f"ERROR {entry.name}: {exc}"
                )

        print(
            f"[완료] 확보={ok}, "
            f"실패/보류={errors}, "
            f"{'APPLY' if args.apply else 'DRY RUN'}"
        )

    finally:
        session.close()


if __name__ == "__main__":
    main()
