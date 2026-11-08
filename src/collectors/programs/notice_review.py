from __future__ import annotations

import argparse
import csv
import hashlib
from collections import Counter
from pathlib import Path

from src.collectors.programs.notice_quality import (
    classify_notice,
    classify_program_notice,
)

SOURCE = Path(
    "data/reports/program_repair/department_body_review.csv"
)
REVIEW = Path(
    "data/reports/program_repair/department_program_review.csv"
)

FIELDS = [
    "source_document_id",
    "title",
    "source_url",
    "body",
    "body_sha256",
    "quality",
    "relevance",
    "review_decision",
    "review_note",
]


def read_rows(path):
    with path.open(
        "r", encoding="utf-8-sig", newline=""
    ) as f:
        return list(csv.DictReader(f))


def prepare():
    if REVIEW.exists():
        raise SystemExit(
            "[STOP] Review CSV exists. Decisions preserved."
        )

    if not SOURCE.exists():
        raise SystemExit("[STOP] Source CSV not found")

    source_rows = read_rows(SOURCE)
    if not source_rows:
        raise SystemExit("[STOP] Source CSV is empty")

    prepared = []
    counts = Counter()

    for row in source_rows:
        body = row["body"]
        title = row["title"]
        quality = classify_notice(body, title)
        relevance = classify_program_notice(title)

        prepared.append({
            "source_document_id": row["source_document_id"],
            "title": title,
            "source_url": row["source_url"],
            "body": body,
            "body_sha256": hashlib.sha256(
                body.encode("utf-8")
            ).hexdigest(),
            "quality": quality,
            "relevance": relevance,
            "review_decision": "HOLD",
            "review_note": "",
        })

        counts[(quality, relevance)] += 1

    REVIEW.parent.mkdir(parents=True, exist_ok=True)

    with REVIEW.open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(prepared)

    print("[PREPARED]", REVIEW)
    print("TOTAL:", len(prepared))

    for (quality, relevance), count in sorted(
        counts.items()
    ):
        print(quality, relevance, count)

    print("[READ ONLY] DB unchanged")


def check():
    if not REVIEW.exists():
        raise SystemExit("[STOP] Run --prepare first")

    rows = read_rows(REVIEW)
    decisions = Counter()

    for row in rows:
        decision = row["review_decision"].strip().upper()
        if decision not in ("HOLD", "APPROVE", "REJECT", ""):
            raise ValueError(
                f"Invalid review decision: "
                f"{row['source_document_id']}"
            )

        digest = hashlib.sha256(
            row["body"].encode("utf-8")
        ).hexdigest()

        if digest != row["body_sha256"]:
            raise ValueError(
                f"Body modified: {row['source_document_id']}"
            )

        decisions[decision or "HOLD"] += 1

    print("[REVIEW CHECK] TOTAL:", len(rows))

    for name, count in sorted(decisions.items()):
        print(name, count)

    print("[READ ONLY] DB unchanged")



def set_decision(document_id, approve, note=None):
    rows = read_rows(REVIEW)
    matches = [
        r for r in rows
        if r["source_document_id"] == str(document_id)
    ]

    if len(matches) != 1:
        raise RuntimeError("Expected exactly one matching ID")

    row = matches[0]
    actual_hash = hashlib.sha256(
        row["body"].encode("utf-8")
    ).hexdigest()

    if actual_hash != row["body_sha256"]:
        raise RuntimeError("Body hash mismatch")

    if approve:
        if (
            classify_notice(row["body"], row["title"])
            != "BODY_REVIEW"
            or classify_program_notice(row["title"])
            != "PROGRAM_CANDIDATE"
        ):
            raise RuntimeError("Not eligible for approval")

        if not note or len(note.strip()) < 5:
            raise RuntimeError(
                "Approval requires a review note"
            )

        row["review_decision"] = "APPROVE"
        row["review_note"] = note.strip()
    else:
        row["review_decision"] = "HOLD"
        row["review_note"] = ""

    temp = REVIEW.with_suffix(".csv.tmp")

    with temp.open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=list(rows[0].keys())
        )
        writer.writeheader()
        writer.writerows(rows)

    temp.replace(REVIEW)

    print("[DECISION SAVED]", document_id)
    print("Decision:", row["review_decision"])
    print("[DB] No changes")


