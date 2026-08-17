"""H2 initialization and lifecycle orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .discovery import WorkspaceDiscovery, discover_workspace
from .entities import make_envelope
from .identity import bytes_fingerprint, content_fingerprint
from .approvals_roles import ApprovalError, validate_canonical_approval, validate_plan_approval
from .domain_packs import (
    DomainPackError,
    bind_software_traceability,
    build_software_traceability,
    derive_software_output_contract,
    load_domain_pack,
    select_software_role_templates,
    software_verification_results,
    validate_software_work_product_approval,
)
from .content_domain import (
    ContentDomainError,
    create_content_brief,
    derive_content_output_contract,
    make_content_variants,
    review_content,
    select_content_variant,
    transition_content,
    validate_content_approval,
)
from .content_domain import _brief_binding_fingerprint
from .evidence import make_checkpoint, make_governance_effect, make_handoff
from .state_machine import TransitionRecord, transition
from .profile import safe_summary
from .context import build_context_routes
from .roles import ROLE_TRANSITIONS, propose_roles, reconcile_roles, role_contract_fingerprint
from .schema_registry import validate
from ..adapters.capabilities import CapabilitySet
from ..adapters.contracts import adapter_state_contract
from ..storage.atomic import read_json, write_atomic, write_lock
from ..storage.manifest import (
    ApplicationManifest,
    ManifestError,
    PlannedWrite,
    apply_manifest,
    build_manifest,
    tree_fingerprint,
)
from ..storage.paths import metadata_rel_path, normalize_rel_path, resolve_in_root

METADATA_DIR = ".harness-everythings"
HARNESS_REL = f"{METADATA_DIR}/harness.json"
PROFILE_REL = f"{METADATA_DIR}/profile/workspace-profile.json"
UNRESOLVED_REL = f"{METADATA_DIR}/profile/unresolved.json"
AUTHORITY_REL = f"{METADATA_DIR}/reports/authority-map.json"
PLAN_REL = f"{METADATA_DIR}/plans/plan.json"
PLAN_APPROVAL_REL = f"{METADATA_DIR}/approvals/plan-approval.json"
GENERATED_REL = f"{METADATA_DIR}/runtime/generated-files.json"
ROLE_REGISTRY_REL = f"{METADATA_DIR}/roles/generated/registry.json"
ROLE_RECONCILIATION_REL = f"{METADATA_DIR}/roles/generated/reconciliation.json"
CONTEXT_ROUTES_REL = f"{METADATA_DIR}/context/generated/routes.json"
ADAPTER_STATE_REL = f"{METADATA_DIR}/adapters/generated/state.json"
RECONCILE_REPORT_REL = f"{METADATA_DIR}/reports/reconcile-proposal.json"
SOFTWARE_PACK_REL = f"{METADATA_DIR}/domain/generated/software-pack.json"
SOFTWARE_CONTRACT_REL = f"{METADATA_DIR}/contracts/generated/software-output-contract.json"
SOFTWARE_TRACEABILITY_REL = f"{METADATA_DIR}/reports/software-traceability.json"
SOFTWARE_VERIFICATION_REL = f"{METADATA_DIR}/reports/software-verification.json"
SOFTWARE_DELIVERY_REL = f"{METADATA_DIR}/reports/software-delivery-state.json"
CONTENT_PACK_REL = f"{METADATA_DIR}/domain/generated/content-pack.json"
CONTENT_BRIEF_REL = f"{METADATA_DIR}/content/generated/brief.json"
CONTENT_CONTRACT_REL = f"{METADATA_DIR}/contracts/generated/content-output-contract.json"
CONTENT_VARIANTS_REL = f"{METADATA_DIR}/content/generated/variants.json"
CONTENT_REVIEW_REL = f"{METADATA_DIR}/reports/content-review.json"
CONTENT_DELIVERY_REL = f"{METADATA_DIR}/reports/content-delivery-state.json"
CONTENT_APPROVAL_REL = f"{METADATA_DIR}/approvals/content-work-product.json"
CONTENT_RELEASE_APPROVAL_REL = f"{METADATA_DIR}/approvals/content-external-release.json"
SOFTWARE_REVIEW_REL = f"{METADATA_DIR}/reports/software-review.json"
SOFTWARE_APPROVAL_REL = f"{METADATA_DIR}/approvals/software-work-product.json"
SOFTWARE_RELEASE_APPROVAL_REL = f"{METADATA_DIR}/approvals/software-external-release.json"
SOFTWARE_TASK_REL = f"{METADATA_DIR}/runtime/software-task.json"
CONTENT_TASK_REL = f"{METADATA_DIR}/runtime/content-task.json"
ARTIFACT_LEDGER_REL = f"{METADATA_DIR}/evidence/generated/artifacts.json"
EVIDENCE_LEDGER_REL = f"{METADATA_DIR}/evidence/generated/evidence.json"
CHECKPOINT_REL = f"{METADATA_DIR}/evidence/generated/checkpoint.json"
HANDOFF_REL = f"{METADATA_DIR}/evidence/generated/handoff.json"
EVALUATION_LEDGER_REL = f"{METADATA_DIR}/evidence/generated/evaluations.json"
GOVERNANCE_EFFECT_REL = f"{METADATA_DIR}/reports/governance-effect.json"
GOVERNANCE_APPROVAL_REL = f"{METADATA_DIR}/approvals/governance-reconcile.json"
ARTIFACT_EVENTS_REL = f"{METADATA_DIR}/evidence/events/artifacts.json"
EVIDENCE_EVENTS_REL = f"{METADATA_DIR}/evidence/events/evidence.json"
EVALUATION_EVENTS_REL = f"{METADATA_DIR}/evidence/events/evaluations.json"


class LifecycleError(ValueError):
    """A safe, user-actionable lifecycle failure."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class InitProposal:
    discovery: WorkspaceDiscovery
    workspace: dict[str, Any]
    profile: dict[str, Any]
    unresolved: dict[str, Any]
    authority: dict[str, Any]
    plan: dict[str, Any]
    manifest: ApplicationManifest | None
    idempotent: bool = False
    drift: bool = False

    def to_result(self, *, include_payloads: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": True,
            "mode": self.workspace["workspace_kind"],
            "idempotent": self.idempotent,
            "drift_detected": self.drift,
            "workspace": self.workspace,
            "profile": self.profile,
            "unresolved": self.unresolved,
            "authority_map": self.authority,
            "plan": self.plan,
        }
        if self.manifest is not None:
            result["application_manifest"] = self.manifest.to_record()
            result["manifest_fingerprint"] = self.manifest.fingerprint()
            if include_payloads:
                result["_payloads"] = {
                    item.rel: item.payload for item in self.manifest.writes
                }
        return safe_summary(result)


def _workspace_ref(workspace: dict[str, Any]) -> str:
    return workspace["entity_id"]


