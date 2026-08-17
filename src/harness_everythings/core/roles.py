"""Deterministic H3 role contracts, lifecycle transitions, and reconciliation."""

from __future__ import annotations

from typing import Any, Iterable

from .entities import derive_id, make_envelope
from .identity import content_fingerprint
from .schema_registry import validate

ROLE_SOURCES = frozenset({"kernel-default", "domain-pack", "generated-overlay", "user"})
ROLE_STATES = ("proposed", "active", "merged", "split", "deprecated", "retired")
ROLE_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"active", "deprecated", "retired"}),
    "active": frozenset({"merged", "split", "deprecated"}),
    "merged": frozenset({"retired"}),
    "split": frozenset({"retired"}),
    "deprecated": frozenset({"retired"}),
    "retired": frozenset(),
}


class RolePlanningError(ValueError):
    """Role planning, lifecycle, or ownership contract failure."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RolePlanningError(f"{field} must be a non-empty string")
    return value


def _contract_fingerprint(record: dict[str, Any]) -> str:
    return content_fingerprint(
        {
            key: value
            for key, value in record.items()
            if key not in {"contract_fingerprint", "created_at", "updated_at", "lifecycle_history"}
        }
    )


def role_contract_fingerprint(record: dict[str, Any]) -> str:
    """Recompute the stable contract fingerprint for doctor and reconciliation."""
    return _contract_fingerprint(record)


def _role(
    name: str,
    mission: str,
    permissions: list[str],
    owns: list[str],
    forbids: list[str],
    capabilities: list[str],
    input_contracts: list[str],
    output_contracts: list[str],
    artifact_obligations: list[dict[str, Any]],
    evidence_obligations: list[dict[str, Any]],
    verification: dict[str, Any],
    stop_conditions: list[str],
    dependencies: list[str],
    boundaries: dict[str, Any],
    origin: str,
    now: str,
    state: str = "proposed",
) -> dict[str, Any]:
    if origin not in ROLE_SOURCES or state not in ROLE_STATES:
        raise RolePlanningError("invalid role source or lifecycle state")
    role_id = derive_id("role", {"role_name": name})
    record = make_envelope(
        "role", {"role_name": name}, f"h3:role:{origin}", now,
        fields={
            "role_id": role_id,
            "role_contract_version": "1.0",
            "role_name": name,
            "mission": mission,
            "permissions": sorted(permissions),
            "owns": sorted(owns),
            "forbids": sorted(forbids),
            "capabilities": sorted(capabilities),
            "input_contract_refs": sorted(input_contracts),
            "output_contract_refs": sorted(output_contracts),
            "artifact_obligations": artifact_obligations,
            "evidence_obligations": evidence_obligations,
            "verification": verification,
            "stop_conditions": sorted(stop_conditions),
            "dependencies": sorted(dependencies),
            "concurrency_boundaries": boundaries,
            "generation_origin": origin,
            "lifecycle_state": state,
            "lifecycle_history": [{
                "from_state": "none",
                "to_state": state,
                "changed_at": now,
                "evidence_ref": f"h3:role-proposal:{role_id}",
                "transition_fingerprint": content_fingerprint({"role_id": role_id, "to_state": state, "evidence_ref": f"h3:role-proposal:{role_id}"}),
            }],
        },
    ).to_record()
    if record["entity_id"] != record["role_id"]:
        raise RolePlanningError("role_id must equal the stable role entity_id")
    record["contract_fingerprint"] = _contract_fingerprint(record)
    validate("role", record)
    return record


def _registry(status: str, source_fingerprint: str, roles: list[dict[str, Any]], now: str) -> dict[str, Any]:
    base = {
        "schema_version": "1.0",
        "entity_type": "role-registry",
        "registry_version": "1.0",
        "status": status,
        "source_fingerprint": source_fingerprint,
        "roles": sorted(roles, key=lambda role: role["role_id"]),
        "evidence_refs": [f"profile:{source_fingerprint}"],
        "generated_at": now,
    }
    result = {**base, "registry_fingerprint": content_fingerprint(base)}
    validate("role-registry", result)
    return result


def propose_roles(
    profile: dict[str, Any],
    *,
    plan_approval_state: str,
    now: str,
) -> dict[str, Any]:
    """Propose only neutral governance roles after explicit Plan approval."""
    source_fingerprint = _text(profile.get("source_fingerprint"), "profile.source_fingerprint")
    if plan_approval_state != "approved":
        return _registry("blocked", source_fingerprint, [], now)
    records = profile.get("records", [])
    keys = {record.get("fact_key") for record in records if isinstance(record, dict)}
    proposed = [
        _role(
            "governance-coordinator",
            "Maintain approved Plans, boundaries, approvals, and lifecycle evidence.",
            ["read:plan", "read:approval", "propose:governance", "write:generated-overlay"],
            ["governance:plan", "governance:approval"],
            ["artifact:production", "external:release", "approval:own-work"],
            ["plan-reconciliation", "approval-boundary", "deterministic-evidence"],
            ["workspace-profile@1.0", "plan@1.0", "approval@1.0"],
            ["governance-proposal@1.0", "evidence@1.0"],
            [{"kind": "governance-proposal", "contract_ref": "governance-proposal@1.0", "required": True}],
            [{"kind": "approval-boundary", "contract_ref": "approval@1.0", "required": True}],
            {"methods": ["schema", "deterministic"], "independent": True, "evidence_required": True},
            ["stop_on_missing_approval", "stop_on_source_drift", "stop_on_ownership_conflict"],
            [],
            {"shared": ["plan", "approval"], "exclusive": ["governance:plan", "governance:approval"]},
            "generated-overlay", now,
        ),
    ]
    if "workspace.verification.signals" in keys or "workspace.risks" in keys:
        proposed.append(
            _role(
                "evidence-reviewer",
                "Independently review source boundaries, verification evidence, risks, and unresolved items.",
                ["read:evidence", "read:profile", "propose:evidence-review", "write:generated-overlay"],
                ["evidence:review", "risk:review"],
                ["governance:approve-own-work", "external:release", "secret:read"],
                ["source-boundary-review", "risk-review", "deterministic-evidence"],
                ["workspace-profile@1.0", "evidence@1.0", "unresolved@1.0"],
                ["evidence@1.0", "governance-proposal@1.0"],
                [{"kind": "evidence", "contract_ref": "evidence@1.0", "required": True}],
                [{"kind": "source-fingerprint", "contract_ref": "workspace-profile@1.0", "required": True}],
                {"methods": ["schema", "deterministic"], "independent": True, "evidence_required": True},
                ["stop_on_private_source", "stop_on_untrusted_instruction", "stop_on_missing_basis"],
                ["governance-coordinator"],
                {"shared": ["evidence", "risks"], "exclusive": ["evidence:review", "risk:review"]},
                "generated-overlay", now,
            )
        )
    return _registry("proposed", source_fingerprint, proposed, now)


def transition_role_state(
    role: dict[str, Any],
    target_state: str,
    *,
    changed_at: str,
    evidence_ref: str,
) -> dict[str, Any]:
    """Apply one explicit lifecycle transition and retain its evidence."""
    current = _text(role.get("lifecycle_state"), "role.lifecycle_state")
    if target_state not in ROLE_STATES:
        raise RolePlanningError(f"invalid target role state: {target_state!r}")
    if target_state not in ROLE_TRANSITIONS.get(current, frozenset()):
        raise RolePlanningError(f"illegal role transition: {current} -> {target_state}")
    evidence_ref = _text(evidence_ref, "evidence_ref")
    updated = dict(role)
    history = list(role.get("lifecycle_history", []))
    transition = {
        "from_state": current,
        "to_state": target_state,
        "changed_at": changed_at,
        "evidence_ref": evidence_ref,
        "transition_fingerprint": content_fingerprint({"role_id": role.get("role_id", role.get("entity_id")), "from_state": current, "to_state": target_state, "changed_at": changed_at, "evidence_ref": evidence_ref}),
    }
    history.append(transition)
    updated["lifecycle_state"] = target_state
    updated["lifecycle_history"] = history
    updated["updated_at"] = changed_at
    updated["contract_fingerprint"] = _contract_fingerprint({key: value for key, value in updated.items() if key != "contract_fingerprint"})
    validate("role", updated)
    return updated


def transition_role(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Compatibility spelling for callers using the shorter lifecycle name."""
    return transition_role_state(*args, **kwargs)


