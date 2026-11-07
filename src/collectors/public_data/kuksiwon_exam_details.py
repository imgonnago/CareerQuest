
from __future__ import annotations

import argparse
import csv
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from sqlalchemy import select, text

from src.db.database import SessionLocal, engine
from src.db.models import (
    CertificateSection,
    Resource,
)
from src.loaders.common import upsert_source_document


SOURCE_NAME = "KUKSIWON_EXAM_DETAIL"
OUTPUT = Path(
    "data/reports/certificates/"
    "kuksiwon_exam_review.csv"
)

PAGES = {
    2: "EXAM_ELIGIBILITY",
    7: "EXAM_SUBJECTS",
}


def tidy(value: str) -> str:
    return re.sub(
        r"\s+", " ", value or ""
    ).strip()


def make_url(
    source_url: str,
    page: int,
) -> str | None:

    parts = urlsplit(source_url)

    if parts.netloc.lower() not in {
        "www.kuksiwon.or.kr",
        "kuksiwon.or.kr",
    }:
        return None

    new_path = re.sub(
        r"/1/view\.do$",
        f"/{page}/view.do",
        parts.path,
    )

    if new_path == parts.path:
        return None

    return urlunsplit((
        parts.scheme,
        parts.netloc,
        new_path,
        parts.query,
        "",
    ))


def extract_eligibility(
    soup: BeautifulSoup,
) -> str:

    heading = next(
        (
            h for h in soup.find_all(
                re.compile(r"^h[2-6]$")
            )
            if tidy(
                h.get_text(" ", strip=True)
            ) == "응시자격"
        ),
        None,
    )

    if heading is None:
        return ""

    lines = []

    for node in heading.next_elements:

        if isinstance(node, Tag):
            if (
                node is not heading
                and node.name in {
                    "h2", "h3", "h4",
                    "h5", "h6", "footer", "nav",
                }
            ):
                break

        elif isinstance(node, NavigableString):

            if (
                node.parent is heading
                or node.parent.find_parent([
                    "script", "style",
                    "nav", "footer",
                ])
            ):
                continue

            part = tidy(str(node))

            if part:
                lines.append(part)

            if sum(map(len, lines)) > 12000:
                break

    result = "\n".join(lines)

    keywords = (
        "응시할 수",
        "자격이 있는",
        "졸업한",
        "결격사유",
        "교육과정",
    )

    if not (
        80 <= len(result) <= 13000
        and any(
            word in result
            for word in keywords
        )
    ):
        return ""

    return result


def extract_subjects(
    soup: BeautifulSoup,
) -> str:

    recognized = []

    for table in soup.find_all("table"):

        table_text = tidy(
            table.get_text(" ", strip=True)
        )

        if len(table_text) < 60:
            continue

        has_exam_heading = any(
            word in table_text
            for word in (
                "시험과목",
                "시험 과목",
                "시험종별",
            )
        )

        if not has_exam_heading:
            continue

        title = (
            "시험시간표"
            if (
                    "시험시간표" in table_text
                    or "교시별" in table_text
            )
            else "시험과목"
        )

        rows = []

        for tr in table.find_all("tr"):

            cells = [
                tidy(
                    cell.get_text(
                        " ", strip=True
                    )
                )
                for cell in tr.find_all(
                    ["th", "td"],
                    recursive=False,
                )
            ]

            if cells:
                rows.append(
                    " | ".join(cells)
                )

        if rows:
            recognized.append(
                title + "\n"
                + "\n".join(rows)
            )

    result = "\n\n".join(recognized)

    if not (
        100 <= len(result) <= 20000
        and "시험" in result
    ):
        return ""

    return result


def load_targets(
    names: list[str] | None,
    limit: int,
) -> list[dict]:

    sql = text("""
        SELECT DISTINCT ON (r.resource_id)
            r.resource_id,
            r.name,
            COALESCE(
                sd.source_url,
                r.canonical_url
            ) AS source_url

        FROM resource r

        JOIN source_document sd
          ON sd.resource_id = r.resource_id

        WHERE r.resource_type = 'CERTIFICATE'
          AND sd.source_name =
              'KUKSIWON_OCCUPATION_INFO'

        ORDER BY
            r.resource_id,
            sd.source_document_id DESC
    """)

    with engine.connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                sql
            ).mappings().all()
        ]

    if names:

        rows = [
            row for row in rows
            if row["name"] in names
        ]

        missing = (
            set(names)
            - {row["name"] for row in rows}
        )

        if missing:
            raise ValueError(
                f"DB에 없는 직종: {sorted(missing)}"
            )

    return rows[:limit]


