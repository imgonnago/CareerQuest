"""Persist a SCRIPTED/MOCK trace into the new agent tables ONLY.

Never called automatically. The caller must explicitly ask to record. Rejects
remote DB hosts, wrong DB names, and non-manual-review policy. Does NOT submit
ResourceSkill proposals, approve a mapping, or touch ResourceSkill itself.
"""
from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import text

from src.mapping.agent_skill_mapping_flow import FlowResult


class UnsafeRecording(RuntimeError):
    pass


def record_mock_trace(trace: FlowResult) -> dict:
    from src.db.database import engine
    if str(engine.url.host).lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise UnsafeRecording("Mock recording is restricted to local PostgreSQL")
    with engine.begin() as conn:
        db = conn.execute(text("SELECT current_database()" )).scalar_one()
        if db != "careerquest":
            raise UnsafeRecording("Mock recording only supported for local careerquest DB")
        policy = conn.execute(text("""
            SELECT allow_auto_approve, allow_auto_apply, default_route
              FROM agent_skill_mapping_policy WHERE policy_id = 1 FOR SHARE
        """)).mappings().first()
        if policy is None or policy["allow_auto_approve"] or policy["allow_auto_apply"] or policy["default_route"] != "HUMAN_REVIEW":
            raise UnsafeRecording("Expected manual_review policy, automatic approval/apply OFF")
        if not trace.simulated or trace.llm_connected or trace.permanent_mapping_changed or trace.queued_proposal_id is not None:
            raise UnsafeRecording("Only simulated no-write traces are supported")
        context = json.dumps({"scenario": "scripted_mock", "llm_connected": False,
                              "permanent_mapping_changed": False, "proposal_queued": False})
        run_id = conn.execute(text("""
            INSERT INTO agent_skill_mapping_run
                (correlation_key, agent_name, agent_version, source_kind,
                 mode, status, context_metadata, started_at, finished_at)
            VALUES (:corr, 'scripted_mock_adapter', 'preintegration_v1',
                    'MANUAL', 'SHADOW', 'COMPLETED', CAST(:meta AS jsonb), now(), now())
            RETURNING run_id
        """), {"corr": "MOCK-" + uuid4().hex, "meta": context}).scalar_one()
        attempt_ids: dict[int, int] = {}
        for attempt in trace.attempts:
            retry_parent = attempt_ids.get(attempt.retry_of_attempt_no)
            # Mock fixture uses fake IDs, so NEVER put them into Skill FK.
            aid = conn.execute(text("""
                INSERT INTO agent_skill_mapping_attempt
                    (run_id, attempt_no, retry_of_attempt_id, candidate_skill_id,
                     candidate_skill_name, relation_type, validation_status,
                     validation_codes, risk_level, route_decision, validated_at, routed_at)
                VALUES (:run,:seq,:retry,NULL,:name,:rel,:validation,
                        CAST(:codes AS jsonb),'UNKNOWN',:route,now(),now())
                RETURNING attempt_id
            """), {"run": run_id, "seq": attempt.attempt_no,
                    "retry": retry_parent, "name": attempt.skill_name[:300],
                    "rel": attempt.relation_type, "validation": attempt.validation_status,
                    "codes": json.dumps([attempt.validation_code]),
                    "route": attempt.route_decision}).scalar_one()
            attempt_ids[attempt.attempt_no] = int(aid)
        for e in trace.tool_events:
            conn.execute(text("""
                INSERT INTO agent_skill_mapping_tool_event
                    (run_id, attempt_id, event_seq, tool_name, decision_origin,
                     tool_status, result_code, event_metadata)
                VALUES (:run, :aid, :seq, :name, 'BACKEND', :status, :code,
                        CAST(:meta AS jsonb))
            """), {"run": run_id, "aid": attempt_ids[e.attempt_no],
                    "seq": e.event_seq, "name": e.tool_name,
                    "status": e.tool_status, "code": e.result_code,
                    "meta": json.dumps({"mock": True})})
    return {"run_id": int(run_id), "attempt_count": len(trace.attempts),
            "event_count": len(trace.tool_events), "llm_connected": False,
            "resource_skill_modified": False, "proposal_queued": False}