def _role_id(role: dict[str, Any]) -> str:
    return _text(role.get("role_id") or role.get("entity_id"), "role_id")


def _role_fingerprint(role: dict[str, Any]) -> str:
    return content_fingerprint(
        {
            key: value
            for key, value in role.items()
            if key not in {"contract_fingerprint", "created_at", "updated_at", "lifecycle_history"}
        }
    )


def _basis(kind: str, role_ids: list[str], source: str) -> dict[str, Any]:
    ordered = sorted(role_ids)
    basis = {"kind": kind, "role_ids": ordered, "source": source}
    return {"role_ids": ordered, "basis": basis, "evidence_ref": f"h3:reconcile:{content_fingerprint(basis)[7:23]}", "fingerprint": content_fingerprint(basis)}


def _overlap(left: dict[str, Any], right: dict[str, Any]) -> set[str]:
    return set(left.get("owns", [])) & set(right.get("owns", []))


def reconcile_roles(
    existing: Iterable[dict[str, Any]],
    proposed: Iterable[dict[str, Any]],
    user_roles: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Return every H3 reconciliation category without mutating user roles."""
    existing_list = sorted(list(existing), key=_role_id)
    proposed_list = sorted(list(proposed), key=_role_id)
    user_list = sorted(list(user_roles), key=_role_id)
    existing_by_id = {_role_id(role): role for role in existing_list}
    user_by_id = {_role_id(role): role for role in user_list}
    retained: list[dict[str, Any]] = []
    additions: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    source = content_fingerprint({"existing": existing_list, "proposed": proposed_list, "user": user_list})

    for role in proposed_list:
        role_id = _role_id(role)
        if role_id in user_by_id:
            item = _basis("user-overlay-priority", [role_id], source)
            item["role_id"] = role_id
            retained.append(item)
            overlap = _overlap(role, user_by_id[role_id])
            if overlap:
                conflicts.append({**_basis("user-ownership-overlap", [role_id], source), "kind": "ownership_overlap", "role_id": role_id, "owns": sorted(overlap)})
            continue
        if role_id in existing_by_id:
            item = _basis("stable-role-id", [role_id], source)
            item["role_id"] = role_id
            retained.append(item)
            old_fp = _role_fingerprint(existing_by_id[role_id])
            new_fp = _role_fingerprint(role)
            if old_fp != new_fp:
                drift.append({**_basis("contract-fingerprint-changed", [role_id], source), "role_id": role_id, "before": old_fp, "after": new_fp})
        elif any(_overlap(role, user_role) for user_role in user_list):
            overlapping = sorted({_role_id(user_role) for user_role in user_list if _overlap(role, user_role)})
            conflicts.append({**_basis("generated-user-ownership-conflict", [role_id, *overlapping], source), "kind": "ownership_overlap", "role_id": role_id, "user_role_ids": overlapping})
        else:
            additions.append({**_basis("new-stable-role-id", [role_id], source), "role": role, "role_id": role_id})

    proposed_ids = {_role_id(role) for role in proposed_list}
    deprecations: list[dict[str, Any]] = []
    lost_basis: list[dict[str, Any]] = []
    for role in existing_list:
        role_id = _role_id(role)
        if role_id in proposed_ids or role_id in user_by_id:
            continue
        if role.get("lifecycle_state") == "deprecated":
            deprecations.append({**_basis("already-deprecated", [role_id], source), "role_id": role_id})
        else:
            lost_basis.append({**_basis("no-proposed-basis", [role_id], source), "role_id": role_id})

    merge_candidates: list[dict[str, Any]] = []
    split_candidates: list[dict[str, Any]] = []
    for role in proposed_list:
        matches = [_role_id(old) for old in existing_list if _overlap(role, old)]
        if len(matches) > 1 and _role_id(role) not in existing_by_id:
            merge_candidates.append({**_basis("shared-ownership-domain", [*matches, _role_id(role)], source), "target_role_id": _role_id(role), "existing_role_ids": sorted(matches)})
    for role in existing_list:
        matches = [_role_id(new) for new in proposed_list if _overlap(role, new)]
        if len(matches) > 1 and _role_id(role) not in {_role_id(new) for new in proposed_list}:
            split_candidates.append({**_basis("multiple-independent-contracts", [_role_id(role), *matches], source), "source_role_id": _role_id(role), "proposed_role_ids": sorted(matches)})

    for index, left in enumerate(proposed_list):
        for right in proposed_list[index + 1:]:
            overlap = _overlap(left, right)
            if overlap:
                conflicts.append({**_basis("proposed-ownership-overlap", [_role_id(left), _role_id(right)], source), "kind": "ownership_overlap", "role_ids": sorted([_role_id(left), _role_id(right)]), "owns": sorted(overlap)})

    report_base = {
        "schema_version": "1.0",
        "entity_type": "role-reconciliation",
        "source_fingerprint": source,
        "retained": retained,
        "additions": additions,
        "conflicts": conflicts,
        "drift": drift,
        "merge_candidates": merge_candidates,
        "split_candidates": split_candidates,
        "deprecations": deprecations,
        "lost_basis": lost_basis,
        "user_overlay_unchanged": True,
    }
    report = {**report_base, "fingerprint": content_fingerprint(report_base)}
    validate("role-reconciliation", report)
    return report
