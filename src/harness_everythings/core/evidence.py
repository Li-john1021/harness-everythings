"""Content-addressed artifacts, evidence, handoffs, evaluations, and governance effects."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .entities import make_envelope
from .identity import bytes_fingerprint, content_fingerprint
from .schema_registry import validate
from ..views.markdown import render_entity


class EvidenceError(ValueError):
    """Invalid evidence, reference, budget, or governance effect."""


VERIFICATION_LEVELS = (
    "designed",
    "implemented",
    "unit_tested",
    "fixture_verified",
    "replay_verified",
    "user_confirmed_in_real_workflow",
    "externally_reproduced",
)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{field} must be a non-empty string")
    return value


def _safe_source_ref(value: Any, field: str = "source_ref") -> str:
    value = _text(value, field)
    if re.match(r"^(?:[A-Za-z]:[\\/]|[\\/]{1,2})", value):
        raise EvidenceError(f"{field} must not contain an absolute local path")
    lower = value.lower()
    if any(marker in lower for marker in ("private", "secret", "credential", "password", "prompt", "trace", "token")) and ("/" in value or "\\" in value):
        raise EvidenceError(f"{field} crosses a sensitive source boundary")
    return value


def make_artifact(content: Any, *, artifact_kind: str, source_ref: str, sensitivity: str, now: str, location_ref: str | None = None) -> dict[str, Any]:
    if artifact_kind not in {"text", "code", "structured", "image", "audio", "video", "report", "reference"}:
        raise EvidenceError("unknown artifact kind")
    if isinstance(content, bytes):
        fingerprint = bytes_fingerprint(content)
    else:
        fingerprint = content_fingerprint(content)
    source_ref = _safe_source_ref(source_ref)
    fields: dict[str, Any] = {"artifact_kind": artifact_kind, "content_fingerprint": fingerprint, "sensitivity": sensitivity}
    if location_ref is not None:
        fields["location_ref"] = _safe_source_ref(location_ref, "location_ref")
    record = make_envelope("artifact", {"content": fingerprint, "source": source_ref}, source_ref, now, fields=fields).to_record()
    record["record_fingerprint"] = content_fingerprint(record)
    validate("artifact", record)
    return record


def make_evidence(*, actor: str, action: str, conclusion_kind: str, supporting_refs: Iterable[str], verification_level: str, source_ref: str, now: str, user_confirmed: bool = False) -> dict[str, Any]:
    if verification_level not in VERIFICATION_LEVELS:
        raise EvidenceError("unknown verification level")
    if verification_level == "user_confirmed_in_real_workflow" and not user_confirmed:
        raise EvidenceError("real-workflow verification requires explicit user confirmation")
    source_ref = _safe_source_ref(source_ref)
    refs = sorted(set(supporting_refs))
    if any(not isinstance(ref, str) or not ref for ref in refs):
        raise EvidenceError("supporting_refs must contain non-empty strings")
    record = make_envelope("evidence", {"actor": actor, "action": action, "refs": refs}, source_ref, now, fields={"actor": actor, "action": action, "conclusion_kind": conclusion_kind, "supporting_refs": refs, "verification_level": verification_level, "user_confirmed": user_confirmed}).to_record()
    record["record_fingerprint"] = content_fingerprint(record)
    validate("evidence", record)
    return record


def make_checkpoint(*, owner_role: str, state: str, completed_refs: Iterable[str], incomplete_refs: Iterable[str], resume_preconditions: Iterable[str]) -> dict[str, Any]:
    base = {"schema_version": "1.0", "entity_type": "checkpoint", "checkpoint_id": content_fingerprint({"owner_role": owner_role, "completed": sorted(completed_refs), "incomplete": sorted(incomplete_refs)}), "owner_role": owner_role, "state": state, "completed_refs": sorted(set(completed_refs)), "incomplete_refs": sorted(set(incomplete_refs)), "resume_preconditions": sorted(set(resume_preconditions))}
    result = {**base, "fingerprint": content_fingerprint(base)}
    validate("checkpoint", result)
    return result


def make_handoff(*, checkpoint: dict[str, Any], incomplete_items: Iterable[str], resume_preconditions: Iterable[str], receiver: str, source_ref: str, now: str) -> dict[str, Any]:
    validate("checkpoint", checkpoint)
    record = make_envelope("handoff", {"checkpoint": checkpoint["checkpoint_id"], "receiver": receiver}, source_ref, now, fields={"checkpoint": checkpoint, "incomplete_items": sorted(set(incomplete_items)), "resume_preconditions": sorted(set(resume_preconditions)), "receiver": receiver}).to_record()
    record["fingerprint"] = content_fingerprint(record)
    validate("handoff", record)
    return record


def recover_handoff(handoff: dict[str, Any], checkpoint: dict[str, Any], *, evaluation_records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Resume only from the current checkpoint and canonical consumed evaluations."""
    validate("handoff", handoff)
    validate("checkpoint", checkpoint)
    embedded = handoff.get("checkpoint", {})
    if embedded.get("checkpoint_id") != checkpoint.get("checkpoint_id") or embedded.get("fingerprint") != checkpoint.get("fingerprint"):
        raise EvidenceError("handoff checkpoint is stale or mismatched")
    if sorted(handoff.get("incomplete_items", [])) != sorted(checkpoint.get("incomplete_refs", [])):
        raise EvidenceError("handoff incomplete items do not match the current checkpoint")
    evaluations: dict[str, dict[str, Any]] = {}
    for record in evaluation_records:
        validate("evaluation", record)
        if record.get("record_fingerprint") != content_fingerprint({key: value for key, value in record.items() if key != "record_fingerprint"}):
            raise EvidenceError("handoff Evaluation fingerprint is stale")
        current = evaluations.get(record["evaluation_id"])
        if current is None or record.get("event_kind") == "consumed":
            evaluations[record["evaluation_id"]] = record
    incomplete = set(checkpoint.get("incomplete_refs", []))
    unresolved = sorted(ref for ref in incomplete if evaluations.get(ref, {}).get("consumption") != "consumed")
    if unresolved:
        raise EvidenceError(f"handoff has unresolved canonical evaluations: {unresolved}")
    if checkpoint.get("state") == "complete" and incomplete:
        raise EvidenceError("complete checkpoint cannot contain incomplete references")
    return {"resumed": True, "checkpoint_id": checkpoint["checkpoint_id"], "receiver": handoff["receiver"], "remaining_refs": list(checkpoint.get("incomplete_refs", []))}


