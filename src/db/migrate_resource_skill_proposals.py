"""Explicit, transactional migration. Existing ResourceSkill data is never changed."""
from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import text
from src.db.database import engine

SQL_PATH = Path(__file__).resolve().parent / "migrations" / "20261120_resource_skill_proposal.sql"
TABLES = ("resource_skill_proposal", "resource_skill_proposal_event")
EXPECTED_PROPOSAL_COLS = {
    "proposal_id", "submission_key", "resource_id", "skill_id", "source_document_id",
    "existing_resource_skill_id", "action", "relation_type", "original_relation_type",
    "original_snapshot_sha256", "evidence_text", "source_sha256", "confidence",
    "producer", "status", "submitted_by", "reviewed_by", "review_note", "reviewed_at",
    "applied_resource_skill_id", "applied_at", "created_at", "updated_at",
}


def statements(sql: str) -> list[str]:
    segments = sql.split("-- CQ_STATEMENT")
    return [x.strip() for x in segments[1:] if x.strip()]


def inspect(conn):
    found = {name: bool(conn.execute(
        text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{name}"}
    ).scalar_one()) for name in TABLES}
    if all(found.values()):
        for tbl, expected in (
            (TABLES[0], EXPECTED_PROPOSAL_COLS),
            (TABLES[1], {"event_id", "proposal_id", "event_type", "actor", "note", "created_at"}),
        ):
            columns = set(conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :tbl
            """), {"tbl": tbl}).scalars().all())
            missing = expected - columns
            if missing:
                raise RuntimeError(f"[STOP] Existing {tbl} schema missing columns: {sorted(missing)}")
        indexes = set(conn.execute(text("""
            SELECT indexname FROM pg_indexes
            WHERE schemaname='public' AND tablename=:tbl
        """), {"tbl":TABLES[0]}).scalars().all())
        if "uq_skill_proposal_active_per_skill" not in indexes:
            raise RuntimeError("[STOP] Missing active-proposal uniqueness index")
        checks = conn.execute(text("""
            SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c
            WHERE c.conrelid = to_regclass('public.resource_skill_proposal')
        """)).scalars().all()
        if not any("PENDING" in v and "APPROVED" in v and "REJECTED" in v for v in checks):
            raise RuntimeError("[STOP] Proposal lifecycle status constraint missing")
    elif any(found.values()):
        raise RuntimeError(f"[STOP] Partial schema found: {found}; manual inspection required")
    return found


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Create only the two new tables")
    args = p.parse_args()
    if not SQL_PATH.exists():
        raise SystemExit(f"Missing migration SQL: {SQL_PATH}")
    stmts = statements(SQL_PATH.read_text(encoding="utf-8"))
    if len(stmts) != 5:
        raise SystemExit("[STOP] Migration SQL layout differs from expected 5 statements")
    with engine.connect() as conn:
        found = inspect(conn)
        name = conn.execute(text("SELECT current_database()" )).scalar_one()
    print(f"[DB] {name} existing={found}")
    if all(found.values()):
        print("[ALREADY EXISTS] Nothing to migrate. No DB writes.")
        return
    if not args.apply:
        print("[PREVIEW] New tables resource_skill_proposal + resource_skill_proposal_event")
        print("[READ ONLY] No DB changes. Pass --apply for explicit DDL.")
        return
    confirm = input(f"Type CREATE_PROPOSAL_TABLES_ON_{name} to create the new tables: ")
    if confirm.strip() != f"CREATE_PROPOSAL_TABLES_ON_{name}":
        raise SystemExit("[CANCEL] No DB writes")
    with engine.begin() as conn:
        if any(inspect(conn).values()):
            raise RuntimeError("[STOP] Schema changed during migration; no writes committed")
        for stmt in stmts:
            conn.execute(text(stmt))
    with engine.connect() as conn:
        result = inspect(conn)
    print(f"[CREATED] {result}; existing resource_skill / student_skill unchanged")


if __name__ == "__main__":
    main()
