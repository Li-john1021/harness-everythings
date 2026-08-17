"""批准与角色生命周期纯状态机。

- 任何角色不得批准自己的治理权限（Spec 第 11 节）。
- 内容创作批准与外部发布批准是两个独立状态（Spec 第 14 节）。
- 角色生命周期：active/proposed/merged/split/deprecated/retired（Spec 第 9 节）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .entities import make_envelope
from .identity import content_fingerprint
from .schema_registry import validate

# ---------------------------------------------------------------------------
# 批准
# ---------------------------------------------------------------------------

# 批准范围：两状态分离，禁止一次批准同时覆盖。
APPROVAL_SCOPES = frozenset({"work_product", "external_release"})

APPROVAL_DECISIONS = frozenset({"approved", "rejected", "withdrawn"})


class ApprovalError(ValueError):
    """非法批准请求。"""


@dataclass(frozen=True)
class ApprovalRequest:
    """请求一次批准的 canonical 输入。"""

    target_ref: str  # 被批准对象引用
    scope: str  # work_product | external_release
    requester: str  # 请求者角色 ID 或 user
    approver: str  # 配置的批准者
    target_owner: str | None = None  # 被批准对象所有者；用于阻止自批
    plan_ref: str | None = None
    plan_fingerprint: str | None = None
    evidence_refs: tuple[str, ...] = ()


def decide(request: ApprovalRequest, decision: str, decided_at: str) -> dict[str, Any]:
    """记录一次批准决策，实施自批拒绝与范围校验。"""
    if request.scope not in APPROVAL_SCOPES:
        raise ApprovalError(f"unknown approval scope: {request.scope!r}")
    if decision not in APPROVAL_DECISIONS:
        raise ApprovalError(f"unknown decision: {decision!r}")
    if not request.target_ref or not request.requester or not request.approver:
        raise ApprovalError("target_ref, requester and approver are required")
    if not decided_at:
        raise ApprovalError("decided_at is required")
    if request.approver == request.requester and request.requester != "user":
        raise ApprovalError(
            "role cannot approve its own governance authority"
        )
    if request.target_owner and request.approver == request.target_owner:
        raise ApprovalError("role cannot approve an object it owns")
    plan_fields: dict[str, Any] = {}
    if request.plan_ref is not None or request.plan_fingerprint is not None:
        if not request.plan_ref or not request.plan_fingerprint:
            raise ApprovalError("plan_ref and plan_fingerprint are required together")
        if not request.evidence_refs or any(
            not isinstance(item, str) or not item for item in request.evidence_refs
        ):
            raise ApprovalError("canonical Plan approval requires evidence_refs")
        plan_fields = {
            "plan_ref": request.plan_ref,
            "plan_fingerprint": request.plan_fingerprint,
            "evidence_refs": list(request.evidence_refs),
        }
    fields = {
        "target_ref": request.target_ref,
        "scope": request.scope,
        "requester": request.requester,
        "approver": request.approver,
        "decision": decision,
        "decided_at": decided_at,
        **plan_fields,
        **(
            {"target_owner": request.target_owner}
            if request.target_owner is not None
            else {}
        ),
        **(
            {"evidence_refs": sorted(set(request.evidence_refs))}
            if request.evidence_refs
            else {}
        ),
    }
    record = make_envelope(
        "approval",
        {
            "target_ref": request.target_ref,
            "scope": request.scope,
            "requester": request.requester,
            "approver": request.approver,
            "plan_ref": request.plan_ref,
            "plan_fingerprint": request.plan_fingerprint,
        },
        f"approval:{request.scope}",
        decided_at,
        fields=fields,
    ).to_record()
    record["approval_fingerprint"] = content_fingerprint(record)
    validate("approval", record)
    return record


def make_plan_approval(
    plan: dict[str, Any],
    *,
    requester: str,
    approver: str,
    target_owner: str,
    evidence_refs: list[str] | tuple[str, ...],
    decided_at: str,
    decision: str = "approved",
    scope: str = "work_product",
) -> dict[str, Any]:
    """Create the canonical approval that authorizes derivation from one Plan."""
    plan_ref = plan.get("entity_id")
    if not isinstance(plan_ref, str) or not plan_ref:
        raise ApprovalError("Plan entity_id is required")
    return decide(
        ApprovalRequest(
            target_ref=plan_ref,
            scope=scope,
            requester=requester,
            approver=approver,
            target_owner=target_owner,
            plan_ref=plan_ref,
            plan_fingerprint=content_fingerprint(plan),
            evidence_refs=tuple(evidence_refs),
        ),
        decision,
        decided_at,
    )


def validate_canonical_approval(
    approval: dict[str, Any],
    *,
    expected_decision: str,
    expected_scope: str,
    expected_target_ref: str,
    expected_target_owner: str,
    evidence_records: Iterable[dict[str, Any]] | None = None,
) -> None:
    """Validate invariants shared by every canonical Approval consumer.

    Creation-time checks are insufficient because persisted records may be
    replaced or replayed.  Consumers must recheck identity, authority,
    evidence references, and the fingerprint at the point of use.
    """
    try:
        validate("approval", approval)
    except Exception as exc:
        raise ApprovalError(f"invalid approval record: {exc}") from exc

    if approval.get("decision") != expected_decision:
        raise ApprovalError("approval decision mismatch")
    if approval.get("scope") != expected_scope:
        raise ApprovalError(f"approval scope mismatch: expected {expected_scope}")
    if approval.get("target_ref") != expected_target_ref:
        raise ApprovalError("approval target mismatch")
    if approval.get("target_owner") != expected_target_owner:
        raise ApprovalError("approval target owner mismatch")

    requester = approval.get("requester")
    approver = approval.get("approver")
    if not isinstance(requester, str) or not requester or not isinstance(approver, str) or not approver:
        raise ApprovalError("approval requester and approver are required")
    if (approver == requester and requester != "user") or approver == expected_target_owner:
        raise ApprovalError("approval cannot be self-authorized")

    evidence_refs = approval.get("evidence_refs")
    if (
        not isinstance(evidence_refs, list)
        or not evidence_refs
        or evidence_refs != sorted(set(evidence_refs))
        or any(not isinstance(item, str) or not item for item in evidence_refs)
    ):
        raise ApprovalError("approval evidence_refs must be non-empty and canonical")

    approval_base = {
        key: value for key, value in approval.items() if key != "approval_fingerprint"
    }
    if approval.get("approval_fingerprint") != content_fingerprint(approval_base):
        raise ApprovalError("approval fingerprint mismatch")

    if evidence_records is not None:
        evidence_by_id: dict[str, dict[str, Any]] = {}
        for record in evidence_records:
            try:
                validate("evidence", record)
            except Exception as exc:
                raise ApprovalError(f"invalid approval Evidence record: {exc}") from exc
            base = {key: value for key, value in record.items() if key != "record_fingerprint"}
            if record.get("record_fingerprint") != content_fingerprint(base):
                raise ApprovalError(f"approval Evidence fingerprint is stale: {record.get('entity_id')}")
            entity_id = record.get("entity_id")
            if entity_id in evidence_by_id and content_fingerprint(evidence_by_id[entity_id]) != content_fingerprint(record):
                raise ApprovalError(f"conflicting approval Evidence record: {entity_id}")
            evidence_by_id[entity_id] = record
        missing = sorted(set(evidence_refs) - set(evidence_by_id))
        if missing:
            raise ApprovalError(f"approval references missing Evidence: {missing}")


def validate_plan_approval(
    approval: dict[str, Any],
    plan: dict[str, Any],
    *,
    target_owner: str,
    expected_scope: str = "work_product",
    evidence_records: Iterable[dict[str, Any]] | None = None,
) -> None:
    """Reject forged, stale, mis-scoped, or self-authorizing Plan approvals."""
    plan_ref = plan.get("entity_id")
    if evidence_records is None:
        raise ApprovalError("canonical Plan approval requires actual Evidence records")
    validate_canonical_approval(
        approval,
        expected_decision="approved",
        expected_scope=expected_scope,
        expected_target_ref=plan_ref,
        expected_target_owner=target_owner,
        evidence_records=evidence_records,
    )
    if approval.get("plan_ref") != plan_ref:
        raise ApprovalError("Plan approval target does not match Plan")
    if approval.get("plan_fingerprint") != content_fingerprint(plan):
        raise ApprovalError("Plan approval fingerprint is stale")


def release_requires_separate_approval(
    approvals: list[dict[str, Any]],
) -> bool:
    """external_release 必须有独立批准记录，work_product 批准不自动覆盖。"""
    return any(
        a.get("scope") == "external_release" and a.get("decision") == "approved"
        for a in approvals
    )


# ---------------------------------------------------------------------------
# 角色生命周期
# ---------------------------------------------------------------------------

ROLE_STATES = ("proposed", "active", "merged", "split", "deprecated", "retired")

ROLE_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"active", "retired"}),
    "active": frozenset({"merged", "split", "deprecated", "retired"}),
    "merged": frozenset({"retired"}),
    "split": frozenset({"retired"}),
    "deprecated": frozenset({"retired", "active"}),  # deprecated 可经决策恢复
    "retired": frozenset(),
}


class RoleTransitionError(ValueError):
    """非法角色状态变化。"""


def transition_role(role: dict[str, Any], to_state: str) -> dict[str, Any]:
    """对角色记录应用一次状态变化，返回新记录。"""
    current = role.get("lifecycle_state")
    if current not in ROLE_STATES:
        raise RoleTransitionError(f"unknown role state: {current!r}")
    if to_state not in ROLE_STATES:
        raise RoleTransitionError(f"unknown target state: {to_state!r}")
    if to_state not in ROLE_TRANSITIONS[current]:
        raise RoleTransitionError(f"illegal role transition: {current} -> {to_state}")
    updated = dict(role)
    updated["lifecycle_state"] = to_state
    return updated