def process_approved(
    apply=False,
    backup_confirmed=False,
    expected_approved=None,
):
    from sqlalchemy import text
    from src.db.database import SessionLocal
    from src.db.models import (
        SourceDocument, Resource, ProgramDetail,
    )
    from src.loaders.common import sha256_text

    rows = read_rows(REVIEW)
    approved = []
    seen = set()

    for row in rows:
        doc_id = int(row["source_document_id"])

        if doc_id in seen:
            raise RuntimeError("Duplicate document ID")
        seen.add(doc_id)

        digest = hashlib.sha256(
            row["body"].encode("utf-8")
        ).hexdigest()

        if digest != row["body_sha256"]:
            raise RuntimeError(
                f"Body hash mismatch: {doc_id}"
            )

        decision = row["review_decision"].strip().upper()

        if decision not in ("HOLD", "APPROVE", "REJECT", ""):
            raise RuntimeError(
                f"Invalid decision: {doc_id}"
            )

        if decision != "APPROVE":
            continue

        if (
            classify_notice(row["body"], row["title"])
            != "BODY_REVIEW"
            or classify_program_notice(row["title"])
            != "PROGRAM_CANDIDATE"
        ):
            raise RuntimeError(
                f"Eligibility failed: {doc_id}"
            )

        if len(row["review_note"].strip()) < 5:
            raise RuntimeError(
                f"Review note missing: {doc_id}"
            )

        approved.append(row)

    print("[APPROVED]", len(approved))

    if apply:
        if not backup_confirmed:
            raise RuntimeError(
                "DB backup confirmation required"
            )

        if (
            expected_approved is None
            or expected_approved != len(approved)
            or len(approved) == 0
        ):
            raise RuntimeError(
                "Expected approval count mismatch"
            )

    if not approved:
        print("[NO CHANGES] No approved documents")
        return

    updated = 0
    already = 0

    with SessionLocal() as db:
        if not apply:
            db.execute(
                text("SET TRANSACTION READ ONLY")
            )

        for row in approved:
            doc_id = int(row["source_document_id"])
            body = row["body"]

            doc = db.get(
                SourceDocument,
                doc_id,
                with_for_update=apply,
            )

            if (
                doc is None
                or doc.source_name
                != "KONYANG_DEPARTMENT_PROGRAM_NOTICES"
                or doc.title != row["title"]
                or doc.source_url != row["source_url"]
            ):
                raise RuntimeError(
                    f"Source identity mismatch: {doc_id}"
                )

            metadata = dict(doc.raw_metadata or {})
            current = (doc.content or "").strip()

            if metadata.get("program_review_approved"):
                if sha256_text(doc.content) != sha256_text(body):
                    raise RuntimeError(
                        f"Previously approved body differs: {doc_id}"
                    )
                already += 1
                print("[ALREADY APPROVED]", doc_id)
                continue

            if (
                len(current) >= 80
                and current != (doc.title or "").strip()
                and not (
                    metadata.get("body_recovery_method")
                    == "bulk_review_csv_v1"
                    and current == body.strip()
                )
            ):
                raise RuntimeError(
                    f"Existing substantial body: {doc_id}"
                )

            if not doc.resource_id:
                raise RuntimeError(
                    f"Missing Resource ID: {doc_id}"
                )

            resource = db.get(
                Resource,
                doc.resource_id,
                with_for_update=apply,
            )

            if (
                resource is None
                or resource.resource_type != "PROGRAM"
                or resource.canonical_url != row["source_url"]
            ):
                raise RuntimeError(
                    f"Resource mismatch: {doc_id}"
                )

            detail = db.get(
                ProgramDetail, resource.resource_id
            )

            if detail is None:
                raise RuntimeError(
                    f"ProgramDetail missing: {doc_id}"
                )

            print(
                "[UPDATE]" if apply else "[WOULD UPDATE]",
                doc_id,
                row["title"][:75],
            )

            updated += 1

            if not apply:
                continue

            metadata.update({
                "program_review_approved": True,
                "program_review_version": "v1",
                "body_quality_status": "APPROVED",
                "body_text_available": True,
                "content_extracted": True,
                "review_note":
                    row["review_note"].strip()[:500],
            })

            doc.content = body
            doc.content_hash = sha256_text(body)
            doc.validation_status = "ACCEPTED"
            doc.evidence_eligible = True
            doc.raw_metadata = metadata

            description = (resource.description or "").strip()

            if (
                len(description) < 80
                or description == (resource.name or "").strip()
            ):
                resource.description = body[:1500]

            detail.raw_metadata = {
                **(detail.raw_metadata or {}),
                **metadata,
            }

            # Never modify resource.is_active here.
            # Content approval is not recruitment approval.

        if apply:
            db.commit()
            print("[COMMITTED]", updated)
        else:
            db.rollback()
            print("[READ ONLY] No DB changes")

    print("[ALREADY APPROVED]", already)
    print("[NEW UPDATES]", updated)




