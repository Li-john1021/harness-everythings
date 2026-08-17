"""单元测试：批准与角色生命周期纯状态机。"""

from __future__ import annotations

import pytest

from harness_everythings.core.approvals_roles import (
    ApprovalError,
    ApprovalRequest,
    RoleTransitionError,
    decide,
    make_plan_approval,
    release_requires_separate_approval,
    transition_role,
    validate_canonical_approval,
    validate_plan_approval,
)
from harness_everythings.core.entities import make_envelope
from harness_everythings.core.evidence import make_evidence
from harness_everythings.core.identity import content_fingerprint


NOW = "2026-08-16T00:00:00Z"


def plan_evidence(plan: dict) -> dict:
    return make_evidence(
        actor="user",
        action="plan-approval",
        conclusion_kind="user_confirmed",
        supporting_refs=[plan["entity_id"]],
        verification_level="fixture_verified",
        source_ref="fixture:plan-approval",
        now=NOW,
    )


class TestApproval:
    def test_user_or_distinct_approver_ok(self):
        req = ApprovalRequest(
            target_ref="plan:1",
            scope="work_product",
            requester="role:worker",
            approver="user",
        )
        result = decide(req, "approved", "2026-08-16T00:00:00Z")
        assert result["decision"] == "approved"

    def test_self_approval_rejected(self):
        req = ApprovalRequest(
            target_ref="governance-proposal:1",
            scope="work_product",
            requester="role:worker",
            approver="role:worker",
        )
        with pytest.raises(ApprovalError):
            decide(req, "approved", "2026-08-16T00:00:00Z")

    def test_target_owner_cannot_approve_own_object(self):
        req = ApprovalRequest(
            target_ref="governance-proposal:1",
            scope="work_product",
            requester="role:coordinator",
            approver="role:owner",
            target_owner="role:owner",
        )
        with pytest.raises(ApprovalError):
            decide(req, "approved", "2026-08-16T00:00:00Z")

    def test_required_approval_fields(self):
        with pytest.raises(ApprovalError):
            decide(
                ApprovalRequest("", "work_product", "role:a", "user"),
                "approved",
                "2026-08-16T00:00:00Z",
            )
        with pytest.raises(ApprovalError):
            decide(
                ApprovalRequest("plan:1", "work_product", "role:a", "user"),
                "approved",
                "",
            )

    def test_unknown_scope(self):
        req = ApprovalRequest("x", "everything", "role:a", "user")
        with pytest.raises(ApprovalError):
            decide(req, "approved", "2026-08-16T00:00:00Z")

    def test_unknown_decision(self):
        req = ApprovalRequest("x", "work_product", "role:a", "user")
        with pytest.raises(ApprovalError):
            decide(req, "maybe", "2026-08-16T00:00:00Z")

    def test_work_approval_does_not_grant_release(self):
        approvals = [
            {"scope": "work_product", "decision": "approved"},
        ]
        assert release_requires_separate_approval(approvals) is False

    def test_separate_release_approval_required(self):
        approvals = [
            {"scope": "work_product", "decision": "approved"},
            {"scope": "external_release", "decision": "approved"},
        ]
        assert release_requires_separate_approval(approvals) is True

    def test_plan_approval_binds_plan_owner_and_fingerprint(self):
        plan = make_envelope("plan", {"case": "h31"}, "fixture", "2026-08-16T00:00:00Z").to_record()
        evidence = plan_evidence(plan)
        approval = make_plan_approval(
            plan,
            requester="role:governance-coordinator",
            approver="user",
            target_owner="workspace:owner",
            evidence_refs=[evidence["entity_id"]],
            decided_at="2026-08-16T00:00:00Z",
        )
        validate_plan_approval(approval, plan, target_owner="workspace:owner", evidence_records=[evidence])
        changed = dict(plan, approval_state="approved")
        with pytest.raises(ApprovalError, match="stale"):
            validate_plan_approval(approval, changed, target_owner="workspace:owner", evidence_records=[evidence])

    def test_forged_plan_approval_fingerprint_and_target_are_rejected(self):
        plan = make_envelope("plan", {"case": "forged"}, "fixture", "2026-08-16T00:00:00Z").to_record()
        evidence = plan_evidence(plan)
        approval = make_plan_approval(
            plan,
            requester="role:governance-coordinator",
            approver="user",
            target_owner="workspace:owner",
            evidence_refs=[evidence["entity_id"]],
            decided_at="2026-08-16T00:00:00Z",
        )
        forged = dict(approval, plan_fingerprint="sha256:" + "0" * 64)
        with pytest.raises(ApprovalError):
            validate_plan_approval(forged, plan, target_owner="workspace:owner", evidence_records=[evidence])
        forged_target = dict(approval, target_ref="plan:0000000000000000")
        with pytest.raises(ApprovalError):
            validate_plan_approval(forged_target, plan, target_owner="workspace:owner", evidence_records=[evidence])

    def test_external_release_approval_cannot_authorize_plan_derivation(self):
        plan = make_envelope("plan", {"case": "scope"}, "fixture", "2026-08-16T00:00:00Z").to_record()
        evidence = plan_evidence(plan)
        approval = make_plan_approval(
            plan,
            requester="role:governance-coordinator",
            approver="user",
            target_owner="workspace:owner",
            evidence_refs=[evidence["entity_id"]],
            decided_at="2026-08-16T00:00:00Z",
            scope="external_release",
        )
        with pytest.raises(ApprovalError, match="work_product"):
            validate_plan_approval(approval, plan, target_owner="workspace:owner", evidence_records=[evidence])

    def test_plan_target_owner_cannot_self_approve(self):
        plan = make_envelope("plan", {"case": "self"}, "fixture", "2026-08-16T00:00:00Z").to_record()
        with pytest.raises(ApprovalError):
            make_plan_approval(
                plan,
                requester="role:governance-coordinator",
                approver="workspace:owner",
                target_owner="workspace:owner",
                evidence_refs=["fixture:approval"],
                decided_at="2026-08-16T00:00:00Z",
            )

    def test_plan_approval_rejects_missing_or_unknown_evidence_records(self):
        plan = make_envelope("plan", {"case": "evidence"}, "fixture", NOW).to_record()
        evidence = plan_evidence(plan)
        approval = make_plan_approval(
            plan,
            requester="role:governance-coordinator",
            approver="user",
            target_owner="workspace:owner",
            evidence_refs=[evidence["entity_id"]],
            decided_at=NOW,
        )
        with pytest.raises(ApprovalError, match="actual Evidence"):
            validate_plan_approval(approval, plan, target_owner="workspace:owner")
        with pytest.raises(ApprovalError, match="missing Evidence"):
            validate_plan_approval(approval, plan, target_owner="workspace:owner", evidence_records=[])

    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ({"requester": "role:approver", "approver": "role:approver"}, "self-authorized"),
            ({"approver": "workspace:owner"}, "self-authorized"),
            ({"target_ref": "artifact:other"}, "target"),
            ({"target_owner": "workspace:other"}, "owner"),
            ({"scope": "external_release"}, "scope"),
            ({"decision": "rejected"}, "decision"),
        ],
    )
    def test_canonical_validator_rechecks_forged_fields_after_refingerprint(self, changes, message):
        approval = decide(
            ApprovalRequest(
                target_ref="artifact:one",
                scope="work_product",
                requester="role:requester",
                approver="role:approver",
                target_owner="workspace:owner",
                evidence_refs=("evidence:one",),
            ),
            "approved",
            "2026-08-16T00:00:00Z",
        )
        forged = dict(approval, **changes)
        forged["approval_fingerprint"] = content_fingerprint(
            {key: value for key, value in forged.items() if key != "approval_fingerprint"}
        )
        with pytest.raises(ApprovalError, match=message):
            validate_canonical_approval(
                forged,
                expected_decision="approved",
                expected_scope="work_product",
                expected_target_ref="artifact:one",
                expected_target_owner="workspace:owner",
            )

    def test_canonical_validator_rejects_noncanonical_or_missing_evidence(self):
        approval = decide(
            ApprovalRequest(
                target_ref="artifact:one",
                scope="work_product",
                requester="role:requester",
                approver="user",
                target_owner="workspace:owner",
                evidence_refs=("evidence:two", "evidence:one"),
            ),
            "approved",
            "2026-08-16T00:00:00Z",
        )
        forged = dict(approval, evidence_refs=[])
        forged["approval_fingerprint"] = content_fingerprint(
            {key: value for key, value in forged.items() if key != "approval_fingerprint"}
        )
        with pytest.raises(ApprovalError, match="evidence"):
            validate_canonical_approval(
                forged,
                expected_decision="approved",
                expected_scope="work_product",
                expected_target_ref="artifact:one",
                expected_target_owner="workspace:owner",
            )


class TestRoleLifecycle:
    def test_proposed_to_active(self):
        role = {"role_id": "role:x", "lifecycle_state": "proposed"}
        assert transition_role(role, "active")["lifecycle_state"] == "active"

    def test_retired_is_final(self):
        role = {"role_id": "role:x", "lifecycle_state": "retired"}
        with pytest.raises(RoleTransitionError):
            transition_role(role, "active")

    def test_illegal_jump(self):
        role = {"role_id": "role:x", "lifecycle_state": "proposed"}
        with pytest.raises(RoleTransitionError):
            transition_role(role, "merged")

    def test_deprecated_can_reactivate(self):
        role = {"role_id": "role:x", "lifecycle_state": "deprecated"}
        assert transition_role(role, "active")["lifecycle_state"] == "active"

    def test_unknown_state(self):
        with pytest.raises(RoleTransitionError):
            transition_role({"lifecycle_state": "ghost"}, "active")
