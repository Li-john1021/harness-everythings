"""Versioned, vendor-neutral domain-pack contracts for H4 and H5."""

from __future__ import annotations

from typing import Any, Iterable

from .entities import derive_id, make_envelope
from .identity import content_fingerprint
from .schema_registry import validate
from .approvals_roles import ApprovalError, validate_plan_approval
from .approvals_roles import ApprovalRequest, decide
from .evidence import make_artifact
from .domain_pack_loader import DomainPackLoadError, load_builtin_domain_pack, validate_loaded_domain_pack


class DomainPackError(ValueError):
    """Invalid, unknown, or incomplete domain-pack input."""


VERIFICATION_CLASSES = ("build", "test", "static", "manual", "hardware")


def load_domain_pack(pack_id: str) -> dict[str, Any]:
    """Load one validated resource pack; arbitrary IDs are rejected."""
    try:
        return load_builtin_domain_pack(pack_id)
    except DomainPackLoadError as exc:
        raise DomainPackError(str(exc)) from exc


def validate_domain_pack(pack: dict[str, Any]) -> None:
    try:
        validate_loaded_domain_pack(pack)
    except DomainPackLoadError as exc:
        raise DomainPackError(str(exc)) from exc


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainPackError(f"{field} must be a non-empty string")
    return value


def _plan_goals(plan: dict[str, Any]) -> list[str]:
    goals = plan.get("goals")
    if not isinstance(goals, list) or not goals or any(not isinstance(goal, str) or not goal.strip() for goal in goals):
        raise DomainPackError("approved Plan must contain non-empty goals")
    return sorted(set(goals))


