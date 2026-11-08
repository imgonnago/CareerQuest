from __future__ import annotations

import argparse
import csv
import hashlib
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from src.db.database import SessionLocal
from src.db.models import Resource, ProgramDetail


OUT = Path(
    "data/reports/program_repair/active_program_audit.csv"
)

CLOSED = {
    "\ubaa8\uc9d1\ub9c8\uac10",
    "\uc811\uc218\ub9c8\uac10",
}

SEOUL = ZoneInfo("Asia/Seoul")


def assess(resource, detail, now):
    metadata = (
        dict(detail.raw_metadata or {})
        if detail else {}
    )

    status = "".join(
        str(metadata.get("list_status") or "").split()
    )

    start = detail.application_start if detail else None
    end = detail.application_end if detail else None
    expires = resource.expires_at

    if status in CLOSED:
        return "CLOSED", "DEACTIVATE"

    deadlines = [
        value for value in (end, expires)
        if value is not None
    ]

    if (
        len(deadlines) == 2
        and min(deadlines) < now <= max(deadlines)
    ):
        return "CONFLICTING_DEADLINES", "REVIEW"

    if any(value < now for value in deadlines):
        return "EXPIRED", "DEACTIVATE"

    if start is not None and start > now:
        return "FUTURE_START", "REVIEW"

    if (
        start is not None
        and end is not None
        and start <= now <= end
    ):
        return "DATE_WINDOW_VALID", "KEEP"

    if status == "\uc0c1\uc2dc":
        return "ALWAYS_UNVERIFIED", "REVIEW"

    return "UNKNOWN_WINDOW", "REVIEW"


def iso(value):
    return value.isoformat() if value else ""


def run(apply=False, backup=False, expected_plan=None):
    if apply and (not backup or not expected_plan):
        raise SystemExit(
            "[STOP] Apply requires --backup-confirmed "
            "and --expected-plan"
        )

    now = datetime.now(SEOUL)
    report = []
    targets = []
    changes = []

    with SessionLocal() as db:
        if not apply:
            db.execute(text("SET TRANSACTION READ ONLY"))

        query = (
            select(Resource, ProgramDetail)
            .outerjoin(
                ProgramDetail,
                ProgramDetail.resource_id
                == Resource.resource_id,
            )
            .where(
                Resource.resource_type == "PROGRAM",
                Resource.is_active.is_(True),
            )
            .order_by(Resource.resource_id)
        )

        if apply:
            query = query.with_for_update(of=Resource)

        records = db.execute(query).all()

        for resource, detail in records:
            reason, action = assess(
                resource, detail, now
            )

            metadata = (
                detail.raw_metadata or {}
                if detail else {}
            )

            row = {
                "resource_id": resource.resource_id,
                "title": resource.name,
                "organization": resource.organization or "",
                "application_start": iso(
                    detail.application_start
                    if detail else None
                ),
                "application_end": iso(
                    detail.application_end
                    if detail else None
                ),
                "expires_at": iso(resource.expires_at),
                "list_status": metadata.get(
                    "list_status", ""
                ),
                "reason": reason,
                "proposed_action": action,
            }

            report.append(row)

            if action == "DEACTIVATE":
                targets.append(resource)
                changes.append(
                    f"{resource.resource_id}|"
                    f"{reason}|"
                    f"{row['application_end']}|"
                    f"{row['expires_at']}|"
                    f"{row['list_status']}"
                )

        signature = hashlib.sha256(
            "\n".join(changes).encode("utf-8")
        ).hexdigest()[:16]

        counts = Counter(
            (row["reason"], row["proposed_action"])
            for row in report
        )

        print("\n[PROGRAM ACTIVITY AUDIT]")
        print("Active resources:", len(report))

        for (reason, action), count in sorted(
            counts.items()
        ):
            print(f"{reason:28} {action:12} {count}")

        print("\n[DEACTIVATION CANDIDATES]", len(targets))

        for resource in targets:
            print(
                resource.resource_id,
                resource.name[:90],
            )

        print("[PLAN DIGEST]", signature)

        if apply:
            if expected_plan != signature:
                raise RuntimeError(
                    "Plan changed; no data committed"
                )

            if not targets:
                raise RuntimeError(
                    "No changes to apply"
                )

            for resource in targets:
                resource.is_active = False

            db.commit()
            print("[COMMITTED]", len(targets))
        else:
            db.rollback()
            print("[READ ONLY] No DB changes")

    if not apply:
        OUT.parent.mkdir(
            parents=True, exist_ok=True
        )

        fields = [
            "resource_id",
            "title",
            "organization",
            "application_start",
            "application_end",
            "expires_at",
            "list_status",
            "reason",
            "proposed_action",
        ]

        with OUT.open(
            "w", encoding="utf-8-sig", newline=""
        ) as f:
            writer = csv.DictWriter(
                f, fieldnames=fields
            )
            writer.writeheader()
            writer.writerows(report)

        print("[CSV]", OUT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true"
    )
    parser.add_argument(
        "--backup-confirmed", action="store_true"
    )
    parser.add_argument("--expected-plan")
    args = parser.parse_args()

    run(
        apply=args.apply,
        backup=args.backup_confirmed,
        expected_plan=args.expected_plan,
    )


if __name__ == "__main__":
    main()
