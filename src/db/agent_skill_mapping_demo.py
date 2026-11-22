"""Offline scripted Skill Mapping Agent demo; no LLM or DB writes by default.

Usage:
  python -m src.db.agent_skill_mapping_demo
  python -m src.db.agent_skill_mapping_demo --lookup Python
  python -m src.db.agent_skill_mapping_demo --preview-json candidate.json
  python -m src.db.agent_skill_mapping_demo --record-mock
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.mapping.agent_skill_mapping_flow import run_candidate_flow
from src.mapping.agent_skill_mapping_tools import SkillMappingAgentTools


class ScriptedMockTools:
    """Self-contained in-memory fixture. These are NOT real PostgreSQL IDs."""
    def lookup_skill_candidates(self, query: str, limit: int = 5) -> dict:
        return {"status": "OK", "candidates": [{"skill_id": 101, "name": "Python", "match_type": "EXACT"}]}

    def validate_skill_mapping(self, candidate: dict) -> dict:
        if candidate["skill_id"] != 101:
            return {"validation_status": "FAILED", "validation_code": "INVALID_SKILL_ID",
                    "route": "RETRY", "approved": False}
        return {"validation_status": "PASSED", "validation_code": "PASS",
                "route": "HUMAN_REVIEW", "approved": False}


def scripted_provider(attempt_no: int, last_feedback: str | None) -> dict:
    # Hardcoded demonstration: in future the MODEL agent would choose the
    # corrected candidate after reading feedback; this script does NOT.
    skill_id = 999999 if attempt_no == 1 else 101
    return {"skill_query": "Python", "candidate": {
        "action": "ADD", "resource_id": 202, "skill_id": skill_id,
        "relation_type": "REQUIRED", "source_document_id": 303,
        "source_sha256": "a" * 64,
        "evidence_text": "Python required", "confidence": "0.92",
        "producer": "scripted-mock",
    }}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    action = p.add_mutually_exclusive_group()
    action.add_argument("--lookup", metavar="SKILL", help="Read actual skill/alias DB, no writes")
    action.add_argument("--preview-json", type=Path,
                        help="Validate candidate against actual DB, read-only")
    action.add_argument("--record-mock", action="store_true",
                        help="Explicitly persist fake trace to agent tables only; asks confirmation")
    args = p.parse_args()

    if args.lookup is not None:
        value = SkillMappingAgentTools().lookup_skill_candidates(args.lookup)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        print("[READ ONLY] Skill dictionary lookup; not model-run")
        return
    if args.preview_json is not None:
        data = json.loads(args.preview_json.read_text(encoding="utf-8-sig"))
        result = SkillMappingAgentTools().validate_skill_mapping(data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("[READ ONLY] Did not queue, approve, or apply")
        return

    trace = run_candidate_flow(scripted_provider, ScriptedMockTools(), max_attempts=3)
    print(json.dumps(trace.to_dict(), ensure_ascii=False, indent=2))
    print("[MOCK] Scripted candidates only. No LLM/model execution.")
    print("[SAFETY] HUMAN_REVIEW means routing intent only; NO proposal has been submitted.")
    if args.record_mock:
        from src.db.database import engine
        if str(engine.url.host).lower() not in {"localhost", "127.0.0.1", "::1"}:
            raise SystemExit("[BLOCK] Local DB required")
        with engine.connect() as c:
            db = c.exec_driver_sql("SELECT current_database()").scalar_one()
        if db != "careerquest":
            raise SystemExit("[BLOCK] Only careerquest database")
        token = "RECORD_MOCK_AGENT_TRACE_ON_careerquest"
        if input(f"Type {token} to insert MOCK trace in NEW agent tables ONLY: ").strip() != token:
            raise SystemExit("[CANCEL] No DB writes")
        from src.db.agent_skill_mapping_shadow_record import record_mock_trace
        print("[RECORDED]", json.dumps(record_mock_trace(trace), ensure_ascii=False))
    else:
        print("[NO DB WRITES] Add --record-mock only if you explicitly want fake data in agent tables.")


if __name__ == "__main__":
    main()
