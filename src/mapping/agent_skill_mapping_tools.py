"""CareerQuest Skill Mapping Agent tools (backend adapter, no LLM runtime).

The existing proposal service remains the ONLY authority for validating and
submitting permanent ResourceSkill mapping proposals. This module never approves
or applies proposals. Queue submission is disabled by default and can only
create PENDING proposals, with an explicit trusted host-side opt-in.

Model-team integration later: expose TOOL_DEFINITIONS to their agent, and route
calls through dispatch_tool(). Do not let model arguments toggle allow_submit.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any, Callable, Mapping


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_skill_candidates",
            "description": "Look up existing canonical Skill IDs; lexical candidates are NOT evidence of correctness.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Skill name or alias"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_skill_mapping",
            "description": "Read-only DB validation of a ResourceSkill proposal. Never approves or saves the mapping.",
            "parameters": {
                "type": "object",
                "properties": {"candidate": {"type": "object"}},
                "required": ["candidate"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_skill_proposal",
            "description": "Opt-in only: submit an already validated candidate to a HUMAN review queue (PENDING). No approval or ResourceSkill write.",
            "parameters": {
                "type": "object",
                "properties": {"candidate": {"type": "object"}},
                "required": ["candidate"],
                "additionalProperties": False,
            },
        },
    },
]


class AgentToolConfigurationError(RuntimeError):
    """An unsafe or incomplete host-side tool configuration."""


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def rank_skill_candidates(query: str, entries: list[dict], *, limit: int = 5) -> list[dict]:
    """Lexical candidates only; a similar name is never automatically mapped.

    Entries are {skill_id, name, aliases:[...]}. This pure function is testable
    without a DB, and the actual adapter uses DB skill + skill_alias rows.
    """
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
        raise ValueError("query must be 1..200 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
        raise ValueError("limit must be in 1..5")
    q = _norm(query)
    scored: list[dict] = []
    for item in entries:
        options = [str(item["name"]), *[str(a) for a in item.get("aliases", [])]]
        best = (0, 0.0, "")
        for term in options:
            t = _norm(term)
            if t == q:
                match = (3, 1.0, term)
            elif len(q) >= 3 and (t.startswith(q) or q.startswith(t)):
                match = (2, 0.80, term)
            elif len(q) >= 4 and len(t) >= 4:
                similarity = SequenceMatcher(None, q, t).ratio()
                match = (1, similarity, term) if similarity >= 0.68 else (0, 0.0, term)
            else:
                match = (0, 0.0, term)
            if match[:2] > best[:2]:
                best = match
        if best[0]:
            scored.append({
                "skill_id": int(item["skill_id"]),
                "name": str(item["name"]),
                "matched_term": best[2],
                "match_type": {3: "EXACT", 2: "PREFIX", 1: "LEXICAL_SIMILAR"}[best[0]],
                "lexical_score": round(best[1], 3),
            })
    scored.sort(key=lambda e: (-{"EXACT": 3, "PREFIX": 2, "LEXICAL_SIMILAR": 1}[e["match_type"]],
                               -e["lexical_score"], e["skill_id"]))
    return scored[:limit]


def _load_skill_entries() -> list[dict]:
    from sqlalchemy import text
    from src.db.database import engine
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT s.skill_id, s.name, sa.alias
              FROM skill s LEFT JOIN skill_alias sa ON sa.skill_id = s.skill_id
             WHERE s.is_active = TRUE ORDER BY s.skill_id
        """)).mappings().all()
    grouped: dict[int, dict] = {}
    for row in rows:
        sid = int(row["skill_id"])
        item = grouped.setdefault(sid, {"skill_id": sid, "name": row["name"], "aliases": []})
        if row["alias"] and row["alias"] not in item["aliases"]:
            item["aliases"].append(row["alias"])
    return list(grouped.values())


def failure_route(message: str) -> tuple[str, str]:
    """Conservative routing of a backend validation error, never auto-approve.

    RETRY means the *agent may propose a modified candidate* later, not that
    the backend silently edits it. A repeated failure must obey an attempt cap.
    """
    m = message.lower()
    if "stale" in m or "sha256 differs" in m:
        return "STALE_SOURCE", "BLOCKED"
    if "not directly owned" in m or "not evidence-eligible" in m:
        return "SOURCE_NOT_ELIGIBLE", "BLOCKED"
    if "does not exist or is inactive" in m and "resource" in m:
        return "RESOURCE_INACTIVE", "BLOCKED"
    if "already linked" in m or "already exists" in m or "conflict" in m:
        return "MAPPING_CONFLICT", "BLOCKED"
    if "verified resourceskill" in m or "modified" in m or "snapshot" in m:
        return "EXISTING_MAPPING_CHANGED", "BLOCKED"
    if "does not exist or is inactive" in m and "skill" in m:
        return "INVALID_SKILL_ID", "RETRY"
    if "exact substring" in m or "evidence_text" in m:
        return "INVALID_EVIDENCE_QUOTE", "RETRY"
    if "invalid" in m or "must be" in m or "required" in m or "unexpected" in m:
        return "INVALID_CANDIDATE", "RETRY"
    return "VALIDATION_REJECTED", "BLOCKED"