def extract_dates(body):
    import re
    from datetime import date

    pattern = (
        r"(?<![0-9])(20[0-9]{2})"
        r"[./-] *([0-9]{1,2})"
        r"[./-] *([0-9]{1,2})(?![0-9])"
    )

    result = set()

    for year, month, day in re.findall(
        pattern, body or ""
    ):
        try:
            result.add(
                date(int(year), int(month), int(day))
            )
        except ValueError:
            continue

    return sorted(result)


def export_shortlist():
    import re
    from datetime import date

    if not REVIEW.exists():
        raise SystemExit("[STOP] Review CSV missing")

    output = REVIEW.with_name(
        "department_program_shortlist.csv"
    )

    if output.exists():
        raise SystemExit(
            "[STOP] Shortlist already exists; "
            "existing file will not be overwritten"
        )

    rows = read_rows(REVIEW)
    shortlist = []
    today = date.today()

    for row in rows:
        if not (
            row["quality"] == "BODY_REVIEW"
            and row["relevance"] == "PROGRAM_CANDIDATE"
        ):
            continue

        dates = extract_dates(row["body"])

        if not dates:
            date_hint = "NO_DATE_FOUND"
        elif max(dates) < today:
            date_hint = "PAST_DATES_ONLY"
        else:
            date_hint = "DATES_REQUIRE_REVIEW"

        preview = re.sub(
            r"\\n|\\r|\\s+",
            " ",
            row["body"]
        ).strip()

        shortlist.append({
            "source_document_id":
                row["source_document_id"],
            "title": row["title"],
            "source_url": row["source_url"],
            "date_hint": date_hint,
            "dates_found": ", ".join(
                str(d) for d in dates[:15]
            ),
            "review_decision":
                row["review_decision"],
            "body_preview": preview[:700],
        })

    fields = [
        "source_document_id",
        "title",
        "source_url",
        "date_hint",
        "dates_found",
        "review_decision",
        "body_preview",
    ]

    with output.open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=fields
        )
        writer.writeheader()
        writer.writerows(shortlist)

    print("[SHORTLIST CREATED]", output)
    print("Candidates:", len(shortlist))

    from collections import Counter
    counts = Counter(
        row["date_hint"] for row in shortlist
    )

    for label, count in sorted(counts.items()):
        print(label, count)

    print("[READ ONLY] DB unchanged")
    print("[NOTE] Dates are hints, not recruitment status")


def show_document(document_id):
    if not REVIEW.exists():
        raise SystemExit("[STOP] Review CSV missing")

    matches = [
        row for row in read_rows(REVIEW)
        if row["source_document_id"] == str(document_id)
    ]

    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one matching document"
        )

    row = matches[0]

    print("[DOCUMENT]", document_id)
    print("TITLE:", row["title"])
    print("URL:", row["source_url"])
    print("QUALITY:", row["quality"])
    print("RELEVANCE:", row["relevance"])
    print("DECISION:", row["review_decision"])
    print("DATES:", [
        str(d) for d in extract_dates(row["body"])
    ])
    print("\\n[EXTRACTED BODY]\\n")
    print(row["body"][:12000])
    print("\\n[READ ONLY] DB unchanged")



def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(
        required=True
    )
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--approve-id", type=int)
    mode.add_argument("--hold-id", type=int)
    mode.add_argument("--show-id", type=int)
    mode.add_argument("--export-shortlist", action="store_true")

    parser.add_argument("--note")
    parser.add_argument(
        "--backup-confirmed", action="store_true"
    )
    parser.add_argument("--expected-approved", type=int)

    args = parser.parse_args()

    if args.prepare:
        prepare()
    elif args.check:
        check()
    elif args.approve_id is not None:
        set_decision(args.approve_id, True, args.note)
    elif args.hold_id is not None:
        set_decision(args.hold_id, False)
    elif args.show_id is not None:
        show_document(args.show_id)
    elif args.export_shortlist:
        export_shortlist()
    else:
        process_approved(
            apply=args.apply,
            backup_confirmed=args.backup_confirmed,
            expected_approved=args.expected_approved,
        )


if __name__ == "__main__":
    main()
