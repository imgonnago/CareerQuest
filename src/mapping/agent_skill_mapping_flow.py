"""Deterministic, LLM-independent workflow skeleton for Skill mapping.

The caller supplies candidates. The backend never invents a new mapping or
silently corrects a failed candidate; an LLM agent may later supply a revised
candidate based on structured feedback. No DB writes, auto-approval, or apply.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Protocol


class ToolAdapter(Protocol):
    def lookup_skill_candidates(self, query: str, limit: int = 5) -> dict: ...
    def validate_skill_mapping(self, candidate: Mapping[str, Any]) -> dict: ...


@dataclass
class Attempt:
    attempt_no: int
    skill_name: str
    relation_type: str | None
    validation_status: str
    validation_code: str
    route_decision: str
    retry_of_attempt_no: int | None = None


@dataclass
class ToolEvent:
    event_seq: int
    attempt_no: int
    tool_name: str
    decision_origin: str
    tool_status: str
    result_code: str


@dataclass
class FlowResult:
    status: str = "COMPLETED"
    route: str = "NOT_DECIDED"
    attempts: list[Attempt] = field(default_factory=list)
    tool_events: list[ToolEvent] = field(default_factory=list)
    queued_proposal_id: int | None = None
    simulated: bool = True
    llm_connected: bool = False
    permanent_mapping_changed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def run_candidate_flow(
    candidate_provider: Callable[[int, str | None], Mapping[str, Any] | None],
    tools: ToolAdapter,
    *,
    max_attempts: int = 3,
) -> FlowResult:
    """Testable integration point; provider can be scripted or a future LLM.

    A passed candidate is routed for human review but NOT submitted. The agent
    pipeline may later explicitly call submit_skill_proposal if host permits.
    """
    if isinstance(max_attempts, bool) or not 1 <= max_attempts <= 5:
        raise ValueError("max_attempts must be 1..5")
    result = FlowResult()
    last_feedback: str | None = None
    for number in range(1, max_attempts + 1):
        raw = candidate_provider(number, last_feedback)
        if raw is None:
            result.route = "BLOCKED"
            break
        if not isinstance(raw, Mapping) or not isinstance(raw.get("candidate"), Mapping):
            raise ValueError("provider must supply {'skill_query':str,'candidate':dict}")
        query = str(raw.get("skill_query", ""))
        payload = raw["candidate"]
        if not query.strip():
            raise ValueError("skill_query required")
        lookup = tools.lookup_skill_candidates(query, limit=5)
        result.tool_events.append(ToolEvent(len(result.tool_events) + 1, number,
            "lookup_skill_candidates", "BACKEND", "SUCCESS", lookup.get("status", "UNKNOWN")))
        response = tools.validate_skill_mapping(payload)
        passed = response.get("validation_status") == "PASSED"
        route = response.get("route", "BLOCKED")
        code = response.get("validation_code", "UNKNOWN")
        result.tool_events.append(ToolEvent(len(result.tool_events) + 1, number,
            "validate_skill_mapping", "BACKEND", "SUCCESS", code))
        if passed:
            route = "HUMAN_REVIEW"  # Never auto-approve, even if the adapter errs.
        elif route == "RETRY" and number == max_attempts:
            route = "BLOCKED"  # Retries exhausted; never auto-accept.
        elif route not in ("RETRY", "BLOCKED"):
            route = "BLOCKED"
        result.attempts.append(Attempt(number, query,
            str(payload.get("relation_type")) if payload.get("relation_type") is not None else None,
            "PASSED" if passed else "FAILED", code, route,
            number - 1 if number > 1 else None))
        result.route = route
        if route != "RETRY":
            break
        last_feedback = code
    if result.route == "NOT_DECIDED":
        result.route = "BLOCKED"
    return result
