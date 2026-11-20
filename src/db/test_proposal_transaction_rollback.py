"""CareerQuest live proposal lifecycle smoke test; ALL row changes rollback.

Usage: python -m src.db.test_proposal_transaction_rollback --rollback-test

Uses a real PostgreSQL transaction with nested savepoints, then unconditionally
rolls back the outer transaction. PostgreSQL sequences may still advance.
This is a BACKEND TRANSACTION test, not a semantic Skill mapping approval.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager

from sqlalchemy import text

from src.db.database import engine
import src.mapping.resource_skill_proposal_service as service


class OuterTransactionFacade:
    """Routes service engine.begin/connect through a single rollback-only tx."""
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def begin(self):
        with self.conn.begin_nested():
            yield self.conn

    @contextmanager
    def connect(self):
        yield self.conn


def choose_synthetic_candidate(conn):
    # No synthetic metadata is permanently added to the database.
    # Choose an active resource, its owned eligible document, and an unlinked Skill.
    row = conn.execute(text("""
        SELECT r.resource_id, r.resource_type,
               d.source_document_id, d.content, s.skill_id
        FROM resource r
        JOIN source_document d ON d.resource_id = r.resource_id
        JOIN LATERAL (
            SELECT sk.skill_id FROM skill sk
            WHERE sk.is_active = TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM resource_skill existing
                  WHERE existing.resource_id = r.resource_id
                    AND existing.skill_id = sk.skill_id
              )
            ORDER BY sk.skill_id
            LIMIT 1
        ) s ON TRUE
        WHERE r.is_active = TRUE
          AND r.resource_type IN ('JOB','COURSE','CERTIFICATE','PROGRAM')
          AND d.validation_status = 'ACCEPTED'
          AND d.evidence_eligible = TRUE
          AND length(d.content) >= 30
        ORDER BY r.resource_id, d.source_document_id
        LIMIT 1
    """)).mappings().first()
    if not row:
        raise RuntimeError("No eligible Resource/SourceDocument/free Skill test tuple")
    rel = {
        "JOB": "RELATED", "COURSE": "TAUGHT",
        "CERTIFICATE": "VERIFIED", "PROGRAM": "TAUGHT",
    }[row["resource_type"]]
    quote = (row["content"] or "").strip()[:80]
    if len(quote) < 4:
        raise RuntimeError("Document beginning too short for evidence quote")
    candidate = service.Candidate.from_json_dict({
        "action": "ADD", "resource_id": int(row["resource_id"]),
        "skill_id": int(row["skill_id"]), "relation_type": rel,
        "source_document_id": int(row["source_document_id"]),
        "source_sha256": service.digest(row["content"]),
        "evidence_text": quote,
        "confidence": "0.50000", "producer": "ROLLBACK_TEST_ONLY",
    })
    return candidate


def counts(conn):
    # The tables are fixed names, not user-controlled identifiers.
    return tuple(int(conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar_one())
                 for t in ("resource_skill", "resource_skill_proposal", "resource_skill_proposal_event"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rollback-test", action="store_true",
                        help="Explicitly test live PostgreSQL lifecycle and rollback")
    args = parser.parse_args()
    if not args.rollback_test:
        print("[PREVIEW] Requires --rollback-test; no DB connection made")
        return
    with engine.connect() as conn:
        name = conn.execute(text("SELECT current_database()")).scalar_one()
        print(f"[DATABASE] {name}")
        if name != "careerquest":
            raise SystemExit("[BLOCK] Expected local careerquest database")
        token = "ROLLBACK_TEST_ON_careerquest"
        if input(f"Type {token} to run a rollback-only backend test: ").strip() != token:
            raise SystemExit("[CANCEL] No writes")
        before = counts(conn)
        candidate = choose_synthetic_candidate(conn)
        # SELECT has opened a SQLAlchemy implicit transaction; close it before
        # starting our explicit outer transaction.
        conn.rollback()
        original_engine = service.engine
        outer = conn.begin()
        try:
            service.engine = OuterTransactionFacade(conn)
            service.preview(candidate)
            print("[PASS] Preview and live source/skill validation")
            pid, state = service.submit(candidate, actor="ROLLBACK_TEST")
            assert state == "PENDING"
            pid2, state2 = service.submit(candidate, actor="ROLLBACK_TEST")
            assert pid2 == pid and state2 == "PENDING"
            print("[PASS] Submit + repeated submit idempotency")
            state3 = service.review(pid, approve=True, actor="ROLLBACK_TEST",
                                    note="Simulated approval for rollback transaction only")
            assert state3 == "APPROVED"
            print("[PASS] PENDING to APPROVED transition")
            mid = service.apply(pid, actor="ROLLBACK_TEST")
            assert mid > 0
            again = service.apply(pid, actor="ROLLBACK_TEST")
            assert again == mid
            checked = service.status(pid)
            assert checked["status"] == "APPLIED"
            assert [e["event_type"] for e in checked["events"]] == [
                "SUBMITTED", "APPROVED", "APPLIED"
            ]
            print("[PASS] APPROVED to APPLIED + 3 audit events + repeat apply")
            current = counts(conn)
            assert current == (before[0] + 1, before[1] + 1, before[2] + 3), (
                before, current
            )
            print("[PASS] Writes isolated inside outer test transaction")
        finally:
            try:
                outer.rollback()  # No permanent rows, even on any test failure.
            finally:
                service.engine = original_engine
                print("[ROLLBACK] Test transaction reversed (sequence numbers may advance)")
        after = counts(conn)
        if after != before:
            raise AssertionError(f"Row counts changed after rollback: before={before}, after={after}")
        print("[PASS] 3 table row counts unchanged after rollback", after)
        print("[PASS] Backend transaction smoke test completed")


if __name__ == "__main__":
    main()
