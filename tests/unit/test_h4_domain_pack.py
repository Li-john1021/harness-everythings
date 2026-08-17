"""H4 software domain-pack contract and deterministic mapping tests."""

from __future__ import annotations

import pytest

from harness_everythings.core.domain_packs import (
    DomainPackError,
    build_software_traceability,
    make_software_artifact,
    make_software_review,
    make_software_work_product_approval,
    validate_software_work_product_approval,
    derive_software_output_contract,
    load_domain_pack,
    select_software_role_templates,
    software_verification_results,
    validate_domain_pack,
)
from harness_everythings.core.entities import make_envelope
from harness_everythings.core.approvals_roles import make_plan_approval
from harness_everythings.core.evidence import make_evidence


NOW = "2026-08-16T00:00:00Z"


def plan(goals: list[str] | None = None) -> dict:
    return make_envelope(
        "plan",
        {"fixture": "h4"},
        "fixture:h4",
        NOW,
        fields={
            "goals": goals or ["Build the approved CLI", "Record reproducible evidence"],
            "scope": {"domain_pack": "software-engineering"},
            "decisions": [],
            "risks": [],
            "stages": ["requirements", "implementation", "validation"],
            "acceptance_strategy": {"required_evidence": ["tests", "user approval"]},
            "approval_state": "approved",
        },
    ).to_record()


def approval_for(value: dict) -> dict:
    evidence = plan_evidence(value)
    return make_plan_approval(value, requester="role:governance", approver="user", target_owner="workspace:h4", evidence_refs=[evidence["entity_id"]], decided_at=NOW)


def plan_evidence(value: dict) -> dict:
    return make_evidence(actor="user", action="plan-approval", conclusion_kind="user_confirmed", supporting_refs=[value["entity_id"]], verification_level="fixture_verified", source_ref="fixture:h4-plan", now=NOW)


def contract_for(value: dict) -> dict:
    return derive_software_output_contract(value, NOW, approval=approval_for(value), target_owner="workspace:h4", evidence_records=[plan_evidence(value)])


