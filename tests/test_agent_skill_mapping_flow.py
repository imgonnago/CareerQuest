"""No PostgreSQL and no LLM: contract tests for Skill Mapping Agent adapter."""
from __future__ import annotations

import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from src.mapping.agent_skill_mapping_flow import run_candidate_flow
from src.mapping.agent_skill_mapping_tools import (
    AgentToolConfigurationError, SkillMappingAgentTools, failure_route,
    rank_skill_candidates,
)


@contextmanager
def mock_proposal_service():
    """Offline stand-in for an existing repo module not shipped in this add-on."""
    module = types.ModuleType("src.mapping.resource_skill_proposal_service")
    class FakeReviewError(ValueError):
        pass
    class FakeCandidate:
        @classmethod
        def from_json_dict(cls, payload):
            if not isinstance(payload, dict) or not isinstance(payload.get("skill_id"), int) or payload["skill_id"] <= 0:
                raise FakeReviewError("skill_id must be positive")
            return payload
    module.Candidate = FakeCandidate
    module.ReviewError = FakeReviewError
    module.preview = lambda c: {"submission_key": "mocked"}
    module.submit = lambda c, actor: (987, "PENDING")
    with patch.dict(sys.modules, {module.__name__: module}):
        yield


class FakeTools:
    def __init__(self, responses):
        self.responses = iter(responses)
    def lookup_skill_candidates(self, query, limit=5):
        return {"status": "OK", "candidates": []}
    def validate_skill_mapping(self, candidate):
        return next(self.responses)


def supplied(attempt_no, feedback):
    return {"skill_query": "Python", "candidate": {"relation_type": "REQUIRED"}}


class AgentFlowTests(unittest.TestCase):
    def test_lookup_exact_alias_and_lexical_are_only_candidates(self):
        entries = [
            {"skill_id": 1, "name": "Python", "aliases": ["파이썬"]},
            {"skill_id": 2, "name": "Java", "aliases": []},
        ]
        self.assertEqual(rank_skill_candidates("파이썬", entries)[0]["skill_id"], 1)
        self.assertEqual(rank_skill_candidates("Python", entries)[0]["match_type"], "EXACT")
        self.assertEqual(rank_skill_candidates("notfound-zzz", entries), [])

    def test_lookup_limit_rejects_boolean(self):
        with self.assertRaises(ValueError):
            rank_skill_candidates("Python", [], limit=True)

    def test_invalid_skill_routed_retry(self):
        self.assertEqual(failure_route("Skill does not exist or is inactive"),
                         ("INVALID_SKILL_ID", "RETRY"))

    def test_stale_source_is_blocked(self):
        self.assertEqual(failure_route("Stale source document: SHA256 differs"),
                         ("STALE_SOURCE", "BLOCKED"))

    def test_retry_then_human_review(self):
        t = FakeTools([
            {"validation_status": "FAILED", "validation_code": "INVALID_SKILL_ID", "route": "RETRY"},
            {"validation_status": "PASSED", "validation_code": "PASS", "route": "HUMAN_REVIEW"},
        ])
        observed = []
        def provider(attempt_no, feedback):
            observed.append((attempt_no, feedback))
            return supplied(attempt_no, feedback)
        r = run_candidate_flow(provider, t)
        self.assertEqual(r.route, "HUMAN_REVIEW")
        self.assertEqual(len(r.attempts), 2)
        self.assertEqual(len(r.tool_events), 4)
        self.assertEqual(observed, [(1, None), (2, "INVALID_SKILL_ID")])
        self.assertIsNone(r.queued_proposal_id)
        self.assertFalse(r.llm_connected)

    def test_retry_cap_fails_closed(self):
        t = FakeTools([{"validation_status": "FAILED", "validation_code": "INVALID_SKILL_ID", "route": "RETRY"}]*3)
        r = run_candidate_flow(supplied, t, max_attempts=2)
        self.assertEqual(len(r.attempts), 2)
        self.assertEqual(r.route, "BLOCKED")

    def test_backend_never_allows_auto_eligible_override(self):
        t = FakeTools([{"validation_status": "PASSED", "validation_code": "PASS", "route": "AUTO_ELIGIBLE"}])
        self.assertEqual(run_candidate_flow(supplied, t).route, "HUMAN_REVIEW")

    def test_unexpected_route_is_blocked(self):
        t = FakeTools([{"validation_status": "FAILED", "validation_code": "BAD", "route": "HUMAN_REVIEW"}])
        self.assertEqual(run_candidate_flow(supplied, t).route, "BLOCKED")

    def test_unknown_tool_rejected(self):
        t = SkillMappingAgentTools(load_entries=lambda: [])
        self.assertEqual(t.dispatch_tool("approve_skill_mapping", {})["code"], "UNKNOWN_TOOL")

    def test_model_cannot_toggle_write_gate(self):
        t = SkillMappingAgentTools(load_entries=lambda: [])
        self.assertEqual(t.dispatch_tool("submit_skill_proposal", {"candidate": {}, "allow_submit": True})["code"],
                         "UNEXPECTED_TOOL_ARGUMENT")
        self.assertEqual(t.dispatch_tool("submit_skill_proposal", {"candidate": {}})["code"],
                         "HOST_WRITE_GATE_OFF")

    def test_write_gate_requires_trusted_actor(self):
        with self.assertRaises(AgentToolConfigurationError):
            SkillMappingAgentTools(allow_submit=True)

    def test_live_validate_calls_service_contract_via_mock(self):
        # Candidate parser imported but DB query replaced. No database access.
        def validator(_parsed): return {"submission_key": "fake"}
        t = SkillMappingAgentTools(preview_func=validator, load_entries=lambda: [])
        payload = {"action": "ADD", "resource_id": 1, "skill_id": 2,
                   "relation_type": "REQUIRED", "source_document_id": 3,
                   "source_sha256": "a" * 64, "evidence_text": "Python required",
                   "confidence": "0.9", "producer": "mock"}
        with mock_proposal_service():
            result = t.validate_skill_mapping(payload)
        self.assertEqual(result["validation_status"], "PASSED")
        self.assertEqual(result["route"], "HUMAN_REVIEW")
        self.assertFalse(result["approved"])

    def test_explicitly_enabled_submission_remains_pending(self):
        t = SkillMappingAgentTools(allow_submit=True, submit_actor="trusted-demo-host",
                                  preview_func=lambda c: {"submission_key": "mock"})
        with mock_proposal_service():
            result = t.submit_skill_proposal({"skill_id": 123})
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["proposal_id"], 987)
        self.assertFalse(result["approved"])
        self.assertFalse(result["resource_skill_changed"])

    def test_invalid_payload_never_approves(self):
        t = SkillMappingAgentTools(load_entries=lambda: [])
        with mock_proposal_service():
            result = t.validate_skill_mapping({"skill_id": 0})
        self.assertEqual(result["validation_status"], "FAILED")
        self.assertFalse(result["approved"])


if __name__ == "__main__":
    unittest.main()
