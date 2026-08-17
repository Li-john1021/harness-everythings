"""H3 lifecycle integration fixtures with real generated files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_everythings.core.lifecycle import (
    ADAPTER_STATE_REL,
    CONTEXT_ROUTES_REL,
    ROLE_RECONCILIATION_REL,
    ROLE_REGISTRY_REL,
    PLAN_APPROVAL_REL,
    LifecycleError,
    apply_init_proposal,
    append_h6_events,
    build_init_proposal,
    diff_workspace,
    doctor_workspace,
    reconcile_workspace,
    retire_generated,
    status_workspace,
)
from harness_everythings.core.approvals_roles import make_plan_approval
from harness_everythings.core.evidence import make_evidence
from harness_everythings.core.roles import propose_roles
from harness_everythings.storage.atomic import read_json
from harness_everythings.storage.atomic import write_atomic
from harness_everythings.storage.manifest import ManifestError, apply_manifest

NOW = "2026-08-16T00:00:00Z"


def approval(fingerprint: str) -> dict[str, str]:
    return {
        "approver": "user",
        "scope": "h3-lifecycle",
        "decision": "approved",
        "approved_manifest_fingerprint": fingerprint,
    }


def init_workspace(root: Path) -> None:
    proposal = build_init_proposal(root, "new", NOW)
    assert proposal.manifest is not None
    apply_init_proposal(root, proposal, approval(proposal.manifest.fingerprint()))


def approve_plan(root: Path) -> None:
    plan_path = root / ".harness-everythings" / "plans" / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["approval_state"] = "approved"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    workspace = read_json(root, ".harness-everythings/harness.json")
    evidence = make_evidence(
        actor="user", action="plan-approval", conclusion_kind="user_confirmed",
        supporting_refs=[plan["entity_id"]], verification_level="fixture_verified",
        source_ref="fixture:h3-plan-approval", now=NOW,
    )
    evidence_manifest = append_h6_events(root, owner_ref=plan["entity_id"], evidence=(evidence,), now=NOW)
    apply_manifest(root, evidence_manifest, approval(evidence_manifest.fingerprint()))
    approval_record = make_plan_approval(
        plan,
        requester="role:governance-coordinator",
        approver="user",
        target_owner=workspace["entity_id"],
        evidence_refs=[evidence["entity_id"]],
        decided_at=NOW,
    )
    from harness_everythings.storage.atomic import write_atomic

    write_atomic(root, PLAN_APPROVAL_REL, approval_record, exclusive=True)


class TestH3Lifecycle:
    def test_reconcile_is_dry_run_then_applies_all_h3_records(self, tmp_path: Path):
        init_workspace(tmp_path)
        approve_plan(tmp_path)
        overlay = tmp_path / ".harness-everythings" / "roles" / "user" / "local.json"
        overlay.parent.mkdir(parents=True)
        overlay.write_bytes(b"user overlay bytes\r\n")
        before_overlay = overlay.read_bytes()

        dry_result, manifest = reconcile_workspace(tmp_path, NOW)
        assert manifest is not None
        assert dry_result["h3"]["status"] == "proposed"
        assert not (tmp_path / ROLE_REGISTRY_REL).exists()
        assert not (tmp_path / CONTEXT_ROUTES_REL).exists()
        assert not (tmp_path / ADAPTER_STATE_REL).exists()
        applied = apply_manifest(tmp_path, manifest, approval(manifest.fingerprint()))
        assert applied["applied"] == 6
        assert overlay.read_bytes() == before_overlay
        for rel in (ROLE_REGISTRY_REL, ROLE_RECONCILIATION_REL, CONTEXT_ROUTES_REL, ADAPTER_STATE_REL):
            assert (tmp_path / rel).is_file()
        assert doctor_workspace(tmp_path, NOW)["ok"] is True
        assert diff_workspace(tmp_path, NOW)["drift_detected"] is False
        status = status_workspace(tmp_path, NOW)
        assert status["h3"]["role_count"] == 2
        assert status["h3"]["route_count"] == 2

    def test_reconcile_apply_is_repeatable_and_user_overlay_wins(self, tmp_path: Path):
        init_workspace(tmp_path)
        approve_plan(tmp_path)
        first, manifest = reconcile_workspace(tmp_path, NOW)
        assert manifest is not None
        apply_manifest(tmp_path, manifest, approval(manifest.fingerprint()))
        overlay = tmp_path / ".harness-everythings" / "roles" / "user" / "local.json"
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_bytes(b"keep exactly\r\n")
        before = overlay.read_bytes()
        second, repeat_manifest = reconcile_workspace(tmp_path, NOW)
        assert repeat_manifest is not None
        apply_manifest(tmp_path, repeat_manifest, approval(repeat_manifest.fingerprint()))
        assert second["h3"]["role_reconciliation"]["conflicts"] == []
        assert overlay.read_bytes() == before
        assert doctor_workspace(tmp_path, NOW)["ok"] is True

    def test_user_role_ownership_prevents_generated_claim(self, tmp_path: Path):
        init_workspace(tmp_path)
        approve_plan(tmp_path)
        user_role = propose_roles(
            read_json(tmp_path, ".harness-everythings/profile/workspace-profile.json"),
            plan_approval_state="approved",
            now=NOW,
        )["roles"][0]
        user_role["generation_origin"] = "user"
        overlay = tmp_path / ".harness-everythings" / "roles" / "user" / "governance.json"
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(json.dumps(user_role, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        _, manifest = reconcile_workspace(tmp_path, NOW)
        assert manifest is not None
        apply_manifest(tmp_path, manifest, approval(manifest.fingerprint()))
        registry = read_json(tmp_path, ROLE_REGISTRY_REL)
        assert user_role["role_id"] not in {role["role_id"] for role in registry["roles"]}
        assert overlay.is_file()

    def test_apply_rejects_workspace_change_without_partial_h3_write(self, tmp_path: Path):
        init_workspace(tmp_path)
        approve_plan(tmp_path)
        _, manifest = reconcile_workspace(tmp_path, NOW)
        assert manifest is not None
        (tmp_path / "user-change.txt").write_text("changed before apply", encoding="utf-8")
        with pytest.raises(ManifestError):
            apply_manifest(tmp_path, manifest, approval(manifest.fingerprint()))
        assert not (tmp_path / ROLE_REGISTRY_REL).exists()
        assert not (tmp_path / CONTEXT_ROUTES_REL).exists()

    def test_retire_rejects_changed_h3_generated_hash(self, tmp_path: Path):
        init_workspace(tmp_path)
        approve_plan(tmp_path)
        _, manifest = reconcile_workspace(tmp_path, NOW)
        assert manifest is not None
        apply_manifest(tmp_path, manifest, approval(manifest.fingerprint()))
        route = tmp_path / CONTEXT_ROUTES_REL
        route.write_bytes(route.read_bytes() + b"\n")
        with pytest.raises(LifecycleError, match="changed"):
            retire_generated(tmp_path)

    @pytest.mark.parametrize(
        ("rel", "field"),
        [
            (ROLE_REGISTRY_REL, "registry_fingerprint"),
            (ROLE_RECONCILIATION_REL, "fingerprint"),
            (CONTEXT_ROUTES_REL, "routing_fingerprint"),
            (ADAPTER_STATE_REL, "state_fingerprint"),
        ],
    )
    def test_doctor_recomputes_h3_fingerprints(self, tmp_path: Path, rel: str, field: str):
        init_workspace(tmp_path)
        approve_plan(tmp_path)
        _, manifest = reconcile_workspace(tmp_path, NOW)
        assert manifest is not None
        apply_manifest(tmp_path, manifest, approval(manifest.fingerprint()))
        record = read_json(tmp_path, rel)
        record[field] = "sha256:" + "0" * 64
        write_atomic(tmp_path, rel, record)
        doctor = doctor_workspace(tmp_path, NOW)
        assert doctor["ok"] is False
        assert any("fingerprint mismatch" in error for error in doctor["errors"])

    def test_doctor_recomputes_each_role_contract_fingerprint(self, tmp_path: Path):
        init_workspace(tmp_path)
        approve_plan(tmp_path)
        _, manifest = reconcile_workspace(tmp_path, NOW)
        assert manifest is not None
        apply_manifest(tmp_path, manifest, approval(manifest.fingerprint()))
        registry = read_json(tmp_path, ROLE_REGISTRY_REL)
        registry["roles"][0]["mission"] += " tampered"
        write_atomic(tmp_path, ROLE_REGISTRY_REL, registry)
        doctor = doctor_workspace(tmp_path, NOW)
        assert doctor["ok"] is False
        assert any("role contract fingerprint mismatch" in error for error in doctor["errors"])

    def test_invalid_plan_approval_blocks_derivation(self, tmp_path: Path):
        init_workspace(tmp_path)
        approve_plan(tmp_path)
        approval_record = read_json(tmp_path, PLAN_APPROVAL_REL)
        approval_record["plan_fingerprint"] = "sha256:" + "0" * 64
        write_atomic(tmp_path, PLAN_APPROVAL_REL, approval_record)
        result, manifest = reconcile_workspace(tmp_path, NOW)
        assert result["h3"]["status"] == "blocked"
        assert manifest is not None
        assert not (tmp_path / ROLE_REGISTRY_REL).exists()
        doctor = doctor_workspace(tmp_path, NOW)
        assert doctor["ok"] is False
        assert any("Plan approval" in error for error in doctor["errors"])
