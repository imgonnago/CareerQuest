"""DB-backed ResourceSkill proposal lifecycle: preview -> submit -> approve -> apply.

Security boundary: semantic correctness is NOT inferred by the validator. Human review
must occur before approve; database checks only consistency, integrity and provenance.
This module intentionally never changes existing student_skill, role_skill or old
resource_skill records except for a specifically approved mapping during apply.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Mapping

from sqlalchemy import text
from src.db.database import engine

ALLOWED_RELATIONS: dict[str, frozenset[str]] = {
    "COURSE": frozenset({"TAUGHT"}),
    "CERTIFICATE": frozenset({"VERIFIED"}),
    "PROGRAM": frozenset({"TAUGHT"}),
    "JOB": frozenset({"REQUIRED", "PREFERRED", "RELATED"}),
}
ACTIONS = frozenset({"ADD", "CONFIRM", "CHANGE_RELATION"})
STATES = frozenset({"PENDING", "APPROVED", "REJECTED", "APPLIED"})
HEX_SHA = re.compile(r"^[0-9a-f]{64}$")


class ReviewError(ValueError):
    """Fail-closed user-visible integrity or state error."""


def digest(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _positive_int(v: Any, name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise ReviewError(f"{name} must be a positive integer")
    return v


def required_actor(s: str, name="actor") -> str:
    if not isinstance(s, str) or not 1 <= len(s.strip()) <= 120:
        raise ReviewError(f"{name} is required (1..120 characters)")
    return s.strip()


@dataclass(frozen=True)
class Candidate:
    action: str
    resource_id: int
    skill_id: int
    relation_type: str
    source_document_id: int
    source_sha256: str
    evidence_text: str
    confidence: Decimal
    producer: str
    existing_resource_skill_id: int | None = None

    @classmethod
    def from_json_dict(cls, data: Mapping[str, Any]) -> "Candidate":
        if not isinstance(data, Mapping):
            raise ReviewError("Proposal JSON must be an object")
        fields = {
            "action", "resource_id", "skill_id", "relation_type", "source_document_id",
            "source_sha256", "evidence_text", "confidence", "producer",
            "existing_resource_skill_id",
        }
        extra = set(data) - fields
        if extra:
            raise ReviewError(f"Unexpected proposal keys: {sorted(extra)}")
        action = data.get("action")
        if action not in ACTIONS:
            raise ReviewError(f"Unsupported action: {action!r}; REMOVE is deliberately unsupported")
        relation = data.get("relation_type")
        if not isinstance(relation, str) or relation not in set().union(*ALLOWED_RELATIONS.values()):
            raise ReviewError(f"Invalid relation_type: {relation!r}")
        text_evidence = data.get("evidence_text")
        if not isinstance(text_evidence, str) or len(text_evidence.strip()) < 4 or len(text_evidence) > 4000:
            raise ReviewError("evidence_text must be 4..4000 characters")
        sha = data.get("source_sha256")
        if not isinstance(sha, str) or not HEX_SHA.fullmatch(sha):
            raise ReviewError("source_sha256 must be a lowercase 64-character SHA256")
        raw_conf = data.get("confidence")
        if isinstance(raw_conf, bool) or raw_conf is None:
            raise ReviewError("confidence must be a number between 0 and 1")
        try:
            confidence = Decimal(str(raw_conf))
        except (InvalidOperation, ValueError, TypeError):
            raise ReviewError("Invalid confidence") from None
        if not confidence.is_finite() or not 0 <= confidence <= 1:
            raise ReviewError("confidence must be finite and in [0,1]")
        if confidence.as_tuple().exponent < -5:
            raise ReviewError("confidence has more than 5 decimal places")
        old_id = data.get("existing_resource_skill_id")
        if action == "ADD" and old_id is not None:
            raise ReviewError("ADD cannot reference existing_resource_skill_id")
        if action != "ADD":
            old_id = _positive_int(old_id, "existing_resource_skill_id")
        return cls(
            action=action,
            resource_id=_positive_int(data.get("resource_id"), "resource_id"),
            skill_id=_positive_int(data.get("skill_id"), "skill_id"),
            relation_type=relation,
            source_document_id=_positive_int(data.get("source_document_id"), "source_document_id"),
            source_sha256=sha,
            evidence_text=text_evidence,
            confidence=confidence,
            producer=required_actor(data.get("producer"), "producer"),
            existing_resource_skill_id=old_id,
        )


def existing_snapshot(row: Mapping[str, Any]) -> str:
    """Compare-and-swap token: detects any modification to the existing mapping."""
    payload = {
        "resource_skill_id": int(row["resource_skill_id"]),
        "resource_id": int(row["resource_id"]),
        "skill_id": int(row["skill_id"]),
        "relation_type": str(row["relation_type"]),
        "evidence_text": row["evidence_text"],
        "mapping_method": row["mapping_method"],
        "confidence": None if row["confidence"] is None else str(Decimal(row["confidence"])),
        "verified": bool(row["verified"]),
        "source_document_id": row["source_document_id"],
    }
    return digest(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def submission_key(candidate: Candidate, original_snapshot_sha256: str | None) -> str:
    payload = {
        "action": candidate.action,
        "resource_id": candidate.resource_id,
        "skill_id": candidate.skill_id,
        "relation_type": candidate.relation_type,
        "source_document_id": candidate.source_document_id,
        "source_sha256": candidate.source_sha256,
        "evidence_text": candidate.evidence_text,
        "confidence": str(candidate.confidence),
        "producer": candidate.producer,
        "existing_resource_skill_id": candidate.existing_resource_skill_id,
        "original_snapshot_sha256": original_snapshot_sha256,
    }
    return digest(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def _resource_and_document(conn, c: Candidate, *, lock: bool = False):
    suffix = " FOR UPDATE" if lock else ""
    resource = conn.execute(text("""
        SELECT resource_id, resource_type, is_active FROM resource
        WHERE resource_id = :rid
    """ + suffix), {"rid": c.resource_id}).mappings().first()
    if resource is None or not resource["is_active"]:
        raise ReviewError("Resource does not exist or is inactive")
    typ = str(resource["resource_type"])
    if c.relation_type not in ALLOWED_RELATIONS.get(typ, frozenset()):
        raise ReviewError(f"Invalid {typ} -> {c.relation_type} relation")
    skill = conn.execute(text("""
        SELECT skill_id, is_active FROM skill WHERE skill_id = :sid
    """ + suffix), {"sid": c.skill_id}).mappings().first()
    if skill is None or not skill["is_active"]:
        raise ReviewError("Skill does not exist or is inactive")
    document = conn.execute(text("""
        SELECT source_document_id, resource_id, content, validation_status,
               evidence_eligible
        FROM source_document WHERE source_document_id = :did
    """ + suffix), {"did": c.source_document_id}).mappings().first()
    if (document is None or document["resource_id"] != c.resource_id
        or document["validation_status"] != "ACCEPTED"
        or document["evidence_eligible"] is not True):
        raise ReviewError("SourceDocument not directly owned or not evidence-eligible/ACCEPTED")
    content = document["content"] or ""
    if digest(content) != c.source_sha256:
        raise ReviewError("Stale source document: SHA256 differs")
    if c.evidence_text not in content:
        raise ReviewError("Evidence quote is not an exact substring of SourceDocument.content")
    return typ


def _existing_mapping(conn, c: Candidate, *, lock: bool = False):
    suffix = " FOR UPDATE" if lock else ""
    if c.action == "ADD":
        row = conn.execute(text("""
            SELECT resource_skill_id FROM resource_skill
            WHERE resource_id=:rid AND skill_id=:sid
        """ + suffix), {"rid": c.resource_id, "sid": c.skill_id}).first()
        if row is not None:
            raise ReviewError("Resource/Skill already linked; propose CONFIRM or CHANGE_RELATION")
        return None, None, None
    row = conn.execute(text("""
        SELECT resource_skill_id, resource_id, skill_id, relation_type,
               evidence_text, mapping_method, confidence, verified, source_document_id
        FROM resource_skill WHERE resource_skill_id=:mid
    """ + suffix), {"mid": c.existing_resource_skill_id}).mappings().first()
    if row is None or row["resource_id"] != c.resource_id or row["skill_id"] != c.skill_id:
        raise ReviewError("Existing ResourceSkill does not match resource_id/skill_id")
    if row["verified"]:
        raise ReviewError("Existing verified ResourceSkill is immutable in this workflow")
    old_relation = str(row["relation_type"])
    if (c.action == "CONFIRM") != (old_relation == c.relation_type):
        raise ReviewError("CONFIRM requires same relation; CHANGE_RELATION requires different relation")
    if c.action == "CHANGE_RELATION":
        duplicate = conn.execute(text("""
            SELECT resource_skill_id FROM resource_skill
            WHERE resource_id=:rid AND skill_id=:sid AND relation_type=:rel
              AND resource_skill_id<>:mid
        """ + suffix), {"rid":c.resource_id,"sid":c.skill_id,"rel":c.relation_type,"mid":c.existing_resource_skill_id}).first()
        if duplicate is not None:
            raise ReviewError("Target relation already exists")
    return row, old_relation, existing_snapshot(row)


def check_candidate(conn, c: Candidate, *, lock=False) -> dict[str, Any]:
    typ = _resource_and_document(conn, c, lock=lock)
    _, old_relation, old_sha = _existing_mapping(conn, c, lock=lock)
    return {
        "resource_type": typ,
        "action": c.action,
        "original_relation_type": old_relation,
        "original_snapshot_sha256": old_sha,
        "submission_key": submission_key(c, old_sha),
    }


def preview(c: Candidate) -> dict[str, Any]:
    with engine.connect() as conn:
        return check_candidate(conn, c)


def _event(conn, pid: int, event: str, actor: str, note: str | None = None):
    conn.execute(text("""
        INSERT INTO resource_skill_proposal_event(proposal_id, event_type, actor, note)
        VALUES (:pid,:evt,:actor,:note)
    """), {"pid":pid,"evt":event,"actor":actor,"note":note})


def _same_saved_payload(row, c: Candidate) -> bool:
    """Idempotency by the exact caller payload; status/audit fields are excluded."""
    fields = (
        "action", "resource_id", "skill_id", "relation_type", "source_document_id",
        "source_sha256", "evidence_text", "producer", "existing_resource_skill_id",
    )
    if any(row[field] != getattr(c, field) for field in fields):
        return False
    return Decimal(row["confidence"]) == c.confidence


def submit(c: Candidate, actor: str) -> tuple[int, str]:
    actor = required_actor(actor)
    with engine.begin() as conn:
        # Lock order is PROPOSAL -> RESOURCE (same as review/apply).
        # This prevents a submit/review inverted-lock-order deadlock.
        active = conn.execute(text("""
            SELECT * FROM resource_skill_proposal
            WHERE resource_id=:rid AND skill_id=:sid
              AND status IN ('PENDING','APPROVED')
            FOR UPDATE
        """), {"rid": c.resource_id, "sid": c.skill_id}).mappings().first()
        if active is not None:
            if _same_saved_payload(active, c):
                return int(active["proposal_id"]), str(active["status"])
            raise ReviewError(
                f"Another active proposal already covers Resource/Skill: "
                f"proposal_id={active['proposal_id']}; review/reject/apply it first"
            )

        # An exact repeat of a completed/rejected request gets its historical
        # result, even if the source or existing mapping has since changed.
        # This is an acknowledgement, NOT new approval or validation.
        historical = conn.execute(text("""
            SELECT * FROM resource_skill_proposal
            WHERE resource_id=:rid AND skill_id=:sid
              AND status IN ('APPLIED','REJECTED')
              AND action=:action AND relation_type=:rel
              AND source_document_id=:did AND source_sha256=:sha
              AND evidence_text=:evidence AND confidence=:confidence
              AND producer=:producer
              AND existing_resource_skill_id IS NOT DISTINCT FROM CAST(:mid AS BIGINT)
            ORDER BY proposal_id DESC LIMIT 1
        """), {
            "rid": c.resource_id, "sid": c.skill_id,
            "action": c.action, "rel": c.relation_type,
            "did": c.source_document_id, "sha": c.source_sha256,
            "evidence": c.evidence_text, "confidence": c.confidence,
            "producer": c.producer, "mid": c.existing_resource_skill_id,
        }).mappings().first()
        if historical is not None:
            return int(historical["proposal_id"]), str(historical["status"])

        # A stable per-resource row lock serializes new submissions using this service.
        metadata = check_candidate(conn, c, lock=True)
        key = metadata["submission_key"]
        existing = conn.execute(text("""
            SELECT proposal_id, status FROM resource_skill_proposal
            WHERE submission_key=:key
        """), {"key":key}).mappings().first()
        if existing:
            return int(existing["proposal_id"]), str(existing["status"])
        # A different candidate can be inserted by a legacy writer not using
        # the resource lock; fail clearly before the partial UNIQUE index does.
        conflicting = conn.execute(text("""
            SELECT proposal_id FROM resource_skill_proposal
            WHERE resource_id=:rid AND skill_id=:sid
              AND status IN ('PENDING','APPROVED') LIMIT 1
        """), {"rid": c.resource_id, "sid": c.skill_id}).first()
        if conflicting is not None:
            raise ReviewError("Active Resource/Skill proposal conflict; refresh and retry")
        pid = conn.execute(text("""
            INSERT INTO resource_skill_proposal (
                submission_key, resource_id, skill_id, source_document_id,
                existing_resource_skill_id, action, relation_type,
                original_relation_type, original_snapshot_sha256,
                evidence_text, source_sha256, confidence, producer, submitted_by
            ) VALUES (
                :key,:rid,:sid,:did,:mid,:action,:rel,:original_rel,:original_sha,
                :evidence,:sha,:confidence,:producer,:actor
            ) RETURNING proposal_id
        """), {
            "key":key, "rid":c.resource_id, "sid":c.skill_id,
            "did":c.source_document_id, "mid":c.existing_resource_skill_id,
            "action":c.action, "rel":c.relation_type,
            "original_rel":metadata["original_relation_type"],
            "original_sha":metadata["original_snapshot_sha256"],
            "evidence":c.evidence_text,"sha":c.source_sha256,
            "confidence":c.confidence, "producer":c.producer, "actor":actor,
        }).scalar_one()
        _event(conn, int(pid), "SUBMITTED", actor)
        return int(pid), "PENDING"


def _load_proposal(conn, pid: int, *, lock=True):
    clause = " FOR UPDATE" if lock else ""
    row = conn.execute(text("SELECT * FROM resource_skill_proposal WHERE proposal_id=:pid" + clause), {"pid":pid}).mappings().first()
    if row is None:
        raise ReviewError(f"Proposal not found: {pid}")
    return row


def _candidate_from_proposal(row) -> Candidate:
    return Candidate.from_json_dict({k:row[k] for k in (
        "action", "resource_id", "skill_id", "relation_type",
        "source_document_id", "source_sha256", "evidence_text", "confidence",
        "producer", "existing_resource_skill_id",
    )})


def _verify_unchanged(conn, row) -> None:
    c = _candidate_from_proposal(row)
    check = check_candidate(conn, c, lock=True)
    if (check["original_snapshot_sha256"] != row["original_snapshot_sha256"]
        or check["original_relation_type"] != row["original_relation_type"]
        or check["submission_key"] != row["submission_key"]):
        raise ReviewError("Proposal is stale; existing mapping or immutable submission changed")


def review(pid: int, *, approve: bool, actor: str, note: str) -> str:
    pid = _positive_int(pid, "proposal_id")
    actor = required_actor(actor)
    if not isinstance(note, str) or len(note.strip()) < 4:
        raise ReviewError("Human review note must explain decision (at least 4 characters)")
    note = note.strip()
    with engine.begin() as conn:
        row = _load_proposal(conn, pid)
        if row["status"] != "PENDING":
            raise ReviewError(f"Cannot review proposal in status {row['status']}")
        if approve:
            _verify_unchanged(conn, row)
        state, event = ("APPROVED", "APPROVED") if approve else ("REJECTED", "REJECTED")
        result = conn.execute(text("""
            UPDATE resource_skill_proposal
               SET status=:state, reviewed_by=:actor, review_note=:note,
                   reviewed_at=now(), updated_at=now()
             WHERE proposal_id=:pid AND status='PENDING'
        """), {"state":state,"actor":actor,"note":note,"pid":pid})
        if result.rowcount != 1:
            raise ReviewError("Status changed concurrently; approval blocked")
        _event(conn, pid, event, actor, note)
    return state


def apply(pid: int, *, actor: str) -> int:
    pid = _positive_int(pid, "proposal_id")
    actor = required_actor(actor)
    with engine.begin() as conn:
        row = _load_proposal(conn, pid)
        if row["status"] == "APPLIED":
            return int(row["applied_resource_skill_id"])
        if row["status"] != "APPROVED" or not row["reviewed_by"]:
            raise ReviewError("Only human-approved proposals may be applied")
        _verify_unchanged(conn, row)
        c = _candidate_from_proposal(row)
        payload = {
            "rid":c.resource_id,"sid":c.skill_id,"rel":c.relation_type,
            "evidence":c.evidence_text,"confidence":c.confidence,
            "did":c.source_document_id,"mid":c.existing_resource_skill_id,
        }
        if c.action == "ADD":
            mid = conn.execute(text("""
                INSERT INTO resource_skill (
                    resource_id, skill_id, relation_type, evidence_text,
                    mapping_method, confidence, verified, source_document_id
                ) VALUES (
                    :rid,:sid,:rel,:evidence,'HUMAN_REVIEW_V1',:confidence,TRUE,:did
                ) RETURNING resource_skill_id
            """), payload).scalar_one()
        else:
            mid = c.existing_resource_skill_id
            result = conn.execute(text("""
                UPDATE resource_skill
                   SET relation_type=:rel, evidence_text=:evidence,
                       mapping_method='HUMAN_REVIEW_V1', confidence=:confidence,
                       verified=TRUE, source_document_id=:did, updated_at=now()
                 WHERE resource_skill_id=:mid AND resource_id=:rid
                   AND skill_id=:sid AND verified=FALSE
            """), payload)
            if result.rowcount != 1:
                raise ReviewError("Existing ResourceSkill was modified; apply blocked")
        result = conn.execute(text("""
            UPDATE resource_skill_proposal
               SET status='APPLIED', applied_resource_skill_id=:mid,
                   applied_at=now(), updated_at=now()
             WHERE proposal_id=:pid AND status='APPROVED'
        """), {"mid":mid,"pid":pid})
        if result.rowcount != 1:
            raise ReviewError("Status changed during apply")
        _event(conn, pid, "APPLIED", actor, f"resource_skill_id={mid}")
        return int(mid)


def status(pid: int) -> dict[str, Any]:
    pid = _positive_int(pid, "proposal_id")
    with engine.connect() as conn:
        row = _load_proposal(conn, pid, lock=False)
        events = conn.execute(text("""
            SELECT event_type, actor, note, created_at
            FROM resource_skill_proposal_event
            WHERE proposal_id=:pid ORDER BY event_id
        """), {"pid":pid}).mappings().all()
    keys = ("proposal_id", "action", "resource_id", "skill_id", "relation_type",
            "source_document_id", "status", "submitted_by", "reviewed_by",
            "applied_resource_skill_id", "created_at", "reviewed_at", "applied_at")
    return {**{key:row[key] for key in keys}, "events":[dict(e) for e in events]}


def list_pending(limit: int=20) -> list[dict[str, Any]]:
    limit = _positive_int(limit,"limit")
    if limit > 500:
        raise ReviewError("limit must be <= 500")
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT proposal_id, resource_id, skill_id, action, relation_type,
                   source_document_id, created_at, submitted_by
            FROM resource_skill_proposal WHERE status='PENDING'
            ORDER BY created_at, proposal_id LIMIT :limit
        """), {"limit":limit}).mappings().all()
    return [dict(r) for r in rows]
