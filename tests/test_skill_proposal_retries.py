"""Offline proposal retry tests; never touches PostgreSQL."""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

import src.mapping.resource_skill_proposal_service as svc


class Result:
    def __init__(self, row): self.row = row
    def mappings(self): return self
    def first(self): return self.row


class MemoryConnection:
    def __init__(self, active=None, past=None):
        self.active = active
        self.past = past
        self.queries = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.queries.append(sql)
        if "FROM resource_skill_proposal" in sql and "FOR UPDATE" in sql:
            return Result(self.active)
        if "FROM resource_skill_proposal" in sql and "ORDER BY proposal_id DESC" in sql:
            row = self.past
            if row is not None:
                match = (
                    row["resource_id"] == params["rid"]
                    and row["skill_id"] == params["sid"]
                    and row["action"] == params["action"]
                    and row["relation_type"] == params["rel"]
                    and row["source_document_id"] == params["did"]
                    and row["source_sha256"] == params["sha"]
                    and row["evidence_text"] == params["evidence"]
                    and row["confidence"] == params["confidence"]
                    and row["producer"] == params["producer"]
                    and row["existing_resource_skill_id"] == params["mid"]
                )
                row = row if match else None
            return Result(row)
        raise AssertionError("Unexpected SQL (must not write): " + sql)


class MemoryEngine:
    def __init__(self, conn): self.conn = conn
    @contextmanager
    def begin(self): yield self.conn


def candidate():
    return svc.Candidate.from_json_dict({
        "action": "ADD", "resource_id": 101, "skill_id": 15,
        "relation_type": "TAUGHT", "source_document_id": 201,
        "source_sha256": "a" * 64, "evidence_text": "Relevant skill explicitly taught",
        "confidence": "0.92000", "producer": "model-team",
    })


def saved(c, status="PENDING", pid=987):
    return {**{f: getattr(c, f) for f in (
        "action", "resource_id", "skill_id", "relation_type", "source_document_id",
        "source_sha256", "evidence_text", "confidence", "producer",
        "existing_resource_skill_id",
    )}, "status": status, "proposal_id": pid}


class RetryTests(unittest.TestCase):
    def test_same_pending_returns_existing_without_revalidation_or_event(self):
        c = candidate()
        conn = MemoryConnection(active=saved(c))
        with patch.object(svc, "engine", MemoryEngine(conn)):
            self.assertEqual(svc.submit(c, actor="reviewer"), (987, "PENDING"))
        self.assertEqual(len(conn.queries), 1)

    def test_same_approved_returns_existing_without_new_writes(self):
        c = candidate()
        conn = MemoryConnection(active=saved(c, "APPROVED", 51))
        with patch.object(svc, "engine", MemoryEngine(conn)):
            self.assertEqual(svc.submit(c, actor="reviewer"), (51, "APPROVED"))

    def test_conflicting_active_is_explicitly_blocked(self):
        c = candidate()
        other = saved(c)
        other["evidence_text"] = "Different source claim"
        conn = MemoryConnection(active=other)
        with patch.object(svc, "engine", MemoryEngine(conn)):
            with self.assertRaisesRegex(svc.ReviewError, "Another active proposal"):
                svc.submit(c, actor="user")
        self.assertEqual(len(conn.queries), 1)

    def test_same_applied_is_idempotent_after_resource_skill_changed(self):
        c = candidate()
        conn = MemoryConnection(past=saved(c, "APPLIED", 77))
        with patch.object(svc, "engine", MemoryEngine(conn)):
            self.assertEqual(svc.submit(c, actor="user"), (77, "APPLIED"))
        self.assertEqual(len(conn.queries), 2)

    def test_same_rejected_is_idempotent_without_new_approval(self):
        c = candidate()
        conn = MemoryConnection(past=saved(c, "REJECTED", 45))
        with patch.object(svc, "engine", MemoryEngine(conn)):
            self.assertEqual(svc.submit(c, actor="user"), (45, "REJECTED"))

    def test_history_mismatch_does_not_skip_live_validation(self):
        c = candidate()
        old = saved(c, "APPLIED")
        old["source_sha256"] = "b" * 64
        conn = MemoryConnection(past=old)
        with patch.object(svc, "engine", MemoryEngine(conn)), \
             patch.object(svc, "check_candidate", side_effect=svc.ReviewError("Live validation blocked")):
            with self.assertRaisesRegex(svc.ReviewError, "Live validation blocked"):
                svc.submit(c, actor="user")

    def test_actor_must_be_nonblank(self):
        with self.assertRaises(svc.ReviewError):
            svc.submit(candidate(), actor="")

    def test_precision_and_finite_check_still_enforced(self):
        c = candidate()
        self.assertEqual(c.confidence, Decimal("0.92000"))
        with self.assertRaises(svc.ReviewError):
            svc.Candidate.from_json_dict({
                **{f: getattr(c, f) for f in (
                    "action", "resource_id", "skill_id", "relation_type",
                    "source_document_id", "source_sha256", "evidence_text",
                    "producer", "existing_resource_skill_id")},
                "confidence": "NaN",
            })


if __name__ == "__main__": unittest.main()