def register_evaluation(*, evaluator: str, result_ref: str, status: str = "registered", user_confirmed: bool = False) -> dict[str, Any]:
    if status not in {"registered", "passed", "failed", "blocked"}:
        raise EvidenceError("unknown evaluation status")
    if status == "passed" and not user_confirmed:
        raise EvidenceError("passed evaluation requires explicit consumption confirmation")
    result = {"schema_version": "1.0", "entity_type": "evaluation", "evaluation_id": content_fingerprint({"evaluator": evaluator, "result_ref": result_ref}), "evaluator": evaluator, "status": status, "consumption": "unconsumed", "result_ref": result_ref, "user_confirmed": user_confirmed, "event_id": content_fingerprint({"event": "registered", "evaluator": evaluator, "result_ref": result_ref}), "event_kind": "registered"}
    result["record_fingerprint"] = content_fingerprint(result)
    validate("evaluation", result)
    return result


def consume_evaluation(evaluation: dict[str, Any], *, actor: str, manual: bool) -> dict[str, Any]:
    validate("evaluation", evaluation)
    if not manual or actor != "user":
        raise EvidenceError("evaluation consumption requires manual user action")
    updated = dict(evaluation)
    updated["consumption"] = "consumed"
    updated["event_id"] = content_fingerprint({"event": "consumed", "evaluation": evaluation.get("evaluation_id"), "actor": actor})
    updated["event_kind"] = "consumed"
    updated["record_fingerprint"] = content_fingerprint({key: value for key, value in updated.items() if key != "record_fingerprint"})
    validate("evaluation", updated)
    return updated