def _acceptance_items(plan: dict[str, Any], requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    strategy = plan.get("acceptance_strategy")
    required = strategy.get("required_evidence", []) if isinstance(strategy, dict) else []
    if not isinstance(required, list) or any(not isinstance(item, str) or not item for item in required):
        raise DomainPackError("Plan acceptance_strategy.required_evidence must be string list")
    conditions = required or ["all approved requirements have implementation and verification evidence"]
    source_ref = f"plan:{plan['entity_id']}"
    return [
        {
            "acceptance_id": derive_id("acceptance", {"plan": plan["entity_id"], "condition": condition}),
            "condition": condition,
            "evidence_class": "manual" if "user" in condition or "approval" in condition else "test",
            "source_ref": source_ref,
        }
        for condition in sorted(set(conditions))
    ]


def derive_software_output_contract(plan: dict[str, Any], now: str, *, approval: dict[str, Any] | None = None, target_owner: str | None = None, evidence_records: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Map only approved Plan content to one deterministic software contract."""
    if plan.get("approval_state") != "approved" or approval is None or not target_owner:
        raise DomainPackError("canonical Plan approval is required before software contract derivation")
    try:
        validate_plan_approval(approval, plan, target_owner=target_owner, evidence_records=evidence_records)
    except ApprovalError as exc:
        raise DomainPackError(str(exc)) from exc
    plan_ref = _text(plan.get("entity_id"), "plan.entity_id")
    pack = load_domain_pack("software-engineering")
    requirements = sorted([
        {
            "requirement_id": derive_id("requirement", {"plan": plan_ref, "goal": goal}),
            "statement": goal,
            "source_ref": f"plan:{plan_ref}",
            "basis_fingerprint": content_fingerprint({"plan": plan_ref, "goal": goal}),
        }
        for goal in _plan_goals(plan)
    ], key=lambda item: item["requirement_id"])
    acceptance = sorted(_acceptance_items(plan, requirements), key=lambda item: item["acceptance_id"])
    role_ids = [template["role_id"] for template in pack["role_templates"]]
    matrix = [
        {
            "row_ref": item["requirement_id"],
            "row_kind": "requirement",
            "verification_class": "manual",
            "independent": True,
            "evidence_required": True,
        }
        for item in requirements
    ] + [
        {
            "row_ref": item["acceptance_id"],
            "row_kind": "acceptance",
            "verification_class": item["evidence_class"],
            "independent": item["evidence_class"] in {"manual", "static"},
            "evidence_required": True,
        }
        for item in acceptance
    ]
    matrix.sort(key=lambda item: (item["row_kind"], item["row_ref"]))
    envelope = make_envelope(
        "output-contract",
        {"plan": plan_ref, "pack": pack["pack_id"], "pack_version": pack["pack_version"]},
        f"h4:software:{plan_ref}",
        now,
        fields={
            "derived_from_plan": plan_ref,
            "domain_pack_id": pack["pack_id"],
            "domain_pack_version": pack["pack_version"],
            "spec_kind": "software-spec",
            "requirements": requirements,
            "acceptance": acceptance,
            "verification_matrix": matrix,
            "release_boundaries": {"software_delivery_approval": "work_product", "external_release_approval": "external_release", "separate": True},
        },
    ).to_record()
    envelope["contract_fingerprint"] = content_fingerprint(envelope)
    validate("software-output-contract", envelope)
    return envelope


def select_software_role_templates(plan: dict[str, Any]) -> list[str]:
    """Select by ownership/risk/verification; language and framework are ignored."""
    pack = load_domain_pack("software-engineering")
    risk = plan.get("risks", [])
    if not isinstance(risk, list) or any(not isinstance(item, dict) for item in risk):
        raise DomainPackError("Plan risks must be an object list")
    selected = {template["role_id"] for template in pack["role_templates"]}
    if any(item.get("kind") == "hardware" for item in risk):
        selected.add("software-build-test")
    return sorted(selected)


def build_software_traceability(contract: dict[str, Any], role_ids: Iterable[str] = ()) -> dict[str, Any]:
    validate("software-output-contract", contract)
    if any(not isinstance(role_id, str) or not role_id for role_id in role_ids):
        raise DomainPackError("role IDs must be non-empty strings")
    requirements = [
        {"requirement_id": item["requirement_id"], "implementation_refs": [], "evidence_refs": [], "verification_refs": [], "status": "unresolved"}
        for item in contract["requirements"]
    ]
    acceptance = [
        {"acceptance_id": item["acceptance_id"], "implementation_refs": [], "evidence_refs": [], "verification_refs": [], "status": "unresolved"}
        for item in contract["acceptance"]
    ]
    base = {
        "schema_version": "1.0",
        "entity_type": "traceability",
        "contract_ref": contract["entity_id"],
        "requirements": requirements,
        "acceptance": acceptance,
        "implementation": [],
        "evidence": [],
        "verification": [],
        "status": "unresolved",
    }
    result = {**base, "fingerprint": content_fingerprint(base)}
    validate("traceability", result)
    return result


def bind_software_traceability(
    traceability: dict[str, Any],
    *,
    implementation_refs: dict[str, list[str]],
    evidence_refs: dict[str, list[str]],
    verification_refs: dict[str, list[str]],
    actual_artifacts: Iterable[dict[str, Any]],
    actual_evidence: Iterable[dict[str, Any]],
    actual_verifications: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    validate("traceability", traceability)
    artifacts = {item.get("entity_id"): item for item in actual_artifacts}
    evidence = {item.get("entity_id"): item for item in actual_evidence}
    verifications = {item.get("verification_id"): item for item in actual_verifications}
    row_ids = {
        row[key]
        for group, key in (("requirements", "requirement_id"), ("acceptance", "acceptance_id"))
        for row in traceability[group]
    }
    for mapping_name, mapping in (
        ("implementation_refs", implementation_refs),
        ("evidence_refs", evidence_refs),
        ("verification_refs", verification_refs),
    ):
        if set(mapping) != row_ids:
            raise DomainPackError(f"{mapping_name} must explicitly map every traceability row and no unknown row")
    updated = dict(traceability)
    rows: list[dict[str, Any]] = []
    for group in ("requirements", "acceptance"):
        replacement = []
        key = "requirement_id" if group == "requirements" else "acceptance_id"
        for row in traceability[group]:
            row_id = row[key]
            values = {
                "implementation_refs": sorted(set(implementation_refs.get(row_id, []))),
                "evidence_refs": sorted(set(evidence_refs.get(row_id, []))),
                "verification_refs": sorted(set(verification_refs.get(row_id, []))),
            }
            if any(ref not in artifacts for ref in values["implementation_refs"]):
                raise DomainPackError(f"traceability references a missing Artifact: {row_id}")
            if any(ref not in evidence for ref in values["evidence_refs"]):
                raise DomainPackError(f"traceability references missing Evidence: {row_id}")
            if any(ref not in verifications for ref in values["verification_refs"]):
                raise DomainPackError(f"traceability references a missing Verification: {row_id}")
            for artifact_ref in values["implementation_refs"]:
                artifact = artifacts[artifact_ref]
                if artifact.get("contract_ref") != traceability["contract_ref"] or artifact.get("record_fingerprint") != _record_fp(artifact):
                    raise DomainPackError(f"traceability Artifact is stale or belongs to another contract: {row_id}")
            for evidence_ref in values["evidence_refs"]:
                record = evidence[evidence_ref]
                supporting = set(record.get("supporting_refs", []))
                if record.get("record_fingerprint") != _record_fp(record) or not {traceability["contract_ref"], row_id}.issubset(supporting):
                    raise DomainPackError(f"traceability Evidence does not support its contract and row: {row_id}")
            for verification_ref in values["verification_refs"]:
                verification = verifications[verification_ref]
                linked_evidence = evidence.get(verification.get("evidence_ref"))
                expected_row_kind = "requirement" if group == "requirements" else "acceptance"
                if (
                    verification.get("contract_ref") != traceability["contract_ref"]
                    or verification.get("row_ref") != row_id
                    or verification.get("row_kind") != expected_row_kind
                    or verification.get("record_fingerprint") != _record_fp(verification)
                    or linked_evidence is None
                    or verification.get("evidence_ref") not in values["evidence_refs"]
                    or row_id not in linked_evidence.get("supporting_refs", [])
                ):
                    raise DomainPackError(f"traceability Verification is not bound to Evidence for its row: {row_id}")
            replacement.append({**row, **values, "status": "complete" if all(values.values()) else "unresolved"})
        updated[group] = replacement
        rows.extend(replacement)
    updated["implementation"] = sorted({ref for row in rows for ref in row["implementation_refs"]})
    updated["evidence"] = sorted({ref for row in rows for ref in row["evidence_refs"]})
    updated["verification"] = sorted({ref for row in rows for ref in row["verification_refs"]})
    updated["status"] = "complete" if rows and all(row["status"] == "complete" for row in rows) else "unresolved"
    updated["fingerprint"] = content_fingerprint({key: value for key, value in updated.items() if key != "fingerprint"})
    validate("traceability", updated)
    return updated


def _without(record: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in record.items() if name != key}


def _record_fp(record: dict[str, Any]) -> str:
    return content_fingerprint(_without(record, "record_fingerprint"))


def _contract_fp(contract: dict[str, Any]) -> str:
    return content_fingerprint(_without(contract, "contract_fingerprint"))


def _require_record(record: dict[str, Any], entity_type: str) -> None:
    validate(entity_type, record)
    if record.get("entity_type") != entity_type:
        raise DomainPackError(f"expected {entity_type} record")


def software_verification_results(contract: dict[str, Any], statuses: dict[str, str] | None = None, evidence_refs: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    validate("software-output-contract", contract)
    statuses = statuses or {}
    results = []
    for item in contract["verification_matrix"]:
        verification_id = f"verification:{item['row_kind']}:{item['row_ref']}"
        status = statuses.get(verification_id, "not_run")
        if status not in {"passed", "failed", "blocked", "not_run"}:
            raise DomainPackError(f"invalid verification status: {status!r}")
        result = {
            "schema_version": "1.0",
            "entity_type": "verification-result",
            "verification_id": verification_id,
            "row_ref": item["row_ref"],
            "row_kind": item["row_kind"],
            "verification_class": item["verification_class"],
            "status": status,
            "deterministic": item["verification_class"] != "manual",
            "contract_ref": contract["entity_id"],
            "contract_fingerprint": _contract_fp(contract),
            "tool_result_source": "not-run",
        }
        if status == "passed":
            evidence = (evidence_refs or {}).get(verification_id)
            if not isinstance(evidence, dict):
                raise DomainPackError("passed verification requires an actual Evidence record")
            _require_record(evidence, "evidence")
            result["evidence_ref"] = evidence["entity_id"]
            result["evidence_fingerprint"] = evidence["record_fingerprint"]
            result["tool_result_source"] = evidence["source_ref"]
        result["record_fingerprint"] = _record_fp(result)
        validate("verification-result", result)
        results.append(result)
    return results


def make_software_artifact(content: Any, *, contract: dict[str, Any], artifact_kind: str, source_ref: str, now: str, sensitivity: str = "internal") -> dict[str, Any]:
    validate("software-output-contract", contract)
    artifact = make_artifact(content, artifact_kind=artifact_kind, source_ref=source_ref, sensitivity=sensitivity, now=now)
    artifact.update({
        "contract_ref": contract["entity_id"],
        "contract_fingerprint": _contract_fp(contract),
        "source_boundary": [source_ref],
        "evidence_boundary": [],
    })
    artifact["record_fingerprint"] = _record_fp(artifact)
    validate("artifact", artifact)
    return artifact


def make_software_review(contract: dict[str, Any], *, artifact_refs: Iterable[str], evidence_refs: Iterable[str], spec_compliance: str, implementation_quality: str, findings: Iterable[str] = (), artifact_records: Iterable[dict[str, Any]] | None = None, evidence_records: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    validate("software-output-contract", contract)
    if contract.get("contract_fingerprint") != _contract_fp(contract):
        raise DomainPackError("software review requires an untampered contract")
    axes = {"spec_compliance": spec_compliance, "implementation_quality": implementation_quality}
    if any(value not in {"passed", "failed", "blocked", "not_run"} for value in axes.values()):
        raise DomainPackError("invalid software review axis status")
    status = "passed" if all(value == "passed" for value in axes.values()) else "blocked" if "blocked" in axes.values() or "not_run" in axes.values() else "failed"
    artifacts = sorted(set(artifact_refs))
    evidence = sorted(set(evidence_refs))
    artifact_map = {item.get("entity_id"): item for item in (artifact_records or [])}
    evidence_map = {item.get("entity_id"): item for item in (evidence_records or [])}
    if set(artifacts) != set(artifact_map) or set(evidence) != set(evidence_map):
        raise DomainPackError("software review must bind the actual Artifact and Evidence records")
    for artifact in artifact_map.values():
        _require_record(artifact, "artifact")
        if artifact.get("contract_ref") != contract["entity_id"] or artifact.get("contract_fingerprint") != _contract_fp(contract) or artifact.get("record_fingerprint") != _record_fp(artifact):
            raise DomainPackError("software review artifact contract or fingerprint binding is stale")
    for evidence_record in evidence_map.values():
        _require_record(evidence_record, "evidence")
        if evidence_record.get("record_fingerprint") != _record_fp(evidence_record):
            raise DomainPackError("software review evidence fingerprint binding is stale")
    if status == "passed" and (not artifacts or not evidence):
        raise DomainPackError("passed software review requires actual artifacts and evidence")
    artifact_bindings = [{"artifact_id": item, "content_fingerprint": artifact_map[item]["content_fingerprint"], "record_fingerprint": artifact_map[item]["record_fingerprint"]} for item in artifacts]
    evidence_bindings = [{"evidence_id": item, "record_fingerprint": evidence_map[item]["record_fingerprint"]} for item in evidence]
    base = {"schema_version": "1.0", "entity_type": "software-review", "review_id": derive_id("software-review", {"contract": contract["entity_id"], "artifacts": artifacts, "evidence": evidence, **axes, "findings": sorted(set(findings))}), "contract_ref": contract["entity_id"], "contract_fingerprint": _contract_fp(contract), "artifact_refs": artifacts, "artifact_bindings": artifact_bindings, "evidence_refs": evidence, "evidence_bindings": evidence_bindings, **axes, "status": status, "findings": sorted(set(findings))}
    result = {**base, "fingerprint": content_fingerprint(base)}
    validate("software-review", result)
    return result


def make_software_work_product_approval(contract: dict[str, Any], review: dict[str, Any], verification_records: Iterable[dict[str, Any]], *, artifact_refs: Iterable[str], evidence_refs: Iterable[str], requester: str, approver: str, target_owner: str, decided_at: str, artifact_records: Iterable[dict[str, Any]] | None = None, evidence_records: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    validate("software-output-contract", contract)
    if contract.get("contract_fingerprint") != _contract_fp(contract):
        raise DomainPackError("software approval requires an untampered contract")
    validate("software-review", review)
    records = list(verification_records)
    for record in records:
        validate("verification-result", record)
    artifacts = sorted(set(artifact_refs))
    evidence = sorted(set(evidence_refs))
    artifact_map = {item.get("entity_id"): item for item in (artifact_records or [])}
    evidence_map = {item.get("entity_id"): item for item in (evidence_records or [])}
    if set(artifacts) != set(artifact_map) or set(evidence) != set(evidence_map):
        raise DomainPackError("software approval requires actual Artifact and Evidence records")
    if review["status"] != "passed" or not artifacts or not evidence or not records or any(item["status"] != "passed" for item in records):
        raise DomainPackError("software work-product approval requires passed review, artifacts, evidence, and verification")
    make_software_review(contract, artifact_refs=artifacts, evidence_refs=evidence, spec_compliance="passed", implementation_quality="passed", findings=review.get("findings", []), artifact_records=artifact_map.values(), evidence_records=evidence_map.values())
    for record in records:
        if record.get("contract_ref") != contract["entity_id"] or record.get("contract_fingerprint") != _contract_fp(contract) or record.get("record_fingerprint") != _record_fp(record):
            raise DomainPackError("software approval verification binding is stale")
    verification_refs = sorted(item["verification_id"] for item in records)
    record = decide(ApprovalRequest(contract["entity_id"], "work_product", requester, approver, target_owner, evidence_refs=tuple(evidence)), "approved", decided_at)
    record.update({"contract_ref": contract["entity_id"], "contract_fingerprint": _contract_fp(contract), "artifact_refs": artifacts, "artifact_bindings": [{"artifact_id": item, "content_fingerprint": artifact_map[item]["content_fingerprint"], "record_fingerprint": artifact_map[item]["record_fingerprint"]} for item in artifacts], "evidence_refs": evidence, "evidence_bindings": [{"evidence_id": item, "record_fingerprint": evidence_map[item]["record_fingerprint"]} for item in evidence], "verification_refs": verification_refs, "verification_bindings": [{"verification_id": item["verification_id"], "record_fingerprint": item["record_fingerprint"]} for item in records], "review_ref": review["review_id"], "review_fingerprint": review["fingerprint"]})
    record["approval_fingerprint"] = content_fingerprint({key: value for key, value in record.items() if key != "approval_fingerprint"})
    validate("approval", record)
    return record


def validate_software_work_product_approval(approval: dict[str, Any], contract: dict[str, Any], review: dict[str, Any], verification_records: Iterable[dict[str, Any]], *, target_owner: str, artifact_records: Iterable[dict[str, Any]] | None = None, evidence_records: Iterable[dict[str, Any]] | None = None) -> None:
    validate("approval", approval)
    validate("software-output-contract", contract)
    validate("software-review", review)
    records = list(verification_records)
    for record in records:
        validate("verification-result", record)
    if approval.get("decision") != "approved" or approval.get("scope") != "work_product" or approval.get("target_ref") != contract["entity_id"] or approval.get("contract_ref") != contract["entity_id"]:
        raise DomainPackError("software approval target or scope mismatch")
    artifacts = {item.get("entity_id"): item for item in (artifact_records or [])}
    evidence = {item.get("entity_id"): item for item in (evidence_records or [])}
    if set(approval.get("artifact_refs", [])) != set(artifacts) or set(approval.get("evidence_refs", [])) != set(evidence):
        raise DomainPackError("software approval is missing actual artifact/evidence records")
    for artifact in artifacts.values():
        _require_record(artifact, "artifact")
        if artifact.get("contract_ref") != contract["entity_id"] or artifact.get("contract_fingerprint") != _contract_fp(contract):
            raise DomainPackError("software approval artifact contract binding is stale")
    for evidence_record in evidence.values():
        _require_record(evidence_record, "evidence")
    if contract.get("contract_fingerprint") != _contract_fp(contract) or approval.get("contract_fingerprint") != _contract_fp(contract) or review.get("contract_ref") != contract["entity_id"] or review.get("contract_fingerprint") != _contract_fp(contract) or review.get("fingerprint") != content_fingerprint(_without(review, "fingerprint")):
        raise DomainPackError("software approval contract or review fingerprint binding is stale")
    if approval.get("target_owner") != target_owner or review.get("status") != "passed" or approval.get("review_ref") != review.get("review_id") or approval.get("review_fingerprint") != review.get("fingerprint"):
        raise DomainPackError("software approval review or owner binding mismatch")
    for artifact_id, artifact in artifacts.items():
        if artifact.get("record_fingerprint") != _record_fp(artifact) or artifact.get("content_fingerprint") != next((item.get("content_fingerprint") for item in approval.get("artifact_bindings", []) if item.get("artifact_id") == artifact_id), None):
            raise DomainPackError("software approval artifact fingerprint binding is stale")
    for evidence_id, evidence_record in evidence.items():
        if evidence_record.get("record_fingerprint") != _record_fp(evidence_record) or evidence_record.get("record_fingerprint") != next((item.get("record_fingerprint") for item in approval.get("evidence_bindings", []) if item.get("evidence_id") == evidence_id), None):
            raise DomainPackError("software approval evidence fingerprint binding is stale")
    if any(item["status"] != "passed" or item.get("contract_fingerprint") != _contract_fp(contract) or item.get("record_fingerprint") != _record_fp(item) for item in records) or sorted(approval.get("verification_refs", [])) != sorted(item["verification_id"] for item in records) or any(item.get("record_fingerprint") != next((binding.get("record_fingerprint") for binding in approval.get("verification_bindings", []) if binding.get("verification_id") == item.get("verification_id")), None) for item in records):
        raise DomainPackError("software approval contains failed, blocked, not_run, or stale verification records")
    base = {key: value for key, value in approval.items() if key != "approval_fingerprint"}
    if approval.get("approval_fingerprint") != content_fingerprint(base):
        raise DomainPackError("software approval fingerprint mismatch")


__all__ = [
    "DomainPackError",
    "VERIFICATION_CLASSES",
    "bind_software_traceability",
    "build_software_traceability",
    "derive_software_output_contract",
    "load_domain_pack",
    "select_software_role_templates",
    "software_verification_results",
    "make_software_artifact",
    "make_software_review",
    "make_software_work_product_approval",
    "validate_software_work_product_approval",
    "validate_domain_pack",
]
