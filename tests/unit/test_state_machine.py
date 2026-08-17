"""单元测试：任务状态机、预算、重试与所有权。"""

from __future__ import annotations

import pytest

from harness_everythings.core.state_machine import (
    Budget,
    OwnershipConflict,
    TransitionError,
    TransitionRecord,
    check_artifact_ownership,
    idempotency_key,
    retry_allowed,
    transition,
)


def make_task(**overrides):
    task = {
        "state": "proposed",
        "transitions": [],
        "owner_role": "role:abc",
    }
    task.update(overrides)
    return task


def rec(to_state, **overrides):
    defaults = dict(
        from_state="proposed",
        to_state=to_state,
        actor="role:abc",
        reason="test",
        evidence_ref="evidence:test",
        at="2026-08-16T00:00:00Z",
        artifacts_in=("artifact:input",),
        artifacts_out=("artifact:output",),
    )
    defaults.update(overrides)
    return TransitionRecord(**defaults)


class TestTaskTransitions:
    def test_happy_path(self):
        task = make_task()
        for target in ("ready", "running", "review", "validation", "awaiting_approval", "delivered"):
            task = transition(task, rec(target, from_state=task["state"]))
        assert task["state"] == "delivered"
        assert len(task["transitions"]) == 6

    def test_illegal_skip_rejected(self):
        with pytest.raises(TransitionError):
            transition(make_task(), rec("delivered"))

    def test_record_source_must_match_task_state(self):
        with pytest.raises(TransitionError):
            transition(make_task(), rec("ready", from_state="running"))

    def test_terminal_no_exit(self):
        task = make_task(state="delivered", transitions=[])
        with pytest.raises(TransitionError):
            transition(task, rec("cancelled"))

    def test_failed_needs_manual_retry(self):
        task = make_task(state="failed", transitions=[])
        task = transition(
            task,
            rec(
                "proposed",
                from_state="failed",
                actor="user",
                evidence_ref="user:retry-1",
            ),
        )
        assert task["state"] == "proposed"

    def test_failed_retry_rejects_role_actor(self):
        with pytest.raises(TransitionError):
            transition(
                make_task(state="failed", transitions=[]),
                rec(
                    "proposed",
                    from_state="failed",
                    actor="role:abc",
                    evidence_ref="user:retry-1",
                ),
            )

    def test_pause_and_resume_with_checkpoint_evidence(self):
        running = make_task(state="running", transitions=[])
        paused = transition(
            running,
            rec("paused", from_state="running", evidence_ref="handoff:checkpoint-1"),
        )
        resumed = transition(paused, rec("running", from_state="paused"))
        assert resumed["state"] == "running"

    def test_pause_requires_checkpoint_evidence(self):
        with pytest.raises(TransitionError):
            transition(
                make_task(state="running", transitions=[]),
                rec("paused", from_state="running", evidence_ref=""),
            )

    def test_every_transition_requires_artifact_refs(self):
        with pytest.raises(TransitionError):
            transition(make_task(), rec("ready", artifacts_in=(), artifacts_out=()))

    def test_cancelled_is_final(self):
        task = make_task(state="cancelled", transitions=[])
        with pytest.raises(TransitionError):
            transition(task, rec("proposed"))

    def test_evidence_required_for_validation(self):
        with pytest.raises(TransitionError):
            transition(make_task(state="review", transitions=[]), rec("validation", evidence_ref=""))

    def test_unknown_state_rejected(self):
        with pytest.raises(TransitionError):
            transition(make_task(state="bogus"), rec("ready"))

    def test_actor_required(self):
        with pytest.raises(TransitionError):
            transition(make_task(), rec("ready", actor=""))

    def test_reason_and_time_required(self):
        with pytest.raises(TransitionError):
            transition(make_task(), rec("ready", reason=""))
        with pytest.raises(TransitionError):
            transition(make_task(), rec("ready", at=""))

    def test_original_not_mutated(self):
        task = make_task()
        snapshot = dict(task)
        transition(task, rec("ready"))
        assert task == snapshot


class TestBudget:
    def test_within_budget(self):
        budget = Budget(max_tokens=100, max_attempts=3)
        assert budget.exceeded({"tokens": 99, "attempts": 3}) is None

    def test_tokens_exceeded(self):
        budget = Budget(max_tokens=100)
        assert budget.exceeded({"tokens": 101}) == "tokens"

    def test_attempts_exceeded(self):
        budget = Budget(max_attempts=2)
        assert budget.exceeded({"attempts": 3}) == "attempts"

    def test_cost_exceeded(self):
        budget = Budget(max_cost=1.0)
        assert budget.exceeded({"cost": 1.5}) == "cost"

    def test_duration_exceeded(self):
        budget = Budget(max_duration_seconds=10)
        assert budget.exceeded({"duration_seconds": 11}) == "duration_seconds"

    def test_negative_budget_and_usage_rejected(self):
        with pytest.raises(TransitionError):
            Budget(max_tokens=-1)
        with pytest.raises(TransitionError):
            Budget(max_tokens=10).exceeded({"tokens": -1})


class TestRetryPolicy:
    def test_safe_auto_within_attempts(self):
        assert retry_allowed("safe_auto", 1, 3) is True

    def test_safe_auto_exhausted(self):
        assert retry_allowed("safe_auto", 3, 3) is False

    def test_manual_never_auto(self):
        assert retry_allowed("manual", 0, 3) is False

    def test_never(self):
        assert retry_allowed("never", 0, 3) is False

    def test_unknown_policy(self):
        with pytest.raises(TransitionError):
            retry_allowed("always", 0, 3)


class TestOwnership:
    def test_no_conflict(self):
        active = {"task:1": make_task(artifacts_owned=["artifact:a"])}
        check_artifact_ownership(active, frozenset(["artifact:b"]))

    def test_conflict_detected(self):
        active = {"task:1": make_task(artifacts_owned=["artifact:a"])}
        with pytest.raises(OwnershipConflict):
            check_artifact_ownership(active, frozenset(["artifact:a"]))

    def test_terminal_task_does_not_block(self):
        active = {"task:1": make_task(state="delivered", artifacts_owned=["artifact:a"])}
        check_artifact_ownership(active, frozenset(["artifact:a"]))


class TestIdempotencyKey:
    def test_same_input_same_key(self):
        assert idempotency_key("role:1", "write", {"x": 1}) == idempotency_key(
            "role:1", "write", {"x": 1}
        )

    def test_different_actor_different_key(self):
        assert idempotency_key("role:1", "write", {}) != idempotency_key(
            "role:2", "write", {}
        )
