"""H4 domain-pack records through the existing dry-run lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_everythings.core.approvals_roles import ApprovalRequest, decide, make_plan_approval
from harness_everythings.core.domain_packs import make_software_artifact, make_software_review, make_software_work_product_approval, software_verification_results
from harness_everythings.core.evidence import make_evidence
from harness_everythings.core.identity import content_fingerprint
from harness_everythings.core.lifecycle import (
    SOFTWARE_APPROVAL_REL,
    SOFTWARE_RELEASE_APPROVAL_REL,
    SOFTWARE_CONTRACT_REL,
    SOFTWARE_PACK_REL,
    SOFTWARE_TRACEABILITY_REL,
    SOFTWARE_VERIFICATION_REL,
    SOFTWARE_DELIVERY_REL,
    apply_init_proposal,
    append_h6_events,
    build_init_proposal,
    doctor_workspace,
    reconcile_workspace,
    status_workspace,
    submit_software_workflow,
)
from harness_everythings.storage.atomic import read_json, write_atomic
from harness_everythings.storage.manifest import apply_manifest


NOW = "2026-08-16T00:00:00Z"


def manifest_approval(fingerprint: str) -> dict[str, str]:
    return {"approver": "user", "scope": "h4-fixture", "decision": "approved", "approved_manifest_fingerprint": fingerprint}


def setup_workspace(root: Path) -> None:
    proposal = build_init_proposal(root, "new", NOW)
    assert proposal.manifest is not None
    apply_init_proposal(root, proposal, manifest_approval(proposal.manifest.fingerprint()))
    plan_path = root / ".harness-everythings" / "plans" / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["approval_state"] = "approved"
    plan["scope"]["domain_pack"] = "software-engineering"
    write_atomic(root, ".harness-everythings/plans/plan.json", plan)
    workspace = read_json(root, ".harness-everythings/harness.json")
    evidence = make_evidence(actor="user", action="plan-approval", conclusion_kind="user_confirmed", supporting_refs=[plan["entity_id"]], verification_level="fixture_verified", source_ref="fixture:h4-plan-approval", now=NOW)
    evidence_manifest = append_h6_events(root, owner_ref=plan["entity_id"], evidence=(evidence,), now=NOW)
    apply_manifest(root, evidence_manifest, manifest_approval(evidence_manifest.fingerprint()))
    approval = make_plan_approval(
        plan,
        requester="role:governance-coordinator",
        approver="user",
        target_owner=workspace["entity_id"],
        evidence_refs=[evidence["entity_id"]],
        decided_at=NOW,
    )
    write_atomic(root, ".harness-everythings/approvals/plan-approval.json", approval, exclusive=True)


class TestH4Lifecycle:
    def test_reconcile_generates_software_pack_contract_and_evidence_views(self, tmp_path: Path):
        setup_workspace(tmp_path)
        result, manifest = reconcile_workspace(tmp_path, NOW)
        assert manifest is not None
        assert result["h3"]["domain"]["status"] == "proposed"
        apply_manifest(tmp_path, manifest, manifest_approval(manifest.fingerprint()))
        for rel in (SOFTWARE_PACK_REL, SOFTWARE_CONTRACT_REL, SOFTWARE_TRACEABILITY_REL, SOFTWARE_VERIFICATION_REL):
            assert (tmp_path / rel).is_file()
        assert doctor_workspace(tmp_path, NOW)["ok"] is True

    def test_unknown_domain_pack_is_blocked_without_domain_writes(self, tmp_path: Path):
        setup_workspace(tmp_path)
        plan_path = tmp_path / ".harness-everythings" / "plans" / "plan.json"
        plan = read_json(tmp_path, ".harness-everythings/plans/plan.json")
        plan["scope"]["domain_pack"] = "not-approved"
        write_atomic(tmp_path, ".harness-everythings/plans/plan.json", plan)
        workspace = read_json(tmp_path, ".harness-everythings/harness.json")
        evidence = make_evidence(actor="user", action="plan-reapproval", conclusion_kind="user_confirmed", supporting_refs=[plan["entity_id"]], verification_level="fixture_verified", source_ref="fixture:h4-reapproval", now=NOW)
        evidence_manifest = append_h6_events(tmp_path, owner_ref=plan["entity_id"], evidence=(evidence,), now=NOW)
        apply_manifest(tmp_path, evidence_manifest, manifest_approval(evidence_manifest.fingerprint()))
        approval = make_plan_approval(plan, requester="role:governance-coordinator", approver="user", target_owner=workspace["entity_id"], evidence_refs=[evidence["entity_id"]], decided_at=NOW)
        write_atomic(tmp_path, ".harness-everythings/approvals/plan-approval.json", approval)
        result, manifest = reconcile_workspace(tmp_path, NOW)
        assert result["h3"]["status"] == "blocked"
        assert manifest is not None
        assert not (tmp_path / SOFTWARE_PACK_REL).exists()

    def test_real_software_workflow_is_registered_and_deeply_verified(self, tmp_path: Path):
        setup_workspace(tmp_path)
        _, manifest = reconcile_workspace(tmp_path, NOW)
        assert manifest is not None
        apply_manifest(tmp_path, manifest, manifest_approval(manifest.fingerprint()))
        contract = read_json(tmp_path, SOFTWARE_CONTRACT_REL)
        workspace = read_json(tmp_path, ".harness-everythings/harness.json")
        artifact = make_software_artifact("implemented", contract=contract, artifact_kind="code", source_ref="fixture:h4:artifact", now=NOW)
        review_evidence = make_evidence(actor="user", action="review", conclusion_kind="user_confirmed", supporting_refs=[artifact["entity_id"]], verification_level="fixture_verified", source_ref="fixture:h4:review", now=NOW)
        initial = software_verification_results(contract)
        review = make_software_review(contract, artifact_refs=[artifact["entity_id"]], evidence_refs=[review_evidence["entity_id"]], spec_compliance="passed", implementation_quality="passed", artifact_records=[artifact], evidence_records=[review_evidence])
        rows = [("requirement", item["requirement_id"]) for item in contract["requirements"]] + [("acceptance", item["acceptance_id"]) for item in contract["acceptance"]]
        row_evidence = {}
        evidence_records = [review_evidence]
        for kind, row_ref in rows:
            evidence = make_evidence(actor="tool", action=f"trace:{kind}:{row_ref}", conclusion_kind="observed", supporting_refs=[contract["entity_id"], row_ref, artifact["entity_id"]], verification_level="fixture_verified", source_ref=f"fixture:h4:trace:{kind}:{row_ref}", now=NOW)
            row_evidence[row_ref] = evidence
            evidence_records.append(evidence)
        verifications = software_verification_results(contract, {item["verification_id"]: "passed" for item in initial}, {item["verification_id"]: row_evidence[item["row_ref"]] for item in initial})
        approval = make_software_work_product_approval(contract, review, verifications, artifact_refs=[artifact["entity_id"]], evidence_refs=[item["entity_id"] for item in evidence_records], artifact_records=[artifact], evidence_records=evidence_records, requester="role:software-review", approver="user", target_owner=workspace["entity_id"], decided_at=NOW)
        workflow = submit_software_workflow(
            tmp_path, artifacts=[artifact], evidence=evidence_records, review=review, verifications=verifications,
            approval=approval,
            traceability_mappings={
                "implementation_refs": {row_ref: [artifact["entity_id"]] for _, row_ref in rows},
                "evidence_refs": {row_ref: [row_evidence[row_ref]["entity_id"]] for _, row_ref in rows},
                "verification_refs": {row_ref: [f"verification:{kind}:{row_ref}"] for kind, row_ref in rows},
            },
            now=NOW,
        )
        apply_manifest(tmp_path, workflow, manifest_approval(workflow.fingerprint()))
        assert read_json(tmp_path, SOFTWARE_DELIVERY_REL)["completion_status"] == "complete"
        assert read_json(tmp_path, SOFTWARE_TRACEABILITY_REL)["status"] == "complete"
        assert doctor_workspace(tmp_path, NOW)["ok"] is True

        release = decide(
            ApprovalRequest(
                approval["entity_id"], "external_release", "role:release-coordinator",
                "user", workspace["entity_id"], evidence_refs=(review_evidence["entity_id"],),
            ),
            "approved",
            NOW,
        )
        release["work_product_approval_ref"] = approval["entity_id"]
        release["work_product_approval_fingerprint"] = content_fingerprint(approval)
        release["approval_fingerprint"] = content_fingerprint(
            {key: value for key, value in release.items() if key != "approval_fingerprint"}
        )
        release_manifest = submit_software_workflow(
            tmp_path, artifacts=[artifact], evidence=evidence_records, review=review,
            verifications=verifications, approval=approval,
            traceability_mappings={
                "implementation_refs": {row_ref: [artifact["entity_id"]] for _, row_ref in rows},
                "evidence_refs": {row_ref: [row_evidence[row_ref]["entity_id"]] for _, row_ref in rows},
                "verification_refs": {row_ref: [f"verification:{kind}:{row_ref}"] for kind, row_ref in rows},
            },
            external_release=release,
            now=NOW,
        )
        apply_manifest(tmp_path, release_manifest, manifest_approval(release_manifest.fingerprint()))
        assert status_workspace(tmp_path, NOW)["h4"]["external_release_state"] == "approved"
        assert status_workspace(tmp_path, NOW)["h4"]["work_product_approval_state"] == "approved"
        assert doctor_workspace(tmp_path, NOW)["ok"] is True

        forged = dict(release, decision="rejected")
        forged["approval_fingerprint"] = content_fingerprint(
            {key: value for key, value in forged.items() if key != "approval_fingerprint"}
        )
        write_atomic(tmp_path, SOFTWARE_RELEASE_APPROVAL_REL, forged)
        doctor = doctor_workspace(tmp_path, NOW)
        assert doctor["ok"] is False
        assert any("software external release approval" in error for error in doctor["errors"])
        assert status_workspace(tmp_path, NOW)["h4"]["external_release_state"] == "invalidated"

        write_atomic(tmp_path, SOFTWARE_RELEASE_APPROVAL_REL, release)
        forged_work = dict(approval, decision="rejected")
        forged_work["approval_fingerprint"] = content_fingerprint(
            {key: value for key, value in forged_work.items() if key != "approval_fingerprint"}
        )
        write_atomic(tmp_path, SOFTWARE_APPROVAL_REL, forged_work)
        status = status_workspace(tmp_path, NOW)
        assert status["h4"]["work_product_approval_state"] == "invalidated"
        assert status["h4"]["external_release_state"] == "invalidated"