def make_governance_effect(*, proposal_ref: str, proposal_fingerprint: str, approval_ref: str, approval_fingerprint: str, application_fingerprint: str, action: str, before_fingerprint: str, after_fingerprint: str, rollback_ref: str, effect_status: str) -> dict[str, Any]:
    if action == "applied" and (
        "unresolved" in approval_ref
        or not proposal_fingerprint.startswith("sha256:")
        or not approval_fingerprint.startswith("sha256:")
        or not application_fingerprint.startswith("sha256:")
    ):
        raise EvidenceError("applied governance effect requires bound proposal, approval, and application fingerprints")
    result = {"schema_version": "1.0", "entity_type": "governance-effect", "proposal_ref": proposal_ref, "proposal_fingerprint": proposal_fingerprint, "approval_ref": approval_ref, "approval_fingerprint": approval_fingerprint, "application_fingerprint": application_fingerprint, "action": action, "before_fingerprint": before_fingerprint, "after_fingerprint": after_fingerprint, "rollback_ref": rollback_ref, "effect_status": effect_status}
    validate("governance-effect", result)
    return result


def reference_integrity(records: dict[str, dict[str, Any]], references: Iterable[str]) -> dict[str, Any]:
    known = set(records)
    referenced = set(references)
    missing = sorted(referenced - known)
    orphaned = sorted(known - referenced)
    return {"ok": not missing, "missing": missing, "orphaned": orphaned}


def validate_budget(budget: dict[str, Any]) -> None:
    if not isinstance(budget, dict):
        raise EvidenceError("budget must be an object")
    for key, value in budget.items():
        if key not in {"max_duration_seconds", "max_tokens", "max_cost", "max_attempts"}:
            raise EvidenceError(f"unknown budget field: {key}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise EvidenceError(f"invalid budget value: {key}")


def deterministic_views(record: dict[str, Any]) -> dict[str, str]:
    return {"json_fingerprint": content_fingerprint(record), "markdown": render_entity(record)}


def doctor_evidence(*, profile_expired: bool = False, references: dict[str, dict[str, Any]] | None = None, referenced_ids: Iterable[str] = (), idle_roles: Iterable[str] = (), ownership_conflicts: Iterable[str] = (), missing_evidence: Iterable[str] = (), context_bloat: Iterable[str] = (), expired_approval: bool = False, unconsumed_evaluations: Iterable[str] = (), incomplete_handoff: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    if profile_expired:
        errors.append("profile expired")
    if references is not None:
        integrity = reference_integrity(references, referenced_ids)
        errors.extend(f"missing reference: {item}" for item in integrity["missing"])
        errors.extend(f"orphan record: {item}" for item in integrity["orphaned"])
    errors.extend(f"idle role: {item}" for item in sorted(idle_roles))
    errors.extend(f"ownership conflict: {item}" for item in sorted(ownership_conflicts))
    errors.extend(f"missing evidence: {item}" for item in sorted(missing_evidence))
    errors.extend(f"context bloat: {item}" for item in sorted(context_bloat))
    if expired_approval:
        errors.append("expired approval")
    errors.extend(f"unconsumed evaluation: {item}" for item in sorted(unconsumed_evaluations))
    if incomplete_handoff:
        errors.append("incomplete handoff")
    return {"ok": not errors, "errors": errors}


__all__ = ["EvidenceError", "consume_evaluation", "deterministic_views", "doctor_evidence", "make_artifact", "make_checkpoint", "make_evidence", "make_governance_effect", "make_handoff", "recover_handoff", "register_evaluation", "reference_integrity", "validate_budget"]