class TestSoftwarePack:
    def test_manifest_is_strict_versioned_and_fingerprinted(self):
        pack = load_domain_pack("software-engineering")
        validate_domain_pack(pack)
        assert pack["pack_version"] == "1.0"
        assert {"ownership", "risk", "verification", "concurrency"} == set(pack["generation_basis"])
        assert all(template["role_id"] for template in pack["role_templates"])
        tampered = dict(pack, pack_version="9.9")
        with pytest.raises(DomainPackError):
            validate_domain_pack(tampered)

    def test_unknown_pack_is_rejected(self):
        with pytest.raises(DomainPackError):
            load_domain_pack("unknown-domain")

    def test_plan_to_contract_and_traceability_are_deterministic(self):
        left = plan(["z goal", "a goal"])
        right = plan(["a goal", "z goal"])
        first = contract_for(left)
        second = contract_for(right)
        assert first == second
        roles = select_software_role_templates(left)
        trace = build_software_traceability(first, roles)
        results = software_verification_results(first)
        assert [item["requirement_id"] for item in first["requirements"]] == sorted(item["requirement_id"] for item in first["requirements"])
        assert len(trace["requirements"]) == len(first["requirements"])
        assert all(item["status"] == "not_run" for item in results)
        assert all(item["verification_class"] in {"build", "test", "static", "manual", "hardware"} for item in results)

    def test_role_selection_does_not_depend_on_language_or_framework(self):
        base = plan()
        changed = dict(base, scope={"domain_pack": "software-engineering", "language": "rust", "framework": "unknown"})
        assert select_software_role_templates(base) == select_software_role_templates(changed)

    def test_unapproved_plan_cannot_derive_contract(self):
        pending = dict(plan(), approval_state="proposed")
        with pytest.raises(DomainPackError):
            contract_for(pending)

    def test_controlled_delivery_requires_real_records_and_passed_verification(self):
        value = plan()
        contract = contract_for(value)
        initial = software_verification_results(contract)
        artifact = make_software_artifact("implemented", contract=contract, artifact_kind="code", source_ref="fixture:artifact", now=NOW)
        review_evidence = make_evidence(actor="user", action="review", conclusion_kind="user_confirmed", supporting_refs=[artifact["entity_id"]], verification_level="fixture_verified", source_ref="fixture:evidence:review", now=NOW)
        review = make_software_review(contract, artifact_refs=[artifact["entity_id"]], evidence_refs=[review_evidence["entity_id"]], spec_compliance="passed", implementation_quality="passed", artifact_records=[artifact], evidence_records=[review_evidence])
        with pytest.raises(DomainPackError):
            make_software_work_product_approval(contract, review, initial, artifact_refs=[artifact["entity_id"]], evidence_refs=[review_evidence["entity_id"]], artifact_records=[artifact], evidence_records=[review_evidence], requester="role:review", approver="user", target_owner="workspace:h4", decided_at=NOW)
        statuses = {item["verification_id"]: "passed" for item in initial}
        verification_evidence = {}
        evidence_records = [review_evidence]
        for item in initial:
            evidence = make_evidence(actor="tool", action=item["verification_id"], conclusion_kind="observed", supporting_refs=[artifact["entity_id"]], verification_level="fixture_verified", source_ref=f"fixture:{item['verification_id']}", now=NOW)
            verification_evidence[item["verification_id"]] = evidence
            evidence_records.append(evidence)
        records = software_verification_results(contract, statuses, verification_evidence)
        approval = make_software_work_product_approval(contract, review, records, artifact_refs=[artifact["entity_id"]], evidence_refs=[item["entity_id"] for item in evidence_records], artifact_records=[artifact], evidence_records=evidence_records, requester="role:review", approver="user", target_owner="workspace:h4", decided_at=NOW)
        validate_software_work_product_approval(approval, contract, review, records, artifact_records=[artifact], evidence_records=evidence_records, target_owner="workspace:h4")

    def test_deep_software_approval_rejects_contract_review_artifact_evidence_and_verification_drift(self):
        value = plan()
        contract = contract_for(value)
        artifact = make_software_artifact("implemented", contract=contract, artifact_kind="code", source_ref="fixture:artifact", now=NOW)
        evidence = make_evidence(actor="user", action="review", conclusion_kind="user_confirmed", supporting_refs=[artifact["entity_id"]], verification_level="fixture_verified", source_ref="fixture:review", now=NOW)
        initial = software_verification_results(contract)
        verification_evidence = make_evidence(actor="tool", action="verify", conclusion_kind="observed", supporting_refs=[artifact["entity_id"]], verification_level="fixture_verified", source_ref="fixture:verify", now=NOW)
        records = software_verification_results(contract, {item["verification_id"]: "passed" for item in initial}, {item["verification_id"]: verification_evidence for item in initial})
        review = make_software_review(contract, artifact_refs=[artifact["entity_id"]], evidence_refs=[evidence["entity_id"]], spec_compliance="passed", implementation_quality="passed", artifact_records=[artifact], evidence_records=[evidence])
        approval = make_software_work_product_approval(contract, review, records, artifact_refs=[artifact["entity_id"]], evidence_refs=[evidence["entity_id"], verification_evidence["entity_id"]], artifact_records=[artifact], evidence_records=[evidence, verification_evidence], requester="role:review", approver="user", target_owner="workspace:h4", decided_at=NOW)
        with pytest.raises(DomainPackError):
            validate_software_work_product_approval(approval, dict(contract, contract_fingerprint="sha256:contract-drift"), review, records, artifact_records=[artifact], evidence_records=[evidence, verification_evidence], target_owner="workspace:h4")
        with pytest.raises(DomainPackError):
            validate_software_work_product_approval(approval, contract, dict(review, findings=["tampered"], fingerprint=review["fingerprint"]), records, artifact_records=[artifact], evidence_records=[evidence, verification_evidence], target_owner="workspace:h4")
        with pytest.raises(DomainPackError):
            validate_software_work_product_approval(approval, contract, review, [dict(records[0], tool_result_source="tampered")], artifact_records=[artifact], evidence_records=[evidence, verification_evidence], target_owner="workspace:h4")
