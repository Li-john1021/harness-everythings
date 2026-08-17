"""H6 evidence, checkpoint, evaluation, budget, and governance tests."""

from __future__ import annotations

import pytest

from harness_everythings.core.evidence import (
    EvidenceError,
    consume_evaluation,
    deterministic_views,
    doctor_evidence,
    make_artifact,
    make_checkpoint,
    make_evidence,
    make_governance_effect,
    make_handoff,
    recover_handoff,
    register_evaluation,
    validate_budget,
)


NOW = "2026-08-16T00:00:00Z"


class TestEvidenceGovernance:
    def test_artifact_and_evidence_are_content_addressed(self):
        artifact = make_artifact("hello", artifact_kind="text", source_ref="fixture:content", sensitivity="public", now=NOW)
        evidence = make_evidence(actor="user", action="inspect", conclusion_kind="observed", supporting_refs=[artifact["entity_id"]], verification_level="fixture_verified", source_ref="fixture:evidence", now=NOW)
        assert artifact["content_fingerprint"].startswith("sha256:")
        assert evidence["supporting_refs"] == [artifact["entity_id"]]
        assert deterministic_views(evidence)["json_fingerprint"].startswith("sha256:")

    def test_private_absolute_source_refs_are_rejected(self):
        with pytest.raises(EvidenceError):
            make_artifact("secret", artifact_kind="text", source_ref=r"C:\Users\audit\private\run.log", sensitivity="sensitive", now=NOW)
        with pytest.raises(EvidenceError):
            make_evidence(actor="tool", action="inspect", conclusion_kind="observed", supporting_refs=[], verification_level="fixture_verified", source_ref=r"C:\Users\audit\private\review.json", now=NOW)

    def test_real_workflow_level_needs_explicit_user_confirmation(self):
        with pytest.raises(EvidenceError):
            make_evidence(actor="role:x", action="verify", conclusion_kind="observed", supporting_refs=[], verification_level="user_confirmed_in_real_workflow", source_ref="fixture:evidence", now=NOW)
        record = make_evidence(actor="user", action="verify", conclusion_kind="user_confirmed", supporting_refs=[], verification_level="user_confirmed_in_real_workflow", source_ref="fixture:evidence", now=NOW, user_confirmed=True)
        assert record["verification_level"] == "user_confirmed_in_real_workflow"

    def test_checkpoint_handoff_and_recovery_contract(self):
        evaluation = register_evaluation(evaluator="fixture", result_ref="artifact:b")
        checkpoint = make_checkpoint(owner_role="role:x", state="partial_success", completed_refs=["artifact:a"], incomplete_refs=[evaluation["evaluation_id"]], resume_preconditions=["user consumes evaluation"])
        handoff = make_handoff(checkpoint=checkpoint, incomplete_items=[evaluation["evaluation_id"]], resume_preconditions=["user consumes evaluation"], receiver="role:y", source_ref="fixture:handoff", now=NOW)
        assert handoff["checkpoint"]["state"] == "partial_success"
        assert handoff["receiver"] == "role:y"
        with pytest.raises(EvidenceError):
            recover_handoff(handoff, checkpoint, evaluation_records=[evaluation])
        consumed = consume_evaluation(evaluation, actor="user", manual=True)
        assert recover_handoff(handoff, checkpoint, evaluation_records=[consumed])["resumed"] is True
        with pytest.raises(EvidenceError):
            recover_handoff(handoff, checkpoint, evaluation_records=[])

    def test_evaluation_is_manual_to_consume(self):
        evaluation = register_evaluation(evaluator="fixture", result_ref="evidence:eval")
        with pytest.raises(EvidenceError):
            consume_evaluation(evaluation, actor="role:x", manual=False)
        consumed = consume_evaluation(evaluation, actor="user", manual=True)
        assert consumed["consumption"] == "consumed"

    def test_budget_rejects_bool_negative_and_unknown_values(self):
        validate_budget({"max_tokens": 10})
        for value in (True, -1, "10"):
            with pytest.raises(EvidenceError):
                validate_budget({"max_tokens": value})
        with pytest.raises(EvidenceError):
            validate_budget({"unknown": 1})

    def test_governance_effect_and_doctor_report_failures(self):
        effect = make_governance_effect(proposal_ref="proposal:1", proposal_fingerprint="sha256:" + "1" * 64, approval_ref="approval:1", approval_fingerprint="sha256:" + "2" * 64, application_fingerprint="sha256:" + "3" * 64, action="applied", before_fingerprint="sha256:before", after_fingerprint="sha256:after", rollback_ref="manifest:rollback", effect_status="changed")
        assert effect["effect_status"] == "changed"
        doctor = doctor_evidence(profile_expired=True, references={"artifact:a": {}}, referenced_ids=["artifact:missing"], idle_roles=["role:x"], ownership_conflicts=["artifact:a"], missing_evidence=["acceptance:a"], context_bloat=["role:y"], expired_approval=True, unconsumed_evaluations=["evaluation:e"], incomplete_handoff=True)
        assert doctor["ok"] is False
        assert len(doctor["errors"]) >= 9
