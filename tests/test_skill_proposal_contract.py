"""Offline tests. CQ_OFFLINE_TEST=1 injects a DB stub; never connects to Postgres."""
import importlib
import os
from pathlib import Path
import sys
import types
import unittest
from decimal import Decimal

if os.environ.get("CQ_OFFLINE_TEST") == "1":
    fake_db = types.ModuleType("src.db.database")
    fake_db.engine = object()
    sys.modules["src.db.database"] = fake_db

from src.mapping.resource_skill_proposal_service import (
    Candidate, ReviewError, check_candidate, digest, existing_snapshot, submission_key,
)
from src.db.migrate_resource_skill_proposals import statements


class FakeResult:
    def __init__(self, rows):
        self.rows = rows
    def mappings(self): return self
    def first(self): return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, *, content, old=None, valid=True):
        self.content = content
        self.old = old
        self.valid = valid
    def execute(self, statement, params):
        sql = str(statement)
        if "FROM resource\n" in sql:
            return FakeResult([{"resource_id":212,"resource_type":"JOB", "is_active":self.valid}])
        if "FROM skill" in sql:
            return FakeResult([{"skill_id":23,"is_active":True}])
        if "FROM source_document" in sql:
            return FakeResult([{
                "source_document_id": 100,"resource_id":212,
                "content":self.content,"validation_status":"ACCEPTED",
                "evidence_eligible":True,
            }])
        if "FROM resource_skill" in sql:
            if "WHERE resource_skill_id=:mid" in sql:
                return FakeResult([self.old] if self.old else [])
            return FakeResult([])
        raise AssertionError("Unknown query: "+sql)


def data(**updates):
    d = dict(action="ADD", resource_id=212, skill_id=23,
         relation_type="REQUIRED", source_document_id=100,
         source_sha256=digest("Python is required for this job."),
         evidence_text="Python is required", confidence="0.98000", producer="model-team")
    d.update(updates)
    return d


class CandidateTests(unittest.TestCase):
    def test_new_accepted(self):
        c=Candidate.from_json_dict(data())
        self.assertEqual(c.confidence, Decimal("0.98000"))
    def test_missing_or_invalid_ids(self):
        for invalid in (-1, 0, True, None, "23"):
            with self.subTest(invalid=invalid), self.assertRaises(ReviewError):
                Candidate.from_json_dict(data(skill_id=invalid))
    def test_sha_required(self):
        with self.assertRaises(ReviewError): Candidate.from_json_dict(data(source_sha256="123"))
    def test_no_delete(self):
        with self.assertRaises(ReviewError): Candidate.from_json_dict(data(action="REMOVE"))
    def test_extra_keys_blocked(self):
        with self.assertRaises(ReviewError): Candidate.from_json_dict(data(verified=True))
    def test_float_bad_nan(self):
        for invalid in ("NaN", "Infinity", -0.1, 1.1, True, None):
            with self.subTest(invalid=invalid), self.assertRaises(ReviewError):
                Candidate.from_json_dict(data(confidence=invalid))
    def test_precision_restricted(self):
        with self.assertRaises(ReviewError): Candidate.from_json_dict(data(confidence="0.123456"))
    def test_existing_id_needed(self):
        with self.assertRaises(ReviewError): Candidate.from_json_dict(data(action="CONFIRM"))
        with self.assertRaises(ReviewError): Candidate.from_json_dict(data(existing_resource_skill_id=11))
    def test_quote_required(self):
        with self.assertRaises(ReviewError): Candidate.from_json_dict(data(evidence_text=""))
    def test_consistent_key(self):
        c=Candidate.from_json_dict(data())
        self.assertEqual(submission_key(c,None), submission_key(c,None))
        self.assertNotEqual(submission_key(c,None), submission_key(c,"a"*64))


class DatabaseBoundaryTests(unittest.TestCase):
    def test_good_eligible_doc_exact_quote(self):
        c=Candidate.from_json_dict(data())
        check=check_candidate(FakeConn(content="Python is required for this job."), c)
        self.assertEqual(check["original_relation_type"],None)
    def test_wrong_quote_block(self):
        c=Candidate.from_json_dict(data(evidence_text="Java is required"))
        with self.assertRaises(ReviewError):
            check_candidate(FakeConn(content="Python is required for this job."),c)
    def test_doc_changed_block(self):
        c=Candidate.from_json_dict(data())
        with self.assertRaises(ReviewError):
            check_candidate(FakeConn(content="Python is not required for this job."), c)
    def test_wrong_resource_relation(self):
        c=Candidate.from_json_dict(data(relation_type="TAUGHT"))
        with self.assertRaises(ReviewError):
            check_candidate(FakeConn(content="Python is required for this job."), c)
    def test_inactive_resource_blocked(self):
        with self.assertRaises(ReviewError):
            check_candidate(FakeConn(content="Python is required for this job.",valid=False), Candidate.from_json_dict(data()))
    def test_existing_snapshot_changes(self):
        row=dict(resource_skill_id=81,resource_id=212,skill_id=23,
            relation_type="REQUIRED",evidence_text="Python is required",
            mapping_method="literal_job_section_v1",confidence=Decimal("0.98000"),
            verified=False,source_document_id=100)
        old=existing_snapshot(row)
        self.assertNotEqual(old, existing_snapshot(dict(row, verified=True)))
        self.assertNotEqual(old, existing_snapshot(dict(row, relation_type="PREFERRED")))
        self.assertNotEqual(old, existing_snapshot(dict(row, evidence_text="different")))
    def test_migration_has_five_statements(self):
        path = Path(__file__).resolve().parents[1]/"src/db/migrations/20261120_resource_skill_proposal.sql"
        sql=path.read_text(encoding="utf-8")
        self.assertEqual(len(statements(sql)),5)
        self.assertIn("CREATE TABLE resource_skill_proposal", sql)
        self.assertIn("CREATE TABLE resource_skill_proposal_event", sql)
        self.assertNotIn("DELETE FROM resource_skill",sql)
        self.assertNotIn("UPDATE resource_skill",sql)


if __name__ == "__main__": unittest.main()
