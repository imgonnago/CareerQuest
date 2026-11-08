from __future__ import annotations

import csv
import html
import re
from collections import Counter
from pathlib import Path

SOURCE = Path(
    "data/reports/program_repair/department_body_review.csv"
)
OUTPUT = Path(
    "data/reports/program_repair/department_attachment_triage.csv"
)

FILE_MARKER = re.compile(
    r"atchFileId=|fileSn=|"
    r"\.hwp(?:x)?\b|\.pdf\b|\.zip\b|"
    r"\.png\b|\.jpe?g\b|\.docx?\b",
    re.I,
)


def inspect(body: str):
    raw = html.unescape(body or "")
    raw = raw.replace("\\r", "\n").replace("\\n", "\n")

    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in raw.splitlines()
    ]
    lines = [line for line in lines if line]

    file_lines = [
        line for line in lines
        if FILE_MARKER.search(line)
    ]
    text_lines = [
        line for line in lines
        if not FILE_MARKER.search(line)
    ]

    narrative = "\n".join(text_lines)
    tokens = re.findall(
        r"[가-힣A-Za-z0-9]{2,}", narrative
    )

    if len(narrative) >= 150 and len(tokens) >= 20:
        label = "BODY_AND_ATTACHMENTS"
    elif len(narrative) >= 80:
        label = "PARTIAL_BODY"
    else:
        label = "ATTACHMENT_FOCUSED"

    return label, narrative, len(file_lines)


def main():
    if OUTPUT.exists():
        raise SystemExit(
            "[STOP] Report exists; protecting previous review"
        )

    with SOURCE.open(
        encoding="utf-8-sig", newline=""
    ) as f:
        rows = list(csv.DictReader(f))

    targets = [
        row for row in rows
        if row["status"] == "ATTACHMENT_REVIEW"
    ]

    results = []
    counts = Counter()

    for row in targets:
        label, narrative, file_count = inspect(
            row["body"]
        )

        counts[label] += 1

        results.append({
            "source_document_id":
                row["source_document_id"],
            "title": row["title"],
            "source_url": row["source_url"],
            "triage": label,
            "original_chars": len(row["body"]),
            "non_file_chars": len(narrative),
            "file_reference_lines": file_count,
            "non_file_body": narrative,
            "review_decision": "HOLD",
        })

    fields = [
        "source_document_id", "title", "source_url",
        "triage", "original_chars", "non_file_chars",
        "file_reference_lines", "non_file_body",
        "review_decision",
    ]

    with OUTPUT.open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    print("\n[ATTACHMENT REVIEW]")
    print("Total:", len(targets))

    for label, count in sorted(counts.items()):
        print(label, count)

    print("\n[SAMPLES]")
    for row in results[:8]:
        print()
        print("ID:", row["source_document_id"])
        print("TITLE:", row["title"])
        print("TRIAGE:", row["triage"])
        print("TEXT:", repr(row["non_file_body"][:220]))

    print("\n[CSV]", OUTPUT)
    print("[READ ONLY] No DB changes")
    print("[NOTE] Triage is not content approval")


if __name__ == "__main__":
    main()