def _bundle(discovery: WorkspaceDiscovery, workspace: dict[str, Any], now: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    ref = _workspace_ref(workspace)
    profile = {
        "schema_version": "1.0",
        "entity_type": "workspace-profile",
        "workspace_ref": ref,
        "source_fingerprint": discovery.source_fingerprint,
        "generated_at": now,
        "records": list(discovery.profile_records),
    }
    unresolved = {
        "schema_version": "1.0",
        "entity_type": "unresolved",
        "workspace_ref": ref,
        "source_fingerprint": discovery.source_fingerprint,
        "items": list(discovery.unresolved),
    }
    authority = {
        "schema_version": "1.0",
        "entity_type": "authority-map",
        "workspace_ref": ref,
        "source_fingerprint": discovery.source_fingerprint,
        **discovery.authority_map,
    }
    return profile, unresolved, authority


def _plan(workspace: dict[str, Any], discovery: WorkspaceDiscovery, mode: str, now: str) -> dict[str, Any]:
    unresolved_refs = [item["key"] for item in discovery.unresolved]
    return make_envelope(
        "plan",
        {"workspace": workspace["entity_id"], "mode": mode, "source": discovery.source_fingerprint},
        "h2:init",
        now,
        fields={
            "goals": ["建立带来源的工作区画像", "由用户审核治理接入范围"],
            "scope": {"workspace_ref": workspace["entity_id"], "mode": mode, "read_only_discovery": True},
            "decisions": [
                {"key": "domain_pack", "status": "unresolved", "candidates": discovery.summary["domain_candidates"]},
                {"key": "instruction_authority", "status": "unresolved"},
                {"key": "ownership_and_release", "status": "unresolved"},
            ],
            "risks": discovery.summary["risks"],
            "stages": ["profile_review", "governance_approval"],
            "acceptance_strategy": {"required_evidence": ["profile_source_fingerprint", "user_plan_approval"], "unresolved_refs": unresolved_refs},
            "approval_state": "proposed",
        },
    ).to_record()


def _payloads(proposal: InitProposal) -> dict[str, Any]:
    generated_files = [
        {"rel": PROFILE_REL, "fingerprint": content_fingerprint(proposal.profile)},
        {"rel": UNRESOLVED_REL, "fingerprint": content_fingerprint(proposal.unresolved)},
        {"rel": AUTHORITY_REL, "fingerprint": content_fingerprint(proposal.authority)},
        {"rel": PLAN_REL, "fingerprint": content_fingerprint(proposal.plan)},
    ]
    registry_base = {"schema_version": "1.0", "entity_type": "generated-files", "files": generated_files}
    registry = {**registry_base, "registry_fingerprint": content_fingerprint(registry_base)}
    return {
        HARNESS_REL: proposal.workspace,
        PROFILE_REL: proposal.profile,
        UNRESOLVED_REL: proposal.unresolved,
        AUTHORITY_REL: proposal.authority,
        PLAN_REL: proposal.plan,
        GENERATED_REL: registry,
    }


def _complete_existing(root: Path) -> bool:
    return all((root / rel).is_file() for rel in (HARNESS_REL, PROFILE_REL, UNRESOLVED_REL, AUTHORITY_REL, PLAN_REL))


def _read_existing(root: Path, rel: str) -> dict[str, Any]:
    try:
        value = read_json(root, rel)
    except (OSError, ValueError) as exc:
        raise LifecycleError("schema_incompatible", f"cannot read lifecycle record: {rel}") from exc
    if not isinstance(value, dict):
        raise LifecycleError("schema_incompatible", f"lifecycle record must be an object: {rel}")
    return value


def _optional_existing(root: Path, rel: str) -> dict[str, Any] | None:
    target = root / rel
    if not target.is_file():
        return None
    return _read_existing(root, rel)


def _user_roles(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read user role overlays without ever writing or replacing them."""
    directory = root / METADATA_DIR / "roles" / "user"
    if not directory.is_dir():
        return [], []
    roles: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in sorted(directory.glob("*.json")):
        rel = path.relative_to(root).as_posix()
        try:
            resolve_in_root(root, rel)
        except Exception as exc:
            errors.append(f"{path.name}: unsafe overlay path: {exc}")
            continue
        try:
            value = read_json(root, rel)
        except ValueError:
            # User overlays may be arbitrary user-owned bytes; only JSON role
            # records participate in role reconciliation.
            continue
        except OSError as exc:
            errors.append(f"{path.name}: cannot read overlay: {exc}")
            continue
        if not isinstance(value, dict):
            continue
        if value.get("entity_type") != "role":
            continue
        try:
            validate("role", value)
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        roles.append(value)
    return roles, errors


def _profile_context_sources(profile: dict[str, Any], roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    role_by_name = {role.get("role_name"): role.get("role_id", role.get("entity_id")) for role in roles}
    governance_id = role_by_name.get("governance-coordinator")
    evidence_id = role_by_name.get("evidence-reviewer")
    sources: list[dict[str, Any]] = []
    for record in sorted(profile.get("records", []), key=lambda item: item.get("fact_key", "")):
        if not isinstance(record, dict):
            continue
        fact_key = record.get("fact_key")
        source_ref = record.get("source_ref") or f"profile:{record.get('entity_id', 'unknown')}"
        source_fingerprint = record.get("source_fingerprint") or profile.get("source_fingerprint")
        if not isinstance(fact_key, str) or not isinstance(source_ref, str) or not isinstance(source_fingerprint, str):
            continue
        owner = evidence_id if fact_key in {"workspace.verification.signals", "workspace.risks"} else governance_id
        source: dict[str, Any] = {
            "source_ref": source_ref,
            "source_fingerprint": source_fingerprint,
            "sensitivity": record.get("sensitivity", "internal"),
            "estimated_tokens": 32,
            "authorized_role_ids": [owner] if owner else [],
            "authorized_purposes": [f"role:{role.get('role_name')}" for role in roles if role.get("role_id", role.get("entity_id")) == owner],
        }
        sources.append(source)
    return sources


def _stabilize_role_registry(desired: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    if not existing:
        return desired
    old_by_id = {role.get("role_id", role.get("entity_id")): role for role in existing.get("roles", [])}
    roles: list[dict[str, Any]] = []
    for role in desired.get("roles", []):
        old = old_by_id.get(role.get("role_id", role.get("entity_id")))
        if old and old.get("contract_fingerprint") == role.get("contract_fingerprint"):
            stable = dict(role)
            stable["created_at"] = old.get("created_at", stable["created_at"])
            stable["updated_at"] = old.get("updated_at", stable["updated_at"])
            stable["lifecycle_history"] = old.get("lifecycle_history", stable["lifecycle_history"])
            roles.append(stable)
        else:
            roles.append(role)
    result = dict(desired)
    result["roles"] = sorted(roles, key=lambda role: role["role_id"])
    result["registry_fingerprint"] = content_fingerprint({key: value for key, value in result.items() if key != "registry_fingerprint"})
    validate("role-registry", result)
    return result


def _h3_bundle(
    profile: dict[str, Any],
    plan: dict[str, Any],
    now: str,
    *,
    existing_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    approved = plan.get("approval_state") == "approved"
    role_registry = propose_roles(profile, plan_approval_state=plan.get("approval_state", "proposed"), now=now)
    if not approved:
        return {"status": "blocked", "reason": "plan approval is required before H3 derivation"}
    return {"status": "proposed", "role_registry": _stabilize_role_registry(role_registry, existing_registry)}


def _requested_domain_pack(plan: dict[str, Any]) -> str | None:
    scope = plan.get("scope")
    if not isinstance(scope, dict):
        return None
    value = scope.get("domain_pack")
    if isinstance(value, str):
        return value
    return None


def _read_h6_events(root: Path, rel: str, entity_type: str, owner_ref: str, item_key: str) -> list[dict[str, Any]]:
    record = _optional_existing(root, rel)
    if record is None:
        return []
    validate(entity_type, record)
    base = {key: value for key, value in record.items() if key != "fingerprint"}
    if record.get("fingerprint") != content_fingerprint(base) or record.get("owner_ref") != owner_ref:
        raise LifecycleError("schema_incompatible", f"H6 event ledger fingerprint or owner mismatch: {rel}")
    seen: dict[str, str] = {}
    for item in record.get("events", []):
        key = item.get("entity_id") if item_key == "entity_id" else item.get(item_key)
        fp = content_fingerprint(item)
        if key in seen and seen[key] != fp:
            raise LifecycleError("schema_incompatible", f"conflicting H6 event: {key}")
        seen[key] = fp
    return list(record.get("events", []))


def _h6_bundle(root: Path, plan: dict[str, Any], now: str, *, plan_approval: dict[str, Any] | None = None) -> dict[str, Any]:
    scope = plan.get("scope")
    if not isinstance(scope, dict) or scope.get("evidence_governance") is not True:
        return {"status": "not-enabled"}
    plan_ref = plan["entity_id"]
    artifact_events = _read_h6_events(root, ARTIFACT_EVENTS_REL, "artifact-events", plan_ref, "entity_id")
    evidence_events = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", plan_ref, "entity_id")
    evaluation_events = _read_h6_events(root, EVALUATION_EVENTS_REL, "evaluation-events", plan_ref, "event_id")
    latest_evaluations: dict[str, dict[str, Any]] = {}
    for event in evaluation_events:
        previous = latest_evaluations.get(event["evaluation_id"])
        if previous is None or (event.get("event_kind") == "consumed" and previous.get("event_kind") != "consumed") or (event.get("event_kind") == previous.get("event_kind") and event.get("event_id", "") > previous.get("event_id", "")):
            latest_evaluations[event["evaluation_id"]] = event
    evaluations_list = sorted(latest_evaluations.values(), key=lambda item: item["evaluation_id"])
    artifacts_base = {"schema_version": "1.0", "entity_type": "artifact-ledger", "owner_ref": plan_ref, "artifacts": sorted(artifact_events, key=lambda item: item["entity_id"])}
    artifacts = {**artifacts_base, "fingerprint": content_fingerprint(artifacts_base)}
    evidence_base = {"schema_version": "1.0", "entity_type": "evidence-ledger", "owner_ref": plan_ref, "evidence": sorted(evidence_events, key=lambda item: item["entity_id"])}
    evidence = {**evidence_base, "fingerprint": content_fingerprint(evidence_base)}
    unconsumed = [item for item in evaluations_list if item.get("consumption") != "consumed"]
    incomplete = [item["evaluation_id"] for item in unconsumed]
    completed = [item["evaluation_id"] for item in evaluations_list if item.get("consumption") == "consumed"]
    checkpoint = make_checkpoint(owner_role="role:governance-coordinator", state="partial_success" if incomplete else "complete", completed_refs=completed, incomplete_refs=incomplete, resume_preconditions=incomplete)
    handoff = make_handoff(checkpoint=checkpoint, incomplete_items=incomplete, resume_preconditions=incomplete, receiver="role:governance-coordinator", source_ref=f"h6:handoff:{plan_ref}", now=now)
    evaluations_base = {"schema_version": "1.0", "entity_type": "evaluation-ledger", "owner_ref": plan_ref, "evaluations": evaluations_list}
    evaluations = {**evaluations_base, "fingerprint": content_fingerprint(evaluations_base)}
    if plan_approval is None:
        return {"status": "blocked", "reason": "canonical Plan approval is required for H6 governance"}
    h6_basis = {
        "plan_ref": plan_ref,
        "artifacts": artifacts["fingerprint"],
        "evidence": evidence["fingerprint"],
        "evaluations": evaluations["fingerprint"],
        "checkpoint": checkpoint["fingerprint"],
        "handoff": handoff["fingerprint"],
    }
    governance_effect = make_governance_effect(
        proposal_ref=f"h6:proposal:{plan_ref}",
        proposal_fingerprint=content_fingerprint(h6_basis),
        approval_ref=plan_approval["entity_id"],
        approval_fingerprint=plan_approval["approval_fingerprint"],
        application_fingerprint=content_fingerprint({"kind": "h6-dry-run", **h6_basis}),
        action="proposed",
        before_fingerprint=artifacts["fingerprint"],
        after_fingerprint=artifacts["fingerprint"],
        rollback_ref=f"h6:rollback:{plan_ref}",
        effect_status="no-change",
    )
    for entity_type, record in (("artifact-ledger", artifacts), ("evidence-ledger", evidence), ("evaluation-ledger", evaluations), ("handoff", handoff), ("governance-effect", governance_effect)):
        validate(entity_type, record)
    return {"status": "proposed", "artifacts": artifacts, "evidence": evidence, "checkpoint": checkpoint, "handoff": handoff, "evaluations": evaluations, "governance_effect": governance_effect}


def _domain_bundle(plan: dict[str, Any], *, plan_approval: dict[str, Any] | None = None, target_owner: str | None = None, evidence_records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    pack_id = _requested_domain_pack(plan)
    if pack_id is None:
        return {"status": "not-enabled"}
    try:
        if pack_id == "content-script":
            scope = plan.get("scope")
            if not isinstance(scope, dict) or not isinstance(scope.get("content_brief"), dict):
                raise ContentDomainError("content-script requires scope.content_brief")
            contents = scope.get("content_variants")
            if not isinstance(contents, list) or not contents or any(not isinstance(item, (str, dict)) for item in contents):
                raise ContentDomainError("content-script requires non-empty scope.content_variants")
            pack = load_domain_pack(pack_id)
            brief = create_content_brief(scope["content_brief"], plan["updated_at"])
            contract = derive_content_output_contract(brief, plan["updated_at"])
            variants = make_content_variants(brief, contents)
            selected_variant_id = scope.get("selected_variant_id")
            if selected_variant_id is not None:
                if not isinstance(selected_variant_id, str) or not selected_variant_id:
                    raise ContentDomainError("selected_variant_id must be a non-empty string")
                variants = select_content_variant(variants, selected_variant_id)
            variants_base = {"schema_version": "1.0", "entity_type": "content-variants", "brief_ref": brief["entity_id"], "variants": variants}
            variants_record = {**variants_base, "fingerprint": content_fingerprint(variants_base)}
            validate("content-variants", variants_record)
            review = review_content(brief, variants, now=plan["updated_at"], selected_variant_id=selected_variant_id)
            return {"status": "proposed", "pack": pack, "brief": brief, "contract": contract, "variants": variants_record, "review": review, "role_template_ids": sorted(template["role_id"] for template in pack["role_templates"])}
        if pack_id != "software-engineering":
            raise DomainPackError(f"unknown domain pack: {pack_id!r}")
        pack = load_domain_pack(pack_id)
        if plan_approval is None or not target_owner:
            raise DomainPackError("canonical Plan approval is required for software derivation")
        contract = derive_software_output_contract(plan, plan["updated_at"], approval=plan_approval, target_owner=target_owner, evidence_records=evidence_records)
        role_ids = select_software_role_templates(plan)
        traceability = build_software_traceability(contract, role_ids)
        verification_records = software_verification_results(contract)
        verification_base = {
            "schema_version": "1.0",
            "entity_type": "verification-results",
            "contract_ref": contract["entity_id"],
            "results": verification_records,
        }
        verification = {**verification_base, "fingerprint": content_fingerprint(verification_base)}
        validate("verification-results", verification)
        delivery_base = {"schema_version": "1.0", "entity_type": "software-delivery-state", "contract_ref": contract["entity_id"], "artifact_refs": [], "review_ref": "", "review_status": "not_run", "verification_refs": [], "verification_status": "not_run", "work_product_approval_ref": "", "completion_status": "unresolved"}
        delivery = {**delivery_base, "fingerprint": content_fingerprint(delivery_base)}
        validate("software-delivery-state", delivery)
        return {
            "status": "proposed",
            "pack": pack,
            "contract": contract,
            "traceability": traceability,
            "verification": verification,
            "delivery": delivery,
            "role_template_ids": role_ids,
        }
    except (DomainPackError, ContentDomainError, KeyError, TypeError, ValueError) as exc:
        return {"status": "blocked", "reason": "domain pack derivation failed", "error": str(exc)}


def _h3_bundle_for_root(root: Path, profile: dict[str, Any], plan: dict[str, Any], now: str) -> dict[str, Any]:
    plan_approval: dict[str, Any] | None = None
    target_owner: str | None = None
    evidence_records = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", plan["entity_id"], "entity_id")
    if plan.get("approval_state") == "approved":
        try:
            workspace = _read_existing(root, HARNESS_REL)
            approval = _optional_existing(root, PLAN_APPROVAL_REL)
            if approval is None:
                raise ApprovalError("canonical Plan approval is missing")
            validate_plan_approval(
                approval,
                plan,
                target_owner=workspace["entity_id"],
                evidence_records=evidence_records,
            )
            plan_approval = approval
            target_owner = workspace["entity_id"]
        except (ApprovalError, KeyError) as exc:
            return {
                "status": "blocked",
                "reason": "canonical Plan approval is invalid",
                "approval_error": str(exc),
            }
    existing_registry = _optional_existing(root, ROLE_REGISTRY_REL)
    base = _h3_bundle(profile, plan, now, existing_registry=existing_registry)
    if base["status"] != "proposed":
        return base
    user_roles, overlay_errors = _user_roles(root)
    user_role_ids = {role.get("role_id", role.get("entity_id")) for role in user_roles}
    user_owned = {owned for role in user_roles for owned in role.get("owns", [])}
    desired_roles = [
        role for role in base["role_registry"]["roles"]
        if role.get("role_id") not in user_role_ids
        and not user_owned.intersection(role.get("owns", []))
    ]
    role_registry = dict(base["role_registry"])
    role_registry["roles"] = desired_roles
    role_registry["registry_fingerprint"] = content_fingerprint({key: value for key, value in role_registry.items() if key != "registry_fingerprint"})
    validate("role-registry", role_registry)
    role_reconciliation = reconcile_roles(
        (existing_registry or {}).get("roles", []),
        base["role_registry"]["roles"],
        user_roles,
    )
    context_roles = [*desired_roles, *user_roles]
    context_routes = build_context_routes(
        context_roles,
        _profile_context_sources(profile, context_roles),
        max_tokens=4000,
    )
    adapter_state = adapter_state_contract(CapabilitySet("runtime-minimal", "runtime", frozenset()))
    domain = _domain_bundle(plan, plan_approval=plan_approval, target_owner=target_owner, evidence_records=evidence_records)
    if domain["status"] == "blocked":
        return {"status": "blocked", "reason": domain["reason"], "domain_error": domain.get("error")}
    if domain.get("pack", {}).get("pack_id") == "content-script":
        evidence_events = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", plan["entity_id"], "entity_id")
        brief_id = domain["brief"]["entity_id"]
        variant_ids = {item["variant_id"] for item in domain["variants"]["variants"]}
        review_refs = sorted(
            item["entity_id"] for item in evidence_events
            if set(item.get("supporting_refs", [])) & ({brief_id} | variant_ids)
        )
        if review_refs:
            domain["review"] = review_content(
                domain["brief"], domain["variants"]["variants"], now=plan["updated_at"],
                selected_variant_id=plan.get("scope", {}).get("selected_variant_id"), evidence_refs=review_refs,
            )
    return {
        "status": "proposed",
        "role_registry": role_registry,
        "role_reconciliation": role_reconciliation,
        "context_routes": context_routes,
        "adapter_state": adapter_state,
        "domain": domain,
        "h6": _h6_bundle(root, plan, now, plan_approval=plan_approval),
        "user_overlay_errors": overlay_errors,
    }


def _h3_diff(root: Path, profile: dict[str, Any], plan: dict[str, Any], now: str) -> dict[str, Any]:
    if plan.get("approval_state") != "approved":
        return {"status": "blocked", "drift_detected": False, "reason": "plan approval is required"}
    desired = _h3_bundle_for_root(root, profile, plan, now)
    if desired.get("status") == "blocked":
        return {
            "status": "blocked",
            "drift_detected": True,
            "reason": desired.get("reason"),
            "approval_error": desired.get("approval_error"),
        }
    comparisons = {
        "role_registry": (ROLE_REGISTRY_REL, desired["role_registry"]),
        "context_routes": (CONTEXT_ROUTES_REL, desired["context_routes"]),
        "adapter_state": (ADAPTER_STATE_REL, desired["adapter_state"]),
    }
    domain = desired.get("domain", {"status": "not-enabled"})
    if domain.get("status") == "proposed":
        if domain.get("pack", {}).get("pack_id") == "software-engineering":
            comparisons.update({
                "software_pack": (SOFTWARE_PACK_REL, domain["pack"]),
                "software_contract": (SOFTWARE_CONTRACT_REL, domain["contract"]),
                "software_traceability": (SOFTWARE_TRACEABILITY_REL, domain["traceability"]),
                "software_verification": (SOFTWARE_VERIFICATION_REL, _optional_existing(root, SOFTWARE_VERIFICATION_REL) or domain["verification"]),
                "software_delivery": (SOFTWARE_DELIVERY_REL, _optional_existing(root, SOFTWARE_DELIVERY_REL) or domain["delivery"]),
            })
        elif domain.get("pack", {}).get("pack_id") == "content-script":
            comparisons.update({
                "content_pack": (CONTENT_PACK_REL, domain["pack"]),
                "content_brief": (CONTENT_BRIEF_REL, domain["brief"]),
                "content_contract": (CONTENT_CONTRACT_REL, domain["contract"]),
                "content_variants": (CONTENT_VARIANTS_REL, domain["variants"]),
                "content_review": (CONTENT_REVIEW_REL, domain["review"]),
            })
            if (root / CONTENT_DELIVERY_REL).is_file():
                comparisons["content_delivery"] = (CONTENT_DELIVERY_REL, _optional_existing(root, CONTENT_DELIVERY_REL))
    h6 = desired.get("h6", {"status": "not-enabled"})
    if h6.get("status") == "proposed":
        comparisons.update({
            "artifact_ledger": (ARTIFACT_LEDGER_REL, h6["artifacts"]),
            "evidence_ledger": (EVIDENCE_LEDGER_REL, h6["evidence"]),
            "checkpoint": (CHECKPOINT_REL, h6["checkpoint"]),
            "handoff": (HANDOFF_REL, h6["handoff"]),
            "evaluation_ledger": (EVALUATION_LEDGER_REL, h6["evaluations"]),
            "governance_effect": (GOVERNANCE_EFFECT_REL, h6["governance_effect"]),
        })
    states: dict[str, Any] = {}
    drifted = False
    for name, (rel, expected) in comparisons.items():
        actual = _optional_existing(root, rel)
        expected_fingerprint = (
            _brief_binding_fingerprint(expected)
            if rel == CONTENT_BRIEF_REL
            else content_fingerprint(expected)
        )
        actual_fingerprint = (
            _brief_binding_fingerprint(actual)
            if rel == CONTENT_BRIEF_REL and actual is not None
            else content_fingerprint(actual) if actual is not None else None
        )
        changed = actual is None or actual_fingerprint != expected_fingerprint
        states[name] = {"present": actual is not None, "drift_detected": changed, "fingerprint": content_fingerprint(actual) if actual is not None else None, "expected_fingerprint": content_fingerprint(expected)}
        drifted = drifted or changed
    reconciliation = _optional_existing(root, ROLE_RECONCILIATION_REL)
    states["role_reconciliation"] = {
        "present": reconciliation is not None,
        "drift_detected": False,
        "fingerprint": content_fingerprint(reconciliation) if reconciliation is not None else None,
        "purpose": "latest adjustment proposal; not canonical role state",
    }
    return {"status": desired["status"], "drift_detected": drifted, "components": states, "user_overlay_errors": desired.get("user_overlay_errors", []), "domain": {"status": domain.get("status"), "role_template_ids": domain.get("role_template_ids", [])}, "h6": {"status": h6.get("status")}}


def build_init_proposal(root: Path, mode: str, now: str) -> InitProposal:
    """Build an init proposal without writing the workspace."""
    if mode not in {"new", "existing"}:
        raise LifecycleError("invalid_input", f"unknown init mode: {mode!r}")
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise LifecycleError("invalid_input", "workspace must be a directory")
    discovery = discover_workspace(root, now, workspace_kind=mode)
    if mode == "new" and discovery.source_paths:
        raise LifecycleError("invalid_input", "init new requires an empty workspace")
    if _complete_existing(root):
        old_profile = _read_existing(root, PROFILE_REL)
        old_workspace = _read_existing(root, HARNESS_REL)
        drift = old_profile.get("source_fingerprint") != discovery.source_fingerprint
        if not drift:
            return InitProposal(
                discovery, old_workspace, old_profile,
                _read_existing(root, UNRESOLVED_REL), _read_existing(root, AUTHORITY_REL),
                _read_existing(root, PLAN_REL), None, idempotent=True,
            )
        return InitProposal(
            discovery, old_workspace, old_profile,
            _read_existing(root, UNRESOLVED_REL), _read_existing(root, AUTHORITY_REL),
            _read_existing(root, PLAN_REL), None, drift=True,
        )
    workspace_kind = mode
    workspace = make_envelope(
        "workspace",
        {"root": str(root), "mode": mode},
        "h2:init",
        now,
        fields={
            "workspace_name": discovery.workspace_name,
            "workspace_kind": workspace_kind,
            "lifecycle_state": "proposed",
            "enabled_domain_packs": [],
            "config_version": "1.0",
        },
    ).to_record()
    profile, unresolved, authority = _bundle(discovery, workspace, now)
    plan = _plan(workspace, discovery, mode, now)
    proposal = InitProposal(discovery, workspace, profile, unresolved, authority, plan, None, drift=_complete_existing(root))
    payloads = _payloads(proposal)
    writes = [
        PlannedWrite(HARNESS_REL, payloads[HARNESS_REL], exclusive=True),
        PlannedWrite(PROFILE_REL, payloads[PROFILE_REL], exclusive=True),
        PlannedWrite(UNRESOLVED_REL, payloads[UNRESOLVED_REL], exclusive=True),
        PlannedWrite(AUTHORITY_REL, payloads[AUTHORITY_REL], exclusive=True),
        PlannedWrite(PLAN_REL, payloads[PLAN_REL], exclusive=True),
        PlannedWrite(GENERATED_REL, payloads[GENERATED_REL], exclusive=True),
    ]
    snapshot_excludes = (".git",) if plan.get("approval_state") == "approved" else (METADATA_DIR, ".git")
    manifest = build_manifest(root, writes, now, snapshot_workspace=True, snapshot_excludes=snapshot_excludes)
    return InitProposal(discovery, workspace, profile, unresolved, authority, plan, manifest, drift=_complete_existing(root))


def apply_init_proposal(root: Path, proposal: InitProposal, approval: dict[str, Any]) -> dict[str, Any]:
    if proposal.manifest is None:
        return {"applied": 0, "idempotent": True}
    approval = dict(approval)
    approval.setdefault("scope", "init")
    try:
        applied = apply_manifest(root, proposal.manifest, approval)
    except ManifestError as exc:
        message = str(exc)
        category = "fingerprint_conflict" if "fingerprint" in message or "workspace" in message else "approval_missing"
        raise LifecycleError(category, message) from exc
    return applied


def inspect_workspace(root: Path, now: str) -> dict[str, Any]:
    return {"ok": True, "operation": "inspect", "discovery": discover_workspace(root, now).to_record()}


def diff_workspace(root: Path, now: str) -> dict[str, Any]:
    discovery = discover_workspace(root, now)
    if not _complete_existing(root):
        return {"ok": True, "initialized": False, "drift_detected": False, "current_source_fingerprint": discovery.source_fingerprint}
    profile = _read_existing(root, PROFILE_REL)
    drift = profile.get("source_fingerprint") != discovery.source_fingerprint
    plan = _read_existing(root, PLAN_REL)
    h3 = _h3_diff(root, profile, plan, now)
    return {
        "ok": True,
        "initialized": True,
        "drift_detected": drift or h3.get("drift_detected", False),
        "previous_source_fingerprint": profile.get("source_fingerprint"),
        "current_source_fingerprint": discovery.source_fingerprint,
        "proposal": "reconcile required" if drift or h3.get("drift_detected", False) else "no-change",
        "h3": h3,
    }


def reconcile_workspace(root: Path, now: str) -> tuple[dict[str, Any], ApplicationManifest | None]:
    discovery = discover_workspace(root, now)
    current = diff_workspace(root, now)
    if not current["initialized"]:
        return {"ok": True, "proposal": "init required", "discovery": discovery.to_record()}, None
    plan = _read_existing(root, PLAN_REL)
    h3 = _h3_bundle_for_root(root, _read_existing(root, PROFILE_REL), plan, now)
    h3_summary = {
        "status": h3.get("status"),
        "user_overlay_errors": h3.get("user_overlay_errors", []),
        "components": {
            key: content_fingerprint(value)
            for key, value in h3.items()
            if key in {"role_registry", "role_reconciliation", "context_routes", "adapter_state"}
        },
        "domain": {
            "status": h3.get("domain", {}).get("status", "not-enabled"),
            "components": {
                key: content_fingerprint(h3["domain"][key])
                for key in ("pack", "contract", "traceability", "verification")
                if key in h3.get("domain", {})
            },
            "role_template_ids": h3.get("domain", {}).get("role_template_ids", []),
        },
        "h6": {
            "status": h3.get("h6", {}).get("status", "not-enabled"),
            "components": {
                key: content_fingerprint(h3["h6"][key])
                for key in ("artifacts", "evidence", "checkpoint", "handoff", "evaluations")
                if key in h3.get("h6", {})
            },
        },
        "role_reconciliation": {
            key: h3.get("role_reconciliation", {}).get(key, [])
            for key in ("retained", "additions", "conflicts", "drift", "merge_candidates", "split_candidates", "deprecations", "lost_basis")
        } if h3.get("role_reconciliation") else {},
    }
    proposal = make_envelope(
        "governance-proposal",
        {"workspace": discovery.source_fingerprint, "h3": h3_summary},
        "h3:reconcile",
        now,
        fields={
            "proposed_change": {
                "kind": "profile-role-context-adapter-reconcile",
                "from": current["previous_source_fingerprint"],
                "to": discovery.source_fingerprint,
                "h3": h3_summary,
                "user_overlay_unchanged": True,
            },
            "evidence_refs": ["workspace:discovery", f"profile:{discovery.source_fingerprint}"],
            "risks": discovery.summary["risks"],
            "rollback_plan": {"kind": "application-manifest-rollback", "user_files": "untouched"},
            "approval_state": "proposed",
        },
    ).to_record()
    validate("governance-proposal", proposal)
    writes: list[PlannedWrite] = []

    def add_json_write(rel: str, payload: dict[str, Any]) -> None:
        target = root / rel
        if target.is_file():
            writes.append(PlannedWrite(rel, payload, exclusive=False, expected_before_fingerprint=bytes_fingerprint(target.read_bytes())))
        else:
            writes.append(PlannedWrite(rel, payload, exclusive=True))

    add_json_write(RECONCILE_REPORT_REL, proposal)
    if h3.get("status") == "proposed":
        add_json_write(ROLE_REGISTRY_REL, h3["role_registry"])
        add_json_write(ROLE_RECONCILIATION_REL, h3["role_reconciliation"])
        add_json_write(CONTEXT_ROUTES_REL, h3["context_routes"])
        add_json_write(ADAPTER_STATE_REL, h3["adapter_state"])
        domain = h3.get("domain", {})
        if domain.get("status") == "proposed":
            if domain.get("pack", {}).get("pack_id") == "software-engineering":
                add_json_write(SOFTWARE_PACK_REL, domain["pack"])
                add_json_write(SOFTWARE_CONTRACT_REL, domain["contract"])
                add_json_write(SOFTWARE_TRACEABILITY_REL, domain["traceability"])
                add_json_write(SOFTWARE_VERIFICATION_REL, domain["verification"])
                add_json_write(SOFTWARE_DELIVERY_REL, domain["delivery"])
            elif domain.get("pack", {}).get("pack_id") == "content-script":
                add_json_write(CONTENT_PACK_REL, domain["pack"])
                add_json_write(CONTENT_BRIEF_REL, domain["brief"])
                add_json_write(CONTENT_CONTRACT_REL, domain["contract"])
                add_json_write(CONTENT_VARIANTS_REL, domain["variants"])
                add_json_write(CONTENT_REVIEW_REL, domain["review"])
        h6 = h3.get("h6", {})
        if h6.get("status") == "proposed":
            add_json_write(ARTIFACT_LEDGER_REL, h6["artifacts"])
            add_json_write(EVIDENCE_LEDGER_REL, h6["evidence"])
            add_json_write(CHECKPOINT_REL, h6["checkpoint"])
            add_json_write(HANDOFF_REL, h6["handoff"])
            add_json_write(EVALUATION_LEDGER_REL, h6["evaluations"])
            add_json_write(GOVERNANCE_EFFECT_REL, h6["governance_effect"])
    generated = _optional_existing(root, GENERATED_REL)
    if generated is None:
        raise LifecycleError("schema_incompatible", "generated file registry is missing")
    generated_base = {key: value for key, value in generated.items() if key != "registry_fingerprint"}
    validate("generated-files", generated)
    entries = {entry["rel"]: entry for entry in generated.get("files", []) if isinstance(entry, dict) and entry.get("rel")}
    for write in writes:
        entries[write.rel] = {"rel": write.rel, "fingerprint": content_fingerprint(write.payload)}
    generated_base["files"] = [entries[key] for key in sorted(entries)]
    generated_payload = {**generated_base, "registry_fingerprint": content_fingerprint(generated_base)}
    validate("generated-files", generated_payload)
    add_json_write(GENERATED_REL, generated_payload)
    snapshot_excludes = (".git",) if plan.get("approval_state") == "approved" else (METADATA_DIR, ".git")
    manifest = build_manifest(root, writes, now, snapshot_workspace=True, snapshot_excludes=snapshot_excludes)
    return {"ok": True, "drift_detected": current["drift_detected"], "proposal": proposal, "h3": h3_summary, "manifest": manifest.to_record(), "manifest_fingerprint": manifest.fingerprint()}, manifest


def append_h6_events(root: Path, *, owner_ref: str, artifacts: tuple[dict[str, Any], ...] = (), evidence: tuple[dict[str, Any], ...] = (), evaluations: tuple[dict[str, Any], ...] = (), now: str) -> ApplicationManifest:
    """Create a dry-run manifest that appends H6 events without rewriting history."""
    root = root.expanduser().resolve(strict=True)
    plan = _optional_existing(root, PLAN_REL)
    if plan is not None and owner_ref != plan.get("entity_id"):
        raise LifecycleError("ownership_conflict", "H6 event owner must be the current Plan")
    batches = (
        (ARTIFACT_EVENTS_REL, "artifact-events", "events", artifacts),
        (EVIDENCE_EVENTS_REL, "evidence-events", "events", evidence),
        (EVALUATION_EVENTS_REL, "evaluation-events", "events", evaluations),
    )
    writes: list[PlannedWrite] = []
    for rel, entity_type, key, incoming in batches:
        if not incoming and not (root / rel).is_file():
            continue
        existing = _optional_existing(root, rel)
        if existing is None:
            current_events: list[dict[str, Any]] = []
        else:
            validate(entity_type, existing)
            if existing.get("owner_ref") != owner_ref:
                raise LifecycleError("ownership_conflict", f"H6 event ledger owner mismatch: {rel}")
            if existing.get("fingerprint") != content_fingerprint({name: value for name, value in existing.items() if name != "fingerprint"}):
                raise LifecycleError("schema_incompatible", f"H6 event ledger fingerprint mismatch: {rel}")
            current_events = list(existing.get(key, []))
        by_key: dict[str, dict[str, Any]] = {}
        event_key = "event_id" if entity_type == "evaluation-events" else "entity_id"
        for event in current_events:
            validate("evaluation" if entity_type == "evaluation-events" else entity_type.removesuffix("-events"), event)
            by_key[event[event_key]] = event
        for event in incoming:
            validate("evaluation" if entity_type == "evaluation-events" else entity_type.removesuffix("-events"), event)
            if event.get("record_fingerprint") != content_fingerprint({name: value for name, value in event.items() if name != "record_fingerprint"}):
                raise LifecycleError("schema_incompatible", f"H6 event record fingerprint mismatch: {event.get(event_key)}")
            old = by_key.get(event[event_key])
            if old is not None and content_fingerprint(old) != content_fingerprint(event):
                raise LifecycleError("fingerprint_conflict", f"H6 event conflicts with existing record: {event[event_key]}")
            by_key[event[event_key]] = event
        payload_base = {"schema_version": "1.0", "entity_type": entity_type, "owner_ref": owner_ref, key: [by_key[item] for item in sorted(by_key)]}
        payload = {**payload_base, "fingerprint": content_fingerprint(payload_base)}
        validate(entity_type, payload)
        target = root / rel
        writes.append(PlannedWrite(rel, payload, exclusive=not target.is_file(), expected_before_fingerprint=bytes_fingerprint(target.read_bytes()) if target.is_file() else None))
    return build_manifest(root, writes, now, snapshot_workspace=True, snapshot_excludes=(METADATA_DIR, ".git"))


def _workflow_event_payload(entity_type: str, owner_ref: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    key = "events"
    base = {"schema_version": "1.0", "entity_type": entity_type, "owner_ref": owner_ref, key: sorted(events, key=lambda item: item.get("event_id", item.get("entity_id", "")))}
    payload = {**base, "fingerprint": content_fingerprint(base)}
    validate(entity_type, payload)
    return payload


def _completed_workflow_task(*, domain: str, owner_role: str, source_ref: str, evidence_ref: str, artifact_refs: list[str], now: str) -> dict[str, Any]:
    if not artifact_refs:
        raise LifecycleError("schema_incompatible", "workflow task requires actual artifact references")
    task = make_envelope(
        "task",
        {"domain": domain, "source": source_ref},
        source_ref,
        now,
        fields={
            "state": "proposed",
            "owner_role": owner_role,
            "budget": {},
            "retry_policy": "manual",
            "idempotency_key": content_fingerprint({"domain": domain, "source": source_ref}),
            "artifacts_owned": sorted(set(artifact_refs)),
            "transitions": [],
        },
    ).to_record()
    for target in ("ready", "running", "review", "validation", "awaiting_approval", "delivered"):
        task = transition(
            task,
            TransitionRecord(
                from_state=task["state"],
                to_state=target,
                actor=owner_role if target != "delivered" else "user",
                reason=f"{domain} workflow entered {target}",
                evidence_ref=evidence_ref,
                at=now,
                artifacts_in=tuple(sorted(set(artifact_refs))),
                artifacts_out=tuple(sorted(set(artifact_refs))),
            ),
        )
    task["updated_at"] = now
    validate("task", task)
    return task


def submit_software_workflow(root: Path, *, artifacts: list[dict[str, Any]], evidence: list[dict[str, Any]], review: dict[str, Any], verifications: list[dict[str, Any]], approval: dict[str, Any], traceability_mappings: dict[str, dict[str, list[str]]], now: str, external_release: dict[str, Any] | None = None) -> ApplicationManifest:
    """Return a dry-run manifest for the complete H4 work-product workflow."""
    root = root.expanduser().resolve(strict=True)
    workspace = _read_existing(root, HARNESS_REL)
    plan = _read_existing(root, PLAN_REL)
    plan_approval = _optional_existing(root, PLAN_APPROVAL_REL)
    contract = _optional_existing(root, SOFTWARE_CONTRACT_REL)
    if plan_approval is None or contract is None:
        raise LifecycleError("approval_missing", "software workflow requires the current Plan approval and contract")
    plan_evidence = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", plan["entity_id"], "entity_id")
    try:
        validate_plan_approval(plan_approval, plan, target_owner=workspace["entity_id"], evidence_records=plan_evidence)
    except ApprovalError as exc:
        raise LifecycleError("approval_missing", str(exc)) from exc
    if approval.get("contract_ref") != contract.get("entity_id"):
        raise LifecycleError("fingerprint_conflict", "software Approval targets a stale contract")
    validate_software_work_product_approval(approval, contract, review, verifications, target_owner=workspace["entity_id"], artifact_records=artifacts, evidence_records=evidence)
    validate("software-review", review)
    validate("verification-results", {"schema_version": "1.0", "entity_type": "verification-results", "contract_ref": contract["entity_id"], "results": verifications, "fingerprint": content_fingerprint({"schema_version": "1.0", "entity_type": "verification-results", "contract_ref": contract["entity_id"], "results": verifications})})
    for item in artifacts + evidence:
        if item.get("record_fingerprint") != content_fingerprint({key: value for key, value in item.items() if key != "record_fingerprint"}):
            raise LifecycleError("schema_incompatible", f"workflow record fingerprint mismatch: {item.get('entity_id')}")
    traceability = bind_software_traceability(
        build_software_traceability(contract),
        implementation_refs=traceability_mappings.get("implementation_refs", {}),
        evidence_refs=traceability_mappings.get("evidence_refs", {}),
        verification_refs=traceability_mappings.get("verification_refs", {}),
        actual_artifacts=artifacts,
        actual_evidence=evidence,
        actual_verifications=verifications,
    )
    if traceability["status"] != "complete":
        raise LifecycleError("verification_failed", "software traceability is not complete")
    writes: list[PlannedWrite] = []
    for rel, entity_type, incoming in ((ARTIFACT_EVENTS_REL, "artifact-events", artifacts), (EVIDENCE_EVENTS_REL, "evidence-events", evidence)):
        current = _optional_existing(root, rel)
        existing = list(current.get("events", [])) if current else []
        by_id = {item["entity_id"]: item for item in existing}
        for item in incoming:
            if item["entity_id"] in by_id and content_fingerprint(by_id[item["entity_id"]]) != content_fingerprint(item):
                raise LifecycleError("fingerprint_conflict", f"workflow event conflicts with {item['entity_id']}")
            by_id[item["entity_id"]] = item
        payload = _workflow_event_payload(entity_type, plan["entity_id"], list(by_id.values()))
        target = root / rel
        writes.append(PlannedWrite(rel, payload, exclusive=not target.is_file(), expected_before_fingerprint=bytes_fingerprint(target.read_bytes()) if target.is_file() else None))
    verification_base = {"schema_version": "1.0", "entity_type": "verification-results", "contract_ref": contract["entity_id"], "results": verifications}
    verification = {**verification_base, "fingerprint": content_fingerprint(verification_base)}
    delivery_base = {"schema_version": "1.0", "entity_type": "software-delivery-state", "contract_ref": contract["entity_id"], "artifact_refs": sorted(item["entity_id"] for item in artifacts), "review_ref": review["review_id"], "review_status": review["status"], "verification_refs": sorted(item["verification_id"] for item in verifications), "verification_status": "passed" if all(item["status"] == "passed" for item in verifications) else "blocked", "work_product_approval_ref": approval["entity_id"], "completion_status": "complete"}
    delivery = {**delivery_base, "fingerprint": content_fingerprint(delivery_base)}
    task = _completed_workflow_task(domain="software-engineering", owner_role="role:software-implementation", source_ref=contract["entity_id"], evidence_ref=approval["entity_id"], artifact_refs=[item["entity_id"] for item in artifacts], now=now)
    for rel, payload in ((SOFTWARE_REVIEW_REL, review), (SOFTWARE_TRACEABILITY_REL, traceability), (SOFTWARE_VERIFICATION_REL, verification), (SOFTWARE_APPROVAL_REL, approval), (SOFTWARE_DELIVERY_REL, delivery), (SOFTWARE_TASK_REL, task)):
        target = root / rel
        writes.append(PlannedWrite(rel, payload, exclusive=not target.is_file(), expected_before_fingerprint=bytes_fingerprint(target.read_bytes()) if target.is_file() else None))
    if external_release is not None:
        try:
            validate_canonical_approval(external_release, expected_decision="approved", expected_scope="external_release", expected_target_ref=approval["entity_id"], expected_target_owner=workspace["entity_id"], evidence_records=evidence)
        except ApprovalError as exc:
            raise LifecycleError("approval_missing", f"invalid software external release approval: {exc}") from exc
        if external_release.get("work_product_approval_ref") != approval.get("entity_id") or external_release.get("work_product_approval_fingerprint") != content_fingerprint(approval):
            raise LifecycleError("approval_missing", "software external release approval is not independently bound")
        target = root / SOFTWARE_RELEASE_APPROVAL_REL
        writes.append(PlannedWrite(SOFTWARE_RELEASE_APPROVAL_REL, external_release, exclusive=not target.is_file(), expected_before_fingerprint=bytes_fingerprint(target.read_bytes()) if target.is_file() else None))
    return build_manifest(root, writes, now, snapshot_workspace=True, snapshot_excludes=(METADATA_DIR, ".git"))


def submit_content_workflow(root: Path, *, approval: dict[str, Any], now: str, external_release: dict[str, Any] | None = None, review: dict[str, Any] | None = None, evidence_records: list[dict[str, Any]] | None = None) -> ApplicationManifest:
    """Return a dry-run manifest for the complete H5 work-product workflow."""
    root = root.expanduser().resolve(strict=True)
    workspace = _read_existing(root, HARNESS_REL)
    plan = _read_existing(root, PLAN_REL)
    plan_approval = _optional_existing(root, PLAN_APPROVAL_REL)
    if plan_approval is None:
        raise LifecycleError("approval_missing", "content workflow requires the current Plan approval")
    plan_evidence = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", plan["entity_id"], "entity_id")
    try:
        validate_plan_approval(plan_approval, plan, target_owner=workspace["entity_id"], evidence_records=plan_evidence)
    except ApprovalError as exc:
        raise LifecycleError("approval_missing", str(exc)) from exc
    brief = _read_existing(root, CONTENT_BRIEF_REL)
    variants_record = _read_existing(root, CONTENT_VARIANTS_REL)
    review = review or _read_existing(root, CONTENT_REVIEW_REL)
    variant = next((item for item in variants_record["variants"] if item.get("variant_id") == review.get("selected_variant_ref")), None)
    if variant is None:
        raise LifecycleError("approval_missing", "content workflow has no selected variant")
    existing_evidence = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", plan["entity_id"], "entity_id")
    incoming_evidence = list(evidence_records or [])
    evidence_by_id = {item["entity_id"]: item for item in existing_evidence}
    for item in incoming_evidence:
        previous = evidence_by_id.get(item.get("entity_id"))
        if previous is not None and content_fingerprint(previous) != content_fingerprint(item):
            raise LifecycleError("fingerprint_conflict", f"content Evidence conflicts with {item.get('entity_id')}")
        evidence_by_id[item.get("entity_id")] = item
    resolved_evidence = list(evidence_by_id.values())
    validate_content_approval(approval, brief, variant, review, target_owner=workspace["entity_id"], evidence_records=resolved_evidence)
    existing_delivery = _optional_existing(root, CONTENT_DELIVERY_REL)
    existing_approval = _optional_existing(root, CONTENT_APPROVAL_REL)
    if existing_delivery is not None or existing_approval is not None:
        if existing_delivery is None or existing_approval is None:
            raise LifecycleError("fingerprint_conflict", "content workflow has a partial prior approval state")
        try:
            validate("content-delivery-state", existing_delivery)
        except Exception as exc:
            raise LifecycleError("schema_incompatible", f"existing content delivery is invalid: {exc}") from exc
        same_approval = (
            existing_approval.get("entity_id") == approval.get("entity_id")
            and content_fingerprint(existing_approval) == content_fingerprint(approval)
        )
        same_delivery = (
            brief.get("lifecycle_state") == "approved"
            and existing_delivery.get("brief_ref") == brief["entity_id"]
            and existing_delivery.get("variant_ref") == variant["variant_id"]
            and existing_delivery.get("review_ref") == review["review_id"]
            and existing_delivery.get("work_product_approval_ref") == approval["entity_id"]
            and existing_delivery.get("lifecycle_state") == "approved"
        )
        if same_approval and same_delivery:
            if external_release is None:
                return build_manifest(root, [], now, snapshot_workspace=True, snapshot_excludes=(METADATA_DIR, ".git"))
            validate_content_approval(external_release, brief, variant, review, target_owner=workspace["entity_id"], work_product_approval=approval, expected_scope="external_release", evidence_records=resolved_evidence)
            existing_release = _optional_existing(root, CONTENT_RELEASE_APPROVAL_REL)
            if existing_release is not None and content_fingerprint(existing_release) != content_fingerprint(external_release):
                raise LifecycleError("fingerprint_conflict", "content external release is already approved with a different record")
            release_writes: list[PlannedWrite] = []
            if existing_release is None:
                release_writes.append(PlannedWrite(CONTENT_RELEASE_APPROVAL_REL, external_release, exclusive=True))
            if existing_delivery.get("external_release_state") != "approved":
                updated_delivery_base = {
                    **{key: value for key, value in existing_delivery.items() if key != "fingerprint"},
                    "external_release_state": "approved",
                }
                updated_delivery = {**updated_delivery_base, "fingerprint": content_fingerprint(updated_delivery_base)}
                delivery_target = root / CONTENT_DELIVERY_REL
                release_writes.append(PlannedWrite(CONTENT_DELIVERY_REL, updated_delivery, exclusive=False, expected_before_fingerprint=bytes_fingerprint(delivery_target.read_bytes())))
            return build_manifest(root, release_writes, now, snapshot_workspace=True, snapshot_excludes=(METADATA_DIR, ".git"))
        raise LifecycleError("fingerprint_conflict", "content workflow is already approved with different content or approval")
    writes: list[PlannedWrite] = []
    delivery_base = {"schema_version": "1.0", "entity_type": "content-delivery-state", "brief_ref": brief["entity_id"], "brief_fingerprint": _brief_binding_fingerprint(brief), "variant_ref": variant["variant_id"], "review_ref": review["review_id"], "review_fingerprint": review["fingerprint"], "work_product_approval_ref": approval["entity_id"], "lifecycle_state": "approved", "external_release_state": "approved" if external_release is not None else "unapproved"}
    delivery = {**delivery_base, "fingerprint": content_fingerprint(delivery_base)}
    evolved_brief = brief
    transition_evidence = approval["entity_id"]
    for target_state in ("outline", "draft", "variants", "review", "awaiting_approval"):
        evolved_brief = transition_content(evolved_brief, target_state, actor="role:content-production", reason=f"content workflow entered {target_state}", evidence_ref=transition_evidence, at=now)
    evolved_brief = transition_content(evolved_brief, "approved", actor="user", reason="content work product approved", evidence_ref=approval["entity_id"], at=now, approval=approval, variant=variant, review=review, target_owner=workspace["entity_id"], current_brief=brief, evidence_records=resolved_evidence)
    task = _completed_workflow_task(domain="content-script", owner_role="role:content-production", source_ref=brief["entity_id"], evidence_ref=approval["entity_id"], artifact_refs=[variant["variant_id"]], now=now)
    for rel, payload in ((CONTENT_BRIEF_REL, evolved_brief), (CONTENT_REVIEW_REL, review), (CONTENT_APPROVAL_REL, approval), (CONTENT_DELIVERY_REL, delivery), (CONTENT_TASK_REL, task)):
        target = root / rel
        writes.append(PlannedWrite(rel, payload, exclusive=not target.is_file(), expected_before_fingerprint=bytes_fingerprint(target.read_bytes()) if target.is_file() else None))
    if external_release is not None:
        validate_content_approval(external_release, brief, variant, review, target_owner=workspace["entity_id"], work_product_approval=approval, expected_scope="external_release", evidence_records=resolved_evidence)
        target = root / CONTENT_RELEASE_APPROVAL_REL
        writes.append(PlannedWrite(CONTENT_RELEASE_APPROVAL_REL, external_release, exclusive=not target.is_file(), expected_before_fingerprint=bytes_fingerprint(target.read_bytes()) if target.is_file() else None))
    if incoming_evidence:
        event_manifest = append_h6_events(root, owner_ref=plan["entity_id"], evidence=tuple(incoming_evidence), now=now)
        writes.extend(event_manifest.writes)
    return build_manifest(root, writes, now, snapshot_workspace=True, snapshot_excludes=(METADATA_DIR, ".git"))


def _validate_persisted_software_work_product(root: Path, workspace: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    work_approval = _read_existing(root, SOFTWARE_APPROVAL_REL)
    contract = _read_existing(root, SOFTWARE_CONTRACT_REL)
    review = _read_existing(root, SOFTWARE_REVIEW_REL)
    verification = _read_existing(root, SOFTWARE_VERIFICATION_REL)
    all_artifacts = _read_h6_events(root, ARTIFACT_EVENTS_REL, "artifact-events", plan["entity_id"], "entity_id")
    evidence = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", plan["entity_id"], "entity_id")
    artifacts = [item for item in all_artifacts if item.get("entity_id") in set(work_approval.get("artifact_refs", []))]
    approval_evidence = [item for item in evidence if item.get("entity_id") in set(work_approval.get("evidence_refs", []))]
    validate_software_work_product_approval(
        work_approval,
        contract,
        review,
        verification["results"],
        target_owner=workspace["entity_id"],
        artifact_records=artifacts,
        evidence_records=approval_evidence,
    )
    return work_approval


def _validate_persisted_software_release(root: Path, workspace: dict[str, Any], plan: dict[str, Any]) -> None:
    release = _read_existing(root, SOFTWARE_RELEASE_APPROVAL_REL)
    work_approval = _validate_persisted_software_work_product(root, workspace, plan)
    evidence = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", plan["entity_id"], "entity_id")
    validate_canonical_approval(
        release,
        expected_decision="approved",
        expected_scope="external_release",
        expected_target_ref=work_approval["entity_id"],
        expected_target_owner=workspace["entity_id"],
        evidence_records=evidence,
    )
    if (
        release.get("work_product_approval_ref") != work_approval.get("entity_id")
        or release.get("work_product_approval_fingerprint") != content_fingerprint(work_approval)
    ):
        raise ApprovalError("software external release approval is not bound to the current work-product approval")


def _validate_persisted_content_release(root: Path, workspace: dict[str, Any], plan: dict[str, Any]) -> None:
    release = _read_existing(root, CONTENT_RELEASE_APPROVAL_REL)
    work_approval = _read_existing(root, CONTENT_APPROVAL_REL)
    brief = _read_existing(root, CONTENT_BRIEF_REL)
    variants = _read_existing(root, CONTENT_VARIANTS_REL)
    review = _read_existing(root, CONTENT_REVIEW_REL)
    variant = next(item for item in variants["variants"] if item["variant_id"] == review["selected_variant_ref"])
    evidence = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", plan["entity_id"], "entity_id")
    validate_content_approval(
        release,
        brief,
        variant,
        review,
        target_owner=workspace["entity_id"],
        work_product_approval=work_approval,
        expected_scope="external_release",
        evidence_records=evidence,
    )


def _validate_persisted_content_work_product(root: Path, workspace: dict[str, Any], plan: dict[str, Any]) -> None:
    work_approval = _read_existing(root, CONTENT_APPROVAL_REL)
    brief = _read_existing(root, CONTENT_BRIEF_REL)
    variants = _read_existing(root, CONTENT_VARIANTS_REL)
    review = _read_existing(root, CONTENT_REVIEW_REL)
    variant = next(item for item in variants["variants"] if item["variant_id"] == review["selected_variant_ref"])
    evidence = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", plan["entity_id"], "entity_id")
    validate_content_approval(
        work_approval,
        brief,
        variant,
        review,
        target_owner=workspace["entity_id"],
        evidence_records=evidence,
    )


def doctor_workspace(root: Path, now: str) -> dict[str, Any]:
    errors: list[str] = []
    discovery = discover_workspace(root, now)
    if _complete_existing(root):
        records = {
            "workspace": _read_existing(root, HARNESS_REL),
            "profile": _read_existing(root, PROFILE_REL),
            "unresolved": _read_existing(root, UNRESOLVED_REL),
            "authority": _read_existing(root, AUTHORITY_REL),
            "plan": _read_existing(root, PLAN_REL),
        }
        for entity_type, record in (("workspace", records["workspace"]), ("workspace-profile", records["profile"]), ("unresolved", records["unresolved"]), ("authority-map", records["authority"]), ("plan", records["plan"])):
            try:
                validate(entity_type, record)
            except Exception as exc:
                errors.append(f"{entity_type}: {exc}")
        for item in records["profile"].get("records", []):
            try:
                validate("profile-record", item)
            except Exception as exc:
                errors.append(f"profile-record: {exc}")
        if (root / GENERATED_REL).is_file():
            try:
                generated = _read_existing(root, GENERATED_REL)
                validate("generated-files", generated)
                generated_base = {key: value for key, value in generated.items() if key != "registry_fingerprint"}
                if generated.get("registry_fingerprint") != content_fingerprint(generated_base):
                    errors.append("generated file registry fingerprint mismatch")
            except Exception as exc:
                errors.append(f"generated-files: {exc}")
        if records["profile"].get("source_fingerprint") != discovery.source_fingerprint:
            errors.append("profile source fingerprint is stale; reconcile required")
        plan_approval: dict[str, Any] | None = None
        evidence_records: list[dict[str, Any]] = []
        try:
            evidence_records = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", records["plan"]["entity_id"], "entity_id")
        except LifecycleError as exc:
            errors.append(f"Plan Evidence: {exc}")
        if records["plan"].get("approval_state") == "approved":
            try:
                approval = _optional_existing(root, PLAN_APPROVAL_REL)
                if approval is None:
                    raise ApprovalError("canonical Plan approval is missing")
                validate_plan_approval(
                    approval,
                    records["plan"],
                    target_owner=records["workspace"]["entity_id"],
                    evidence_records=evidence_records,
                )
                plan_approval = approval
            except (ApprovalError, KeyError) as exc:
                errors.append(f"Plan approval: {exc}")
            _, overlay_errors = _user_roles(root)
            errors.extend(f"user-role-overlay: {item}" for item in overlay_errors)
            for rel, entity_type in ((ROLE_REGISTRY_REL, "role-registry"), (ROLE_RECONCILIATION_REL, "role-reconciliation"), (CONTEXT_ROUTES_REL, "context-routes"), (ADAPTER_STATE_REL, "adapter-state")):
                record = _optional_existing(root, rel)
                if record is None:
                    errors.append(f"{entity_type}: generated record is missing")
                    continue
                try:
                    validate(entity_type, record)
                except Exception as exc:
                    errors.append(f"{entity_type}: {exc}")
            registry = _optional_existing(root, ROLE_REGISTRY_REL)
            if registry:
                registry_base = {key: value for key, value in registry.items() if key != "registry_fingerprint"}
                if registry.get("registry_fingerprint") != content_fingerprint(registry_base):
                    errors.append("role registry fingerprint mismatch")
                seen_owns: dict[str, str] = {}
                for role in registry.get("roles", []):
                    role_id = role.get("role_id", role.get("entity_id"))
                    if role.get("contract_fingerprint") != role_contract_fingerprint(role):
                        errors.append(f"role contract fingerprint mismatch: {role_id}")
                    if role_id != role.get("entity_id"):
                        errors.append(f"role {role_id}: role_id/entity_id mismatch")
                    for owned in role.get("owns", []):
                        previous = seen_owns.get(owned)
                        if previous and previous != role_id:
                            errors.append(f"role ownership conflict: {owned}")
                        seen_owns[owned] = role_id
                    for transition in role.get("lifecycle_history", []):
                        from_state = transition.get("from_state")
                        to_state = transition.get("to_state")
                        if from_state != "none" and to_state not in ROLE_TRANSITIONS.get(from_state, frozenset()):
                            errors.append(f"role lifecycle transition invalid: {role_id}")
            routes = _optional_existing(root, CONTEXT_ROUTES_REL)
            if routes:
                routes_base = {key: value for key, value in routes.items() if key != "routing_fingerprint"}
                if routes.get("routing_fingerprint") != content_fingerprint(routes_base):
                    errors.append("context routing fingerprint mismatch")
                for route in routes.get("routes", []):
                    if route.get("estimated_tokens", 0) > route.get("max_token_budget", 0):
                        errors.append(f"context route budget exceeded: {route.get('route_id')}")
                    if len(route.get("source_refs", [])) != len(route.get("source_fingerprints", [])):
                        errors.append(f"context route source mapping mismatch: {route.get('route_id')}")
                    for source_ref in route.get("source_refs", []):
                        if any(marker in source_ref.lower() for marker in ("history", "evolution", "trace", "prompt", "private", "secret", "credential", "employer", "personal", "api_key", "token")):
                            errors.append(f"context route crosses source boundary: {route.get('route_id')}")
            adapter_state = _optional_existing(root, ADAPTER_STATE_REL)
            if adapter_state:
                adapter_base = {"runtime": adapter_state.get("runtime"), "workspace": adapter_state.get("workspace")}
                if adapter_state.get("state_fingerprint") != content_fingerprint(adapter_base):
                    errors.append("adapter state fingerprint mismatch")
                for adapter in (adapter_state.get("runtime", {}), adapter_state.get("workspace", {})):
                    try:
                        validate("adapter-contract", adapter)
                    except Exception as exc:
                        errors.append(f"adapter-contract: {exc}")
                    missing = adapter.get("missing", [])
                    degradation = adapter.get("degradation", {})
                    if any(capability not in degradation for capability in missing):
                        errors.append(f"adapter fallback missing: {adapter.get('adapter_id')}")
            reconciliation = _optional_existing(root, ROLE_RECONCILIATION_REL)
            if reconciliation:
                reconciliation_base = {key: value for key, value in reconciliation.items() if key != "fingerprint"}
                if reconciliation.get("fingerprint") != content_fingerprint(reconciliation_base):
                    errors.append("role reconciliation fingerprint mismatch")
            requested_pack = _requested_domain_pack(records["plan"])
            if requested_pack:
                domain_expected = _domain_bundle(records["plan"], plan_approval=plan_approval, target_owner=records["workspace"]["entity_id"], evidence_records=evidence_records)
                if domain_expected.get("status") != "proposed":
                    errors.append(f"domain pack: {domain_expected.get('reason', domain_expected.get('error', 'blocked'))}")
                else:
                    if domain_expected.get("pack", {}).get("pack_id") == "content-script":
                        evidence_events = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", records["plan"]["entity_id"], "entity_id")
                        brief_id = domain_expected["brief"]["entity_id"]
                        variant_ids = {item["variant_id"] for item in domain_expected["variants"]["variants"]}
                        review_refs = sorted(
                            item["entity_id"] for item in evidence_events
                            if set(item.get("supporting_refs", [])) & ({brief_id} | variant_ids)
                        )
                        if review_refs:
                            domain_expected["review"] = review_content(
                                domain_expected["brief"], domain_expected["variants"]["variants"], now=records["plan"]["updated_at"],
                                selected_variant_id=records["plan"].get("scope", {}).get("selected_variant_id"), evidence_refs=review_refs,
                            )
                    if domain_expected.get("pack", {}).get("pack_id") == "software-engineering":
                        domain_records = (
                            (SOFTWARE_PACK_REL, "domain-pack-manifest", "pack"),
                            (SOFTWARE_CONTRACT_REL, "software-output-contract", "contract"),
                            (SOFTWARE_TRACEABILITY_REL, "traceability", "traceability"),
                            (SOFTWARE_VERIFICATION_REL, "verification-results", "verification"),
                            (SOFTWARE_DELIVERY_REL, "software-delivery-state", "delivery"),
                        )
                    else:
                        domain_records = (
                            (CONTENT_PACK_REL, "domain-pack-manifest", "pack"),
                            (CONTENT_BRIEF_REL, "content-brief", "brief"),
                            (CONTENT_CONTRACT_REL, "content-output-contract", "contract"),
                            (CONTENT_VARIANTS_REL, "content-variants", "variants"),
                            (CONTENT_REVIEW_REL, "content-review", "review"),
                        )
                    for rel, entity_type, key in domain_records:
                        actual = _optional_existing(root, rel)
                        if actual is None:
                            errors.append(f"{entity_type}: generated record is missing")
                            continue
                        try:
                            validate(entity_type, actual)
                            expected_fingerprint = (
                                _brief_binding_fingerprint(domain_expected[key])
                                if key == "brief"
                                else content_fingerprint(domain_expected[key])
                            )
                            actual_fingerprint = (
                                _brief_binding_fingerprint(actual)
                                if key == "brief"
                                else content_fingerprint(actual)
                            )
                            if not (key in {"delivery", "verification", "traceability"} and (root / SOFTWARE_APPROVAL_REL).is_file()) and actual_fingerprint != expected_fingerprint:
                                errors.append(f"{entity_type}: derived contract drift")
                        except Exception as exc:
                            errors.append(f"{entity_type}: {exc}")
                    if domain_expected.get("pack", {}).get("pack_id") == "software-engineering" and (root / SOFTWARE_APPROVAL_REL).is_file():
                        try:
                            contract = _read_existing(root, SOFTWARE_CONTRACT_REL)
                            review = _read_existing(root, SOFTWARE_REVIEW_REL)
                            verification = _read_existing(root, SOFTWARE_VERIFICATION_REL)
                            approval = _read_existing(root, SOFTWARE_APPROVAL_REL)
                            all_artifacts = _read_h6_events(root, ARTIFACT_EVENTS_REL, "artifact-events", records["plan"]["entity_id"], "entity_id")
                            all_evidence = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", records["plan"]["entity_id"], "entity_id")
                            artifacts = [item for item in all_artifacts if item.get("entity_id") in set(approval.get("artifact_refs", []))]
                            evidence = [item for item in all_evidence if item.get("entity_id") in set(approval.get("evidence_refs", []))]
                            validate_software_work_product_approval(approval, contract, review, verification["results"], target_owner=records["workspace"]["entity_id"], artifact_records=artifacts, evidence_records=evidence)
                            traceability = _read_existing(root, SOFTWARE_TRACEABILITY_REL)
                            if traceability.get("status") != "complete":
                                errors.append("software traceability is not complete")
                        except Exception as exc:
                            errors.append(f"software workflow approval: {exc}")
                    if domain_expected.get("pack", {}).get("pack_id") == "content-script" and (root / CONTENT_APPROVAL_REL).is_file():
                        try:
                            brief = _read_existing(root, CONTENT_BRIEF_REL)
                            variants = _read_existing(root, CONTENT_VARIANTS_REL)
                            review = _read_existing(root, CONTENT_REVIEW_REL)
                            approval = _read_existing(root, CONTENT_APPROVAL_REL)
                            variant = next(item for item in variants["variants"] if item["variant_id"] == review["selected_variant_ref"])
                            evidence = _read_h6_events(root, EVIDENCE_EVENTS_REL, "evidence-events", records["plan"]["entity_id"], "entity_id")
                            validate_content_approval(approval, brief, variant, review, target_owner=records["workspace"]["entity_id"], evidence_records=evidence)
                            delivery = _optional_existing(root, CONTENT_DELIVERY_REL)
                            if delivery is None or delivery.get("fingerprint") != content_fingerprint({key: value for key, value in delivery.items() if key != "fingerprint"}):
                                errors.append("content delivery state fingerprint mismatch")
                        except Exception as exc:
                            errors.append(f"content workflow approval: {exc}")
            if (root / SOFTWARE_RELEASE_APPROVAL_REL).is_file():
                try:
                    _validate_persisted_software_release(root, records["workspace"], records["plan"])
                except Exception as exc:
                    errors.append(f"software external release approval: {exc}")
            if (root / CONTENT_RELEASE_APPROVAL_REL).is_file():
                try:
                    _validate_persisted_content_release(root, records["workspace"], records["plan"])
                except Exception as exc:
                    errors.append(f"content external release approval: {exc}")
            for event_rel, event_type in ((ARTIFACT_EVENTS_REL, "artifact-events"), (EVIDENCE_EVENTS_REL, "evidence-events"), (EVALUATION_EVENTS_REL, "evaluation-events")):
                    event_record = _optional_existing(root, event_rel)
                    if event_record is not None:
                        try:
                            validate(event_type, event_record)
                            if event_record.get("owner_ref") != records["plan"]["entity_id"]:
                                errors.append(f"{event_type}: owner mismatch")
                            if event_record.get("fingerprint") != content_fingerprint({key: value for key, value in event_record.items() if key != "fingerprint"}):
                                errors.append(f"{event_type}: ledger fingerprint mismatch")
                            for event in event_record.get("events", []):
                                if event.get("record_fingerprint") != content_fingerprint({key: value for key, value in event.items() if key != "record_fingerprint"}):
                                    errors.append(f"{event_type}: event fingerprint mismatch")
                        except Exception as exc:
                            errors.append(f"{event_type}: {exc}")
            expected_h6 = _h6_bundle(root, records["plan"], now, plan_approval=plan_approval)
            if expected_h6.get("status") == "proposed":
                h6_records = (
                    (ARTIFACT_LEDGER_REL, "artifact-ledger", "artifacts"),
                    (EVIDENCE_LEDGER_REL, "evidence-ledger", "evidence"),
                    (CHECKPOINT_REL, "checkpoint", "checkpoint"),
                    (HANDOFF_REL, "handoff", "handoff"),
                    (EVALUATION_LEDGER_REL, "evaluation-ledger", "evaluations"),
                    (GOVERNANCE_EFFECT_REL, "governance-effect", "governance_effect"),
                )
                for rel, entity_type, key in h6_records:
                    actual = _optional_existing(root, rel)
                    if actual is None:
                        errors.append(f"{entity_type}: generated record is missing")
                        continue
                    try:
                        validate(entity_type, actual)
                        if content_fingerprint(actual) != content_fingerprint(expected_h6[key]):
                            errors.append(f"{entity_type}: generated record drift")
                        if entity_type == "evaluation-ledger":
                            errors.extend(f"unconsumed evaluation: {item.get('evaluation_id')}" for item in actual.get("evaluations", []) if item.get("consumption") != "consumed")
                        if entity_type == "handoff":
                            embedded = actual.get("checkpoint", {})
                            if embedded.get("fingerprint") != content_fingerprint({key: value for key, value in embedded.items() if key != "fingerprint"}):
                                errors.append("handoff checkpoint fingerprint mismatch")
                    except Exception as exc:
                        errors.append(f"{entity_type}: {exc}")
    else:
        errors.append("workspace is not initialized")
    overlay = root / METADATA_DIR / "roles" / "user"
    return {"ok": not errors, "errors": errors, "profile_source_fingerprint": discovery.source_fingerprint, "user_overlay_present": overlay.is_dir(), "h3": _h3_diff(root, _read_existing(root, PROFILE_REL), _read_existing(root, PLAN_REL), now) if _complete_existing(root) else {"status": "uninitialized"}}


def status_workspace(root: Path, now: str) -> dict[str, Any]:
    if not _complete_existing(root):
        return {"ok": True, "initialized": False, "lifecycle": "uninitialized"}
    workspace = _read_existing(root, HARNESS_REL)
    plan = _read_existing(root, PLAN_REL)
    unresolved = _read_existing(root, UNRESOLVED_REL)
    current = diff_workspace(root, now)
    role_registry = _optional_existing(root, ROLE_REGISTRY_REL)
    routes = _optional_existing(root, CONTEXT_ROUTES_REL)
    adapter_state = _optional_existing(root, ADAPTER_STATE_REL)
    domain_status = current.get("h3", {}).get("domain", {"status": "not-enabled"})
    software_approval_state = "unapproved"
    if (root / SOFTWARE_APPROVAL_REL).is_file():
        try:
            _validate_persisted_software_work_product(root, workspace, plan)
            software_approval_state = "approved"
        except Exception:
            software_approval_state = "invalidated"
    software_release_state = "unapproved"
    if (root / SOFTWARE_RELEASE_APPROVAL_REL).is_file():
        try:
            _validate_persisted_software_release(root, workspace, plan)
            software_release_state = "approved"
        except Exception:
            software_release_state = "invalidated"
    content_approval_state = "unapproved"
    if (root / CONTENT_APPROVAL_REL).is_file():
        try:
            _validate_persisted_content_work_product(root, workspace, plan)
            content_approval_state = "approved"
        except Exception:
            content_approval_state = "invalidated"
    content_release_state = "unapproved"
    if (root / CONTENT_RELEASE_APPROVAL_REL).is_file():
        try:
            _validate_persisted_content_release(root, workspace, plan)
            content_release_state = "approved"
        except Exception:
            content_release_state = "invalidated"
    h4 = {**domain_status, "delivery": _optional_existing(root, SOFTWARE_DELIVERY_REL) if (root / SOFTWARE_DELIVERY_REL).is_file() else None, "approval": _optional_existing(root, SOFTWARE_APPROVAL_REL) is not None, "work_product_approval_state": software_approval_state, "external_release_state": software_release_state}
    h5 = {"delivery": _optional_existing(root, CONTENT_DELIVERY_REL) if (root / CONTENT_DELIVERY_REL).is_file() else None, "approval": _optional_existing(root, CONTENT_APPROVAL_REL) is not None, "work_product_approval_state": content_approval_state, "external_release_state": content_release_state}
    return {"ok": True, "initialized": True, "lifecycle": workspace.get("lifecycle_state"), "plan_approval": plan.get("approval_state"), "unresolved_count": len(unresolved.get("items", [])), "drift_detected": current["drift_detected"], "user_overlay_present": (root / METADATA_DIR / "roles" / "user").is_dir(), "h3": {"status": current.get("h3", {}).get("status"), "role_count": len((role_registry or {}).get("roles", [])), "route_count": len((routes or {}).get("routes", [])), "adapter_ids": [adapter.get("adapter_id") for adapter in ((adapter_state or {}).get("runtime", {}), (adapter_state or {}).get("workspace", {})) if adapter], "unresolved": current.get("h3", {}).get("user_overlay_errors", [])}, "h4": h4, "h5": h5, "h6": current.get("h3", {}).get("h6", {"status": "not-enabled"})}


def upgrade_workspace(root: Path, now: str) -> dict[str, Any]:
    if not _complete_existing(root):
        return {"ok": True, "proposal": "init required", "manifest": None}
    current = diff_workspace(root, now)
    if not current.get("drift_detected"):
        plan = {"schema_version": "1.0", "entity_type": "upgrade-proposal", "from_version": "1.0", "to_version": "1.0", "status": "no-change", "generated_at": now}
        return {"ok": True, "proposal": plan, "manifest": None}
    proposal, manifest = reconcile_workspace(root, now)
    return {"ok": True, "proposal": {"schema_version": "1.0", "entity_type": "upgrade-proposal", "from_version": "1.0", "to_version": "1.0", "status": "proposed", "reconcile": proposal.get("proposal")}, "manifest": manifest.to_record() if manifest else None, "manifest_fingerprint": manifest.fingerprint() if manifest else None, "_manifest": manifest}


def retire_generated(root: Path, approval: dict[str, Any] | None = None, *, apply: bool = False) -> dict[str, Any]:
    registry_path = root / GENERATED_REL
    if not registry_path.is_file():
        return {"ok": True, "retireable": [], "retired": 0, "proposal_fingerprint": content_fingerprint({"files": []})}
    registry = _read_existing(root, GENERATED_REL)
    registry_base = {key: value for key, value in registry.items() if key != "registry_fingerprint"}
    if registry.get("registry_fingerprint") != content_fingerprint(registry_base):
        raise LifecycleError("user_file_conflict", "generated file registry fingerprint mismatch")
    files = registry.get("files", [])
    checks: list[dict[str, Any]] = []
    for entry in files:
        rel = normalize_rel_path(entry.get("rel", ""))
        target = resolve_in_root(root, rel)
        if not target.is_file():
            raise LifecycleError("user_file_conflict", f"generated target is missing: {rel}")
        actual = bytes_fingerprint(target.read_bytes())
        if actual != entry.get("fingerprint"):
            raise LifecycleError("user_file_conflict", f"generated target changed: {rel}")
        checks.append({"rel": rel, "fingerprint": actual})
    proposal = {
        "schema_version": "1.0",
        "entity_type": "retire-proposal",
        "files": checks,
        "user_files": "preserved",
        "workspace_fingerprint": tree_fingerprint(root, (METADATA_DIR, ".git")),
    }
    proposal_fp = content_fingerprint(proposal)
    if not apply:
        return {"ok": True, "retireable": checks, "retired": 0, "proposal": proposal, "proposal_fingerprint": proposal_fp}
    if not approval or approval.get("decision") != "approved" or approval.get("approved_manifest_fingerprint") != proposal_fp:
        raise LifecycleError("approval_missing", "retire requires approval bound to the proposal fingerprint")
    with write_lock(root):
        if tree_fingerprint(root, (METADATA_DIR, ".git")) != proposal["workspace_fingerprint"]:
            raise LifecycleError("fingerprint_conflict", "workspace changed since retire proposal")
        # The proposal check is repeated at the deletion boundary. A user edit
        # between proposal and apply therefore cannot be silently removed.
        for item in checks:
            target = resolve_in_root(root, item["rel"])
            if not target.is_file() or bytes_fingerprint(target.read_bytes()) != item["fingerprint"]:
                raise LifecycleError("user_file_conflict", f"generated target changed: {item['rel']}")
        snapshots = {item["rel"]: resolve_in_root(root, item["rel"]).read_bytes() for item in checks}
        try:
            for item in checks:
                resolve_in_root(root, item["rel"]).unlink()
            registry_path.unlink(missing_ok=True)
        except OSError as exc:
            for rel, data in snapshots.items():
                try:
                    write_atomic(root, rel, json.loads(data.decode("utf-8")), exclusive=True)
                except Exception:
                    pass
            raise LifecycleError("internal_invariant", "retire failed and attempted recovery") from exc
    return {"ok": True, "retireable": checks, "retired": len(checks), "proposal_fingerprint": proposal_fp}
