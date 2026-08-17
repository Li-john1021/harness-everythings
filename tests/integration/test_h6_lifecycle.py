"""H6 records are created and reconciled through ApplicationManifest."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness_everythings.core.approvals_roles import make_plan_approval
from harness_everythings.core.evidence import EvidenceError, consume_evaluation, recover_handoff, register_evaluation
from harness_everythings.core.evidence import make_evidence
from harness_everythings.core.lifecycle import (
    ARTIFACT_LEDGER_REL,
    CHECKPOINT_REL,
    EVALUATION_LEDGER_REL,
    EVIDENCE_LEDGER_REL,
    GOVERNANCE_EFFECT_REL,
    HANDOFF_REL,
    apply_init_proposal,
    build_init_proposal,
    doctor_workspace,
    reconcile_workspace,
    retire_generated,
    status_workspace,
    LifecycleError,
    append_h6_events,
)
from harness_everythings.storage.atomic import read_json, write_atomic
from harness_everythings.storage.manifest import apply_manifest


NOW = "2026-08-17T00:00:00Z"


def approval(fingerprint: str) -> dict[str, str]:
    return {"approver": "user", "scope": "h6-fixture", "decision": "approved", "approved_manifest_fingerprint": fingerprint}


def setup(root: Path) -> None:
    proposal = build_init_proposal(root, "new", NOW)
    apply_init_proposal(root, proposal, approval(proposal.manifest.fingerprint()))
    plan = read_json(root, ".harness-everythings/plans/plan.json")
    plan["approval_state"] = "approved"
    plan["scope"]["evidence_governance"] = True
    write_atomic(root, ".harness-everythings/plans/plan.json", plan)
    workspace = read_json(root, ".harness-everythings/harness.json")
    plan_evidence = make_evidence(actor="user", action="plan-approval", conclusion_kind="user_confirmed", supporting_refs=[plan["entity_id"]], verification_level="fixture_verified", source_ref="fixture:h6-plan", now=NOW)
    evidence_manifest = append_h6_events(root, owner_ref=plan["entity_id"], evidence=(plan_evidence,), now=NOW)
    apply_manifest(root, evidence_manifest, approval(evidence_manifest.fingerprint()))
    plan_approval = make_plan_approval(plan, requester="role:governance-coordinator", approver="user", target_owner=workspace["entity_id"], evidence_refs=[plan_evidence["entity_id"]], decided_at=NOW)
    write_atomic(root, ".harness-everythings/approvals/plan-approval.json", plan_approval)


def test_h6_records_are_lifecycle_managed_and_no_change_is_explicit(tmp_path: Path):
    setup(tmp_path)
    result, manifest = reconcile_workspace(tmp_path, NOW)
    assert manifest is not None
    assert result["h3"]["h6"]["status"] == "proposed"
    apply_manifest(tmp_path, manifest, approval(manifest.fingerprint()))
    for rel in (ARTIFACT_LEDGER_REL, EVIDENCE_LEDGER_REL, CHECKPOINT_REL, HANDOFF_REL, EVALUATION_LEDGER_REL, GOVERNANCE_EFFECT_REL):
        assert (tmp_path / rel).is_file()
    effect = read_json(tmp_path, GOVERNANCE_EFFECT_REL)
    assert effect["action"] == "proposed"
    assert effect["effect_status"] == "no-change"
    assert doctor_workspace(tmp_path, NOW)["ok"] is True
    assert status_workspace(tmp_path, NOW)["h6"]["status"] == "proposed"
    # The user-approved Plan changed after init, so retire must stop before
    # touching any generated H6 record instead of deleting a mismatched file.
    with pytest.raises(LifecycleError):
        retire_generated(tmp_path)
    user_file = tmp_path / "user.txt"
    user_file.write_bytes("keep exact bytes\r\n".encode("utf-8"))
    before = user_file.read_bytes()
    _, repeat_manifest = reconcile_workspace(tmp_path, NOW)
    assert repeat_manifest is not None
    apply_manifest(tmp_path, repeat_manifest, approval(repeat_manifest.fingerprint()))
    assert user_file.read_bytes() == before


def test_h6_reconcile_is_idempotent_after_apply(tmp_path: Path):
    setup(tmp_path)
    _, manifest = reconcile_workspace(tmp_path, NOW)
    assert manifest is not None
    apply_manifest(tmp_path, manifest, approval(manifest.fingerprint()))
    before = {rel: (tmp_path / rel).read_bytes() for rel in (ARTIFACT_LEDGER_REL, EVIDENCE_LEDGER_REL, CHECKPOINT_REL, HANDOFF_REL, EVALUATION_LEDGER_REL, GOVERNANCE_EFFECT_REL)}
    second, second_manifest = reconcile_workspace(tmp_path, NOW)
    assert second["h3"]["h6"]["status"] == "proposed"
    assert second_manifest is not None
    apply_manifest(tmp_path, second_manifest, approval(second_manifest.fingerprint()))
    after = {rel: (tmp_path / rel).read_bytes() for rel in before}
    assert after == before


def test_h6_append_only_evaluation_consumption_projects_checkpoint_and_handoff(tmp_path: Path):
    setup(tmp_path)
    _, manifest = reconcile_workspace(tmp_path, NOW)
    apply_manifest(tmp_path, manifest, approval(manifest.fingerprint()))
    plan = read_json(tmp_path, ".harness-everythings/plans/plan.json")
    evaluation = register_evaluation(evaluator="role:governance", result_ref="artifact:evaluation")
    event_manifest = append_h6_events(tmp_path, owner_ref=plan["entity_id"], evaluations=(evaluation,), now=NOW)
    apply_manifest(tmp_path, event_manifest, approval(event_manifest.fingerprint()))
    _, projected = reconcile_workspace(tmp_path, NOW)
    apply_manifest(tmp_path, projected, approval(projected.fingerprint()))
    assert read_json(tmp_path, EVALUATION_LEDGER_REL)["evaluations"][0]["consumption"] == "unconsumed"
    assert doctor_workspace(tmp_path, NOW)["ok"] is False
    with pytest.raises(EvidenceError):
        recover_handoff(
            read_json(tmp_path, HANDOFF_REL), read_json(tmp_path, CHECKPOINT_REL),
            evaluation_records=read_json(tmp_path, EVALUATION_LEDGER_REL)["evaluations"],
        )
    consumed = consume_evaluation(evaluation, actor="user", manual=True)
    consumed_manifest = append_h6_events(tmp_path, owner_ref=plan["entity_id"], evaluations=(consumed,), now=NOW)
    apply_manifest(tmp_path, consumed_manifest, approval(consumed_manifest.fingerprint()))
    _, projected = reconcile_workspace(tmp_path, NOW)
    apply_manifest(tmp_path, projected, approval(projected.fingerprint()))
    assert read_json(tmp_path, EVALUATION_LEDGER_REL)["evaluations"][0]["consumption"] == "consumed"
    assert read_json(tmp_path, CHECKPOINT_REL)["state"] == "complete"
    completed_recovery = recover_handoff(
        read_json(tmp_path, HANDOFF_REL), read_json(tmp_path, CHECKPOINT_REL),
        evaluation_records=read_json(tmp_path, EVALUATION_LEDGER_REL)["evaluations"],
    )
    assert completed_recovery["remaining_refs"] == []
    assert doctor_workspace(tmp_path, NOW)["ok"] is True
