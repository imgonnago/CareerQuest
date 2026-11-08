from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import text
from src.db.database import SessionLocal
from src.db.models import SourceDocument, Resource, ProgramDetail
from src.collectors.programs.notice_quality import (
    classify_notice, classify_program_notice,
)

REVIEW_FILE = Path(
    "data/reports/program_repair/department_program_review.csv"
)
SOURCE_NAME = "KONYANG_DEPARTMENT_PROGRAM_NOTICES"


def load_candidates():
    with REVIEW_FILE.open(
        encoding="utf-8-sig", newline=""
    ) as f:
        rows = list(csv.DictReader(f))

    seen = set()
    selected = []

    for row in rows:
        doc_id = int(row["source_document_id"])
        if doc_id in seen:
            raise RuntimeError(f"Duplicate ID: {doc_id}")
        seen.add(doc_id)

        body = row["body"]
        digest = hashlib.sha256(
            body.encode("utf-8")
        ).hexdigest()

        if digest != row["body_sha256"]:
            raise RuntimeError(f"CSV hash mismatch: {doc_id}")

        decision = row["review_decision"].strip().upper()
        if decision not in ("", "HOLD", "APPROVE", "REJECT"):
            raise RuntimeError(f"Invalid decision: {doc_id}")

        if (
            classify_notice(body, row["title"]) == "BODY_REVIEW"
            and classify_program_notice(row["title"])
            == "PROGRAM_CANDIDATE"
            and decision != "REJECT"
        ):
            selected.append(row)

    return sorted(
        selected, key=lambda r: int(r["source_document_id"])
    )


def run(args):
    candidates = load_candidates()
    if not candidates:
        raise RuntimeError("No eligible candidates")

    approved = 0
    preserved = 0
    updates = []
    plan_parts = []

    with SessionLocal() as db:
        if not args.apply:
            db.execute(text("SET TRANSACTION READ ONLY"))

        for row in candidates:
            doc_id = int(row["source_document_id"])
            doc = db.get(
                SourceDocument, doc_id,
                with_for_update=args.apply,
            )

            if (
                doc is None
                or doc.source_name != SOURCE_NAME
                or doc.title != row["title"]
                or doc.source_url != row["source_url"]
            ):
                raise RuntimeError(
                    f"Document identity mismatch: {doc_id}"
                )

            parsed = urlsplit(doc.source_url or "")
            if (
                parsed.scheme != "https"
                or not (parsed.hostname or "").endswith(
                    ".konyang.ac.kr"
                )
            ):
                raise RuntimeError(
                    f"Unexpected source URL: {doc_id}"
                )

            resource = db.get(
                Resource, doc.resource_id,
                with_for_update=args.apply,
            )

            if (
                resource is None
                or resource.resource_type != "PROGRAM"
                or resource.canonical_url != doc.source_url
            ):
                raise RuntimeError(
                    f"Resource identity mismatch: {doc_id}"
                )

            detail = db.get(
                ProgramDetail, resource.resource_id
            )
            if detail is None:
                raise RuntimeError(
                    f"Missing ProgramDetail: {doc_id}"
                )

            meta = dict(doc.raw_metadata or {})
            body = row["body"]

            if meta.get("program_review_approved") is True:
                current_digest = hashlib.sha256(
                    (doc.content or "").encode("utf-8")
                ).hexdigest()

                if current_digest != row["body_sha256"]:
                    raise RuntimeError(
                        f"Approved body changed: {doc_id}"
                    )

                approved += 1
                continue

            if row["review_decision"].strip().upper() == "APPROVE":
                raise RuntimeError(
                    f"Approved in CSV but not DB: {doc_id}"
                )

            existing = (doc.content or "").strip()
            if (
                len(existing) >= 80
                and existing != (doc.title or "").strip()
            ):
                preserved += 1
                continue

            updates.append((doc, resource, detail, meta, body))

            plan_parts.append(
                f"{doc_id}|{row['body_sha256']}|"
                f"{doc.content_hash or ''}"
            )

        digest = hashlib.sha256(
            "\n".join(plan_parts).encode("utf-8")
        ).hexdigest()[:16]

        print("[BULK RECOVERY PLAN]")
        print("Candidates:", len(candidates))
        print("Already approved:", approved)
        print("Existing substantial bodies preserved:", preserved)
        print("Proposed updates:", len(updates))
        print("Plan digest:", digest)
        print("First IDs:", [
            item[0].source_document_id
            for item in updates[:12]
        ])

        if args.apply:
            backup = Path(args.backup_file or "")
            if (
                not backup.is_file()
                or backup.suffix.lower() != ".dump"
                or backup.stat().st_size < 1024
            ):
                raise RuntimeError("Valid backup file required")

            if (
                args.expected_updates != len(updates)
                or args.plan_digest != digest
                or not updates
            ):
                raise RuntimeError(
                    "Plan confirmation mismatch; rollback"
                )

            for doc, resource, detail, meta, body in updates:
                meta.update({
                    "body_repaired": True,
                    "body_text_available": True,
                    "content_extracted": True,
                    "body_quality_status": "BODY_REVIEW",
                    "body_recovery_method": "bulk_review_csv_v1",
                    "program_review_approved": False,
                    "content_review_status": "RECOVERED_UNREVIEWED",
                })

                doc.content = body
                doc.content_hash = hashlib.sha256(
                    body.encode("utf-8")
                ).hexdigest()
                doc.validation_status = "PENDING"
                doc.evidence_eligible = False
                doc.raw_metadata = meta

                old_description = (
                    resource.description or ""
                ).strip()

                if (
                    len(old_description) < 80
                    or old_description == resource.name.strip()
                ):
                    resource.description = body[:1500]

                detail.raw_metadata = {
                    **(detail.raw_metadata or {}),
                    **meta,
                }

                # Do not change resource.is_active or dates.

            db.commit()
            print("[COMMITTED]", len(updates))
        else:
            db.rollback()
            print("[READ ONLY] No DB changes")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-file")
    parser.add_argument("--expected-updates", type=int)
    parser.add_argument("--plan-digest")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