class SkillMappingAgentTools:
    """Actual DB-backed implementations of the three future agent tools.

    By default these are READ-ONLY. Model cannot enable writes with arguments.
    submit_skill_proposal needs trusted host config and creates PENDING only.
    """

    def __init__(self, *, allow_submit: bool = False, submit_actor: str | None = None,
                 load_entries: Callable[[], list[dict]] = _load_skill_entries,
                 preview_func: Callable[[Any], dict] | None = None,
                 submit_func: Callable[..., tuple[int, str]] | None = None):
        self.allow_submit = bool(allow_submit)
        self.submit_actor = submit_actor
        self._load_entries = load_entries
        self._preview_func = preview_func
        self._submit_func = submit_func
        if self.allow_submit and not (isinstance(submit_actor, str) and submit_actor.strip()):
            raise AgentToolConfigurationError("submit_actor must be supplied by trusted host")

    def lookup_skill_candidates(self, query: str, limit: int = 5) -> dict:
        matches = rank_skill_candidates(query, self._load_entries(), limit=limit)
        return {"status": "OK", "candidates": matches,
                "note": "Lexical candidates only; never infer a canonical ID from similarity alone."}

    def validate_skill_mapping(self, candidate: Mapping[str, Any]) -> dict:
        from src.mapping.resource_skill_proposal_service import Candidate, ReviewError, preview
        try:
            parsed = Candidate.from_json_dict(candidate)
            (self._preview_func or preview)(parsed)
        except ReviewError as e:
            code, route = failure_route(str(e))
            return {"validation_status": "FAILED", "validation_code": code,
                    "route": route, "approved": False, "resource_skill_changed": False}
        except Exception:
            # Database outage or unexpected exception: fail closed; never
            # pass internal SQL, credentials, or raw evidence to an LLM tool.
            return {"validation_status": "FAILED", "validation_code": "VALIDATOR_UNAVAILABLE",
                    "route": "BLOCKED", "approved": False, "resource_skill_changed": False}
        return {"validation_status": "PASSED", "validation_code": "PASS",
                "route": "HUMAN_REVIEW", "approved": False,
                "resource_skill_changed": False}

    def submit_skill_proposal(self, candidate: Mapping[str, Any]) -> dict:
        # No model-supplied field can turn this on. Integration must opt in.
        if not self.allow_submit:
            return {"status": "DISABLED", "code": "HOST_WRITE_GATE_OFF",
                    "approved": False, "resource_skill_changed": False}
        from src.mapping.resource_skill_proposal_service import Candidate, submit
        parsed = Candidate.from_json_dict(candidate)
        # Existing submit() performs validation under row locks. Do NOT
        # pre-preview here: exact APPLIED/REJECTED retry acknowledgements are
        # intentionally idempotent, even after the underlying source changes.
        pid, status = (self._submit_func or submit)(parsed, actor=self.submit_actor)
        return {"status": status, "proposal_id": pid, "approved": False,
                "resource_skill_changed": False}

    def dispatch_tool(self, name: str, arguments: Mapping[str, Any]) -> dict:
        # Deny surprising parameters, including model attempts to pass actor
        # or allow_submit. Tool selection remains under the caller/agent.
        allowed = {
            "lookup_skill_candidates": {"query", "limit"},
            "validate_skill_mapping": {"candidate"},
            "submit_skill_proposal": {"candidate"},
        }
        if name not in allowed:
            return {"status": "REJECTED", "code": "UNKNOWN_TOOL"}
        if not isinstance(arguments, Mapping) or (set(arguments) - allowed[name]):
            return {"status": "REJECTED", "code": "UNEXPECTED_TOOL_ARGUMENT"}
        if name == "lookup_skill_candidates":
            return self.lookup_skill_candidates(arguments.get("query"), arguments.get("limit", 5))
        if name == "validate_skill_mapping":
            return self.validate_skill_mapping(arguments.get("candidate"))
        return self.submit_skill_proposal(arguments.get("candidate"))