def apply_one(
    resource_id: int,
    name: str,
    section_type: str,
    content: str,
    url: str,
    page: int,
) -> str:

    with SessionLocal() as db:

        if db.get(Resource, resource_id) is None:
            return "SKIP_MISSING_RESOURCE"

        existing = db.scalar(
            select(CertificateSection).where(
                CertificateSection.resource_id
                == resource_id,

                CertificateSection.section_type
                == section_type,
            )
        )

        # 기존 Section을 덮어쓰지 않음
        if existing is not None:
            return "SKIP_EXISTING_SECTION"

        doc = upsert_source_document(
            db,
            resource_id=resource_id,

            source_name=SOURCE_NAME,
            source_type="OFFICIAL_WEB",
            document_type="CERTIFICATE_INFO",

            source_record_key=(
                f"{resource_id}:{section_type}"
            ),

            source_url=url,
            title=f"{name} - {section_type}",
            content=content,

            raw_metadata={
                "page": page,
                "section_type": section_type,
                "parser": "kuksiwon_exam_v1",
            },

            discovery_channel="OFFICIAL_WEB",
            http_status=200,
            fetch_status="SUCCESS",
            validation_status="ACCEPTED",
            trust_level="A",
            evidence_eligible=True,
        )

        db.add(
            CertificateSection(
                resource_id=resource_id,
                section_type=section_type,

                section_order=(
                    2 if page == 2 else 3
                ),

                content=content,
                source_document_id=(
                    doc.source_document_id
                ),
            )
        )

        db.commit()

    return "CREATED"


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--names",
        nargs="*",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=3,
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args = parser.parse_args()

    if (
        not 1 <= args.limit <= 25
        or args.delay < 1
    ):
        parser.error(
            "limit 1~25, delay >= 1"
        )

    targets = load_targets(
        args.names,
        args.limit,
    )

    if not targets:
        print(
            "[EMPTY] 국시원 대상이 없습니다."
        )
        return

    results = []

    with requests.Session() as http:

        http.headers.update({
            "User-Agent":
                "CareerQuestAcademicResearch/1.0"
        })

        for row in targets:

            for page, section_type in (
                PAGES.items()
            ):

                url = make_url(
                    row["source_url"] or "",
                    page,
                )

                result = {
                    "resource_id": row["resource_id"],
                    "occupation": row["name"],
                    "section_type": section_type,
                    "source_url": url or "",
                    "status": "HOLD",
                    "length": 0,
                    "preview": "",
                }

                if not url:
                    result["status"] = (
                        "INVALID_SOURCE_URL"
                    )

                else:
                    try:
                        response = http.get(
                            url,
                            timeout=20,
                        )

                        response.raise_for_status()

                        soup = BeautifulSoup(
                            response.text,
                            "html.parser",
                        )

                        if page == 2:
                            content = (
                                extract_eligibility(soup)
                            )
                        else:
                            content = (
                                extract_subjects(soup)
                            )

                        result["length"] = len(
                            content
                        )

                        result["preview"] = tidy(
                            content
                        )[:160]

                        if content:
                            result["status"] = "READY"

                            if args.apply:
                                result["status"] = (
                                    apply_one(
                                        row["resource_id"],
                                        row["name"],
                                        section_type,
                                        content,
                                        url,
                                        page,
                                    )
                                )

                    except (
                        requests.RequestException,
                        ValueError,
                    ) as exc:

                        result["status"] = (
                            "FETCH_ERROR:"
                            + type(exc).__name__
                        )

                    time.sleep(args.delay)

                results.append(result)

                print(
                    f"[{result['status']}] "
                    f"{row['name']} "
                    f"{section_type} "
                    f"{result['length']}자"
                )

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=list(results[0]),
        )

        writer.writeheader()
        writer.writerows(results)

    print("[REPORT]", OUTPUT)

    print(
        "[MODE]",
        "APPLY"
        if args.apply
        else "DRY RUN / DB 변경 없음",
    )


if __name__ == "__main__":
    main()
