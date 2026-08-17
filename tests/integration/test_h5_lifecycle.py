"""H5 content pack through the approved Plan and manifest lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

from harness_everythings.core.approvals_roles import make_plan_approval
from harness_everythings.core.content_domain import create_content_brief, make_content_approval, make_content_variants, review_content
from harness_everythings.core.evidence import make_evidence
from harness_everythings.core.identity import content_fingerprint
from harness_everythings.core.lifecycle import (
    CONTENT_RELEASE_APPROVAL_REL,
    CONTENT_BRIEF_REL,
    CONTENT_CONTRACT_REL,
    CONTENT_PACK_REL,
    CONTENT_REVIEW_REL,
    CONTENT_DELIVERY_REL,
    CONTENT_VARIANTS_REL,
    apply_init_proposal,
    append_h6_events,
    build_init_proposal,
    doctor_workspace,
    reconcile_workspace,
    status_workspace,
    submit_content_workflow,
)
from harness_everythings.storage.atomic import read_json, write_atomic
from harness_everythings.storage.manifest import apply_manifest


NOW = "2026-08-16T00:00:00Z"


def manifest_approval(fingerprint: str) -> dict[str, str]:
    return {"approver": "user", "scope": "h5-fixture", "decision": "approved", "approved_manifest_fingerprint": fingerprint}


def setup_workspace(root: Path) -> None:
    proposal = build_init_proposal(root, "new", NOW)
    assert proposal.manifest is not None
    apply_init_proposal(root, proposal, manifest_approval(proposal.manifest.fingerprint()))
    plan = read_json(root, ".harness-everythings/plans/plan.json")
    plan["approval_state"] = "approved"
    plan["scope"].update({
        "domain_pack": "content-script",
        "content_brief": {
            "audience": "developers",
            "goals": ["explain the change"],
            "claims": [{"claim_id": "claim:one", "text": "A bounded claim", "source_refs": ["source:one"]}],
            "sources": [{"source_ref": "source:one", "source_fingerprint": "sha256:source", "sensitivity": "public"}],
            "structure": ["opening", "evidence", "cta"],
            "length": {"min_words": 1, "max_words": 80},
            "tone": "clear",
            "platform": "blog",
            "prohibited": ["guaranteed"],
            "cta": "read the docs",
        },
        "content_variants": [
            {"content": "opening evidence cta A bounded claim read the docs", "claim_refs": ["claim:one"]},
            {"content": "opening evidence cta A bounded claim read the docs again", "claim_refs": ["claim:one"]},
        ],
    })
    brief = create_content_brief(plan["scope"]["content_brief"], NOW)
    plan["scope"]["selected_variant_id"] = make_content_variants(brief, plan["scope"]["content_variants"])[0]["variant_id"]
    write_atomic(root, ".harness-everythings/plans/plan.json", plan)
    workspace = read_json(root, ".harness-everythings/harness.json")
    plan_evidence = make_evidence(actor="user", action="plan-approval", conclusion_kind="user_confirmed", supporting_refs=[plan["entity_id"]], verification_level="fixture_verified", source_ref="fixture:h5-plan-approval", now=NOW)
    evidence_manifest = append_h6_events(root, owner_ref=plan["entity_id"], evidence=(plan_evidence,), now=NOW)
    apply_manifest(root, evidence_manifest, manifest_approval(evidence_manifest.fingerprint()))
    approval = make_plan_approval(plan, requester="role:governance-coordinator", approver="user", target_owner=workspace["entity_id"], evidence_refs=[plan_evidence["entity_id"]], decided_at=NOW)
    write_atomic(root, ".harness-everythings/approvals/plan-approval.json", approval, exclusive=True)


def test_content_domain_lifecycle_records_are_written_and_verified(tmp_path: Path):
    setup_workspace(tmp_path)
    result, manifest = reconcile_workspace(tmp_path, NOW)
    assert manifest is not None
    assert result["h3"]["domain"]["status"] == "proposed"
    apply_manifest(tmp_path, manifest, manifest_approval(manifest.fingerprint()))
    for rel in (CONTENT_PACK_REL, CONTENT_BRIEF_REL, CONTENT_CONTRACT_REL, CONTENT_VARIANTS_REL, CONTENT_REVIEW_REL):
        assert (tmp_path / rel).is_file()
    assert doctor_workspace(tmp_path, NOW)["ok"] is True


def test_real_content_workflow_records_approval_and_delivery_state(tmp_path: Path):
    setup_workspace(tmp_path)
    _, manifest = reconcile_workspace(tmp_path, NOW)
    assert manifest is not None
    apply_manifest(tmp_path, manifest, manifest_approval(manifest.fingerprint()))
    workspace = read_json(tmp_path, ".harness-everythings/harness.json")
    brief = read_json(tmp_path, CONTENT_BRIEF_REL)
    variants = read_json(tmp_path, CONTENT_VARIANTS_REL)["variants"]
    review = read_json(tmp_path, CONTENT_REVIEW_REL)
    variant = next(item for item in variants if item["variant_id"] == review["selected_variant_ref"])
    evidence = make_evidence(
        actor="user", action="content-review", conclusion_kind="user_confirmed",
        supporting_refs=[
            brief["entity_id"], variant["variant_id"], "claim:one", "source:one",
            "claim:claim:one", "variant-selection", "content-length", "prohibited-content",
            "required-structure", "platform", "cta",
        ], verification_level="fixture_verified",
        source_ref="fixture:h5:content-review", now=NOW,
    )
    review = review_content(brief, variants, now=NOW, selected_variant_id=variant["variant_id"], evidence_refs=[evidence["entity_id"]])
    approval = make_content_approval(variant["variant_id"], brief=brief, requester="role:content-compliance", approver="user", target_owner=workspace["entity_id"], decided_at=NOW, variant=variant, review=review, evidence_refs=[evidence["entity_id"]], evidence_records=[evidence])
    workflow = submit_content_workflow(tmp_path, approval=approval, review=review, evidence_records=[evidence], now=NOW)
    apply_manifest(tmp_path, workflow, manifest_approval(workflow.fingerprint()))
    assert read_json(tmp_path, CONTENT_DELIVERY_REL)["lifecycle_state"] == "approved"
    assert doctor_workspace(tmp_path, NOW)["ok"] is True

    release = make_content_approval(
        variant["variant_id"], brief=brief, requester="role:release-coordinator",
        approver="user", target_owner=workspace["entity_id"], decided_at=NOW,
        scope="external_release", variant=variant, review=review,
        work_product_approval=approval, evidence_refs=[evidence["entity_id"]],
        evidence_records=[evidence],
    )
    release_manifest = submit_content_workflow(
        tmp_path, approval=approval, review=review, evidence_records=[evidence],
        external_release=release, now=NOW,
    )
    apply_manifest(tmp_path, release_manifest, manifest_approval(release_manifest.fingerprint()))
    assert read_json(tmp_path, CONTENT_DELIVERY_REL)["external_release_state"] == "approved"
    assert status_workspace(tmp_path, NOW)["h5"]["external_release_state"] == "approved"
    assert status_workspace(tmp_path, NOW)["h5"]["work_product_approval_state"] == "approved"
    assert doctor_workspace(tmp_path, NOW)["ok"] is True

    forged = dict(release, decision="rejected")
    forged["approval_fingerprint"] = content_fingerprint(
        {key: value for key, value in forged.items() if key != "approval_fingerprint"}
    )
    write_atomic(tmp_path, CONTENT_RELEASE_APPROVAL_REL, forged)
    doctor = doctor_workspace(tmp_path, NOW)
    assert doctor["ok"] is False
    assert any("content external release approval" in error for error in doctor["errors"])
    assert status_workspace(tmp_path, NOW)["h5"]["external_release_state"] == "invalidated"


def test_repeating_same_content_workflow_is_idempotent(tmp_path: Path):
    setup_workspace(tmp_path)
    _, manifest = reconcile_workspace(tmp_path, NOW)
    assert manifest is not None
    apply_manifest(tmp_path, manifest, manifest_approval(manifest.fingerprint()))
    workspace = read_json(tmp_path, ".harness-everythings/harness.json")
    brief = read_json(tmp_path, CONTENT_BRIEF_REL)
    variants = read_json(tmp_path, CONTENT_VARIANTS_REL)["variants"]
    review = read_json(tmp_path, CONTENT_REVIEW_REL)
    variant = next(item for item in variants if item["variant_id"] == review["selected_variant_ref"])
    evidence = make_evidence(
        actor="user", action="content-review", conclusion_kind="user_confirmed",
        supporting_refs=[brief["entity_id"], variant["variant_id"], "claim:one", "source:one", "claim:claim:one", "variant-selection", "content-length", "prohibited-content", "required-structure", "platform", "cta"],
        verification_level="fixture_verified", source_ref="fixture:h5:repeat", now=NOW,
    )
    review = review_content(brief, variants, now=NOW, selected_variant_id=variant["variant_id"], evidence_refs=[evidence["entity_id"]])
    approval = make_content_approval(variant["variant_id"], brief=brief, requester="role:content-compliance", approver="user", target_owner=workspace["entity_id"], decided_at=NOW, variant=variant, review=review, evidence_refs=[evidence["entity_id"]], evidence_records=[evidence])
    workflow = submit_content_workflow(tmp_path, approval=approval, review=review, evidence_records=[evidence], now=NOW)
    apply_manifest(tmp_path, workflow, manifest_approval(workflow.fingerprint()))
    repeat = submit_content_workflow(tmp_path, approval=approval, review=review, evidence_records=[evidence], now=NOW)
    assert repeat.writes == ()
    applied = apply_manifest(tmp_path, repeat, manifest_approval(repeat.fingerprint()))
    assert applied["applied"] == 0
    assert doctor_workspace(tmp_path, NOW)["ok"] is True
