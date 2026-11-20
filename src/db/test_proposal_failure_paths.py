"""Live PostgreSQL negative-path smoke test; ALWAYS ROLLBACK every write.

Local development only. Sequence counters may still advance.
"""
from __future__ import annotations

import argparse
from dataclasses import replace

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.db.database import engine
from src.db.test_proposal_transaction_rollback import (
    OuterTransactionFacade, choose_synthetic_candidate, counts,
)
import src.mapping.resource_skill_proposal_service as svc


def blocked(fn, reason):
    try:
        fn()
    except svc.ReviewError as exc:
        print(f"[PASS] {reason}: {exc}")
    else:
        raise AssertionError(f"Expected ReviewError: {reason}")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--rollback-test", action="store_true")
    args=p.parse_args()
    if not args.rollback_test:
        print("[PREVIEW] Use --rollback-test on local dev DB; no DB operations")
        return

    if str(engine.url.host).lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("[BLOCK] Rollback test requires localhost PostgreSQL (not remote/cloud)")

    with engine.connect() as conn:
        name=conn.execute(text("SELECT current_database() ")).scalar_one()
        if name != "careerquest":
            raise SystemExit("[BLOCK] Only careerquest local DB supported")
        token = "NEGATIVE_TEST_ON_careerquest"
        if input(f"Type {token}: ").strip() != token:
            raise SystemExit("[CANCEL] No writes")
        before=counts(conn)
        c=choose_synthetic_candidate(conn)
        conn.rollback()

        orig=svc.engine
        outer=conn.begin()
        try:
            svc.engine=OuterTransactionFacade(conn)
            blocked(lambda: svc.preview(replace(c, source_sha256="b" * 64)),
                    "Invalid source hash")
            blocked(lambda: svc.preview(replace(c, relation_type="EXPERIENCED")),
                    "Invalid Resource relation")
            pid,state=svc.submit(c, actor="ROLLBACK_NEGATIVE_TEST")
            assert state == "PENDING"
            assert svc.submit(c, actor="ROLLBACK_NEGATIVE_TEST") == (pid, "PENDING")
            print("[PASS] Repeated PENDING submission does not duplicate")

            different_quote=c.evidence_text[:max(4,len(c.evidence_text)-1)]
            if different_quote == c.evidence_text:
                different_quote=c.evidence_text[1:]
            blocked(lambda: svc.submit(replace(c,evidence_text=different_quote), actor="X"),
                    "Different active proposal conflict")
            blocked(lambda: svc.apply(pid, actor="X"), "Apply before approval")
            blocked(lambda: svc.review(pid, approve=True, actor="X", note="no"),
                    "Short review note")

            # Temporarily alter the source inside a nested savepoint, then undo it.
            tamper=conn.begin_nested()
            try:
                conn.execute(text("""
                    UPDATE source_document SET content = content || ' TEMP_CHANGED'
                    WHERE source_document_id=:did
                """), {"did":c.source_document_id})
                blocked(lambda: svc.review(pid, approve=True, actor="HUMAN_TEST",
                                           note="Simulated review of source for rollback test"),
                        "Stale source blocks approval")
            finally:
                tamper.rollback()

            assert svc.review(pid, approve=True, actor="HUMAN_TEST",
                              note="Simulated approval for rollback-only test") == "APPROVED"
            blocked(lambda: svc.review(pid, approve=True, actor="HUMAN_TEST",
                                       note="Repeated approval request"),
                    "Repeated review blocked")
            mid=svc.apply(pid, actor="APPLY_TEST")
            assert svc.apply(pid,actor="APPLY_TEST")==mid
            assert svc.submit(c,actor="ROLLBACK_NEGATIVE_TEST")== (pid,"APPLIED")
            record=svc.status(pid)
            assert [e["event_type"] for e in record["events"]] == [
                "SUBMITTED", "APPROVED", "APPLIED"
            ]
            print("[PASS] Idempotent APPLIED retry; audit remains 3 events")
            now=counts(conn)
            assert now == (before[0]+1,before[1]+1,before[2]+3), (before,now)
            print("[PASS] Exact single mapping/proposal and three audit events")
        finally:
            try: outer.rollback()
            finally:
                svc.engine=orig
                print("[ROLLBACK] All writes in test outer transaction reversed")

        after=counts(conn)
        assert after==before, (before,after)
        print("[PASS] No persisted changes to 3 tables",after)

    # Independent transaction / row-lock check; only SELECT FOR UPDATE, no writes.
    with engine.connect() as a, engine.connect() as b:
        try:
            a.execute(text("SELECT resource_id FROM resource WHERE resource_id=:rid FOR UPDATE"),
                      {"rid":c.resource_id})
            b.execute(text("SET LOCAL lock_timeout = '200ms'"))
            try:
                b.execute(text("SELECT resource_id FROM resource WHERE resource_id=:rid FOR UPDATE"),
                          {"rid":c.resource_id})
            except DBAPIError as exc:
                # PostgreSQL 55P03 = lock_not_available (lock_timeout)
                code=getattr(getattr(exc,"orig",None),"sqlstate",None)
                if code != "55P03":
                    raise
                print("[PASS] Competing transaction respects resource row lock")
            else:
                raise AssertionError("Second transaction unexpectedly acquired same row lock")
        finally:
            b.rollback()
            a.rollback()
    print("[PASS] Backend negative paths / retries / lock test finished")


if __name__ == "__main__": main()
