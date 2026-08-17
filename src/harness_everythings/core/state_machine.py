"""纯任务状态机（Spec 第 15 节 canonical 状态）。

不接模型、Hook、队列或外部工具；只做纯函数状态转换与守卫判断。
每次转换返回新记录，不就地修改；调用方负责记录执行者、原因、证据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TASK_STATES = (
    "proposed",
    "ready",
    "running",
    "paused",
    "review",
    "validation",
    "awaiting_approval",
    "delivered",
    "failed",
    "cancelled",
)

# 合法状态变化表。终态不再转出；failed/cancelled 只能经显式人工重试
# 决策回到 proposed，不允许静默复活。
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"running", "cancelled"}),
    "running": frozenset({"paused", "review", "failed", "cancelled"}),
    "paused": frozenset({"running", "failed", "cancelled"}),
    "review": frozenset({"validation", "failed", "cancelled"}),
    "validation": frozenset({"awaiting_approval", "failed", "cancelled"}),
    "awaiting_approval": frozenset({"delivered", "failed", "cancelled"}),
    "delivered": frozenset(),
    "failed": frozenset({"proposed"}),  # 仅显式人工重试决策
    "cancelled": frozenset(),
}


class TransitionError(ValueError):
    """非法状态变化或缺少必需的转换证据。"""


@dataclass(frozen=True)
class TransitionRecord:
    """一次状态变化的 canonical 记录。"""

    from_state: str
    to_state: str
    actor: str  # 执行者标识（角色 ID 或 user）
    reason: str
    evidence_ref: str  # 支撑证据引用；空串表示无证据，受限转换会拒绝
    at: str  # ISO-8601 UTC，固定时钟注入
    artifacts_in: tuple[str, ...] = ()
    artifacts_out: tuple[str, ...] = ()


def transition(task: dict[str, Any], record: TransitionRecord) -> dict[str, Any]:
    """对任务记录应用一次状态变化，返回新记录。"""
    current = task.get("state")
    if current not in TASK_STATES:
        raise TransitionError(f"unknown state: {current!r}")
    if record.to_state not in TASK_STATES:
        raise TransitionError(f"unknown target state: {record.to_state!r}")
    if record.from_state != current:
        raise TransitionError(
            f"transition source mismatch: task is {current}, record says "
            f"{record.from_state}"
        )
    allowed = TASK_TRANSITIONS[current]
    if record.to_state not in allowed:
        raise TransitionError(
            f"illegal transition: {current} -> {record.to_state}"
        )
    if not isinstance(record.evidence_ref, str) or not record.evidence_ref.strip():
        raise TransitionError("every transition requires a non-empty evidence_ref")
    for field_name, refs in (("artifacts_in", record.artifacts_in), ("artifacts_out", record.artifacts_out)):
        if not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise TransitionError(f"every transition requires non-empty {field_name}")
    if not record.actor:
        raise TransitionError("actor is required")
    if not record.reason:
        raise TransitionError("reason is required")
    if not record.at:
        raise TransitionError("transition time is required")
    if current == "failed" and record.to_state == "proposed":
        if record.actor != "user" and not record.actor.startswith("user:"):
            raise TransitionError("failed task retry requires an explicit user decision")
        if not record.evidence_ref:
            raise TransitionError("failed task retry requires user evidence")
    updated = dict(task)
    updated["state"] = record.to_state
    history = list(task.get("transitions", []))
    history.append(
        {
            "from_state": record.from_state,
            "to_state": record.to_state,
            "actor": record.actor,
            "reason": record.reason,
            "evidence_ref": record.evidence_ref,
            "at": record.at,
            "artifacts_in": list(record.artifacts_in),
            "artifacts_out": list(record.artifacts_out),
        }
    )
    updated["transitions"] = history
    return updated


# ---------------------------------------------------------------------------
# 预算与资源控制（纯计算部分）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Budget:
    """时间、token、费用、尝试次数预算；unit 由调用方约定。"""

    max_duration_seconds: int | None = None
    max_tokens: int | None = None
    max_cost: float | None = None
    max_attempts: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_duration_seconds",
            "max_tokens",
            "max_cost",
            "max_attempts",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise TransitionError(f"{name} cannot be negative")

    def exceeded(self, usage: dict[str, Any]) -> str | None:
        """返回第一个超限维度名；未超限返回 None。"""
        for name, value in usage.items():
            if name in {"duration_seconds", "tokens", "cost", "attempts"}:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise TransitionError(f"invalid usage value for {name}")
                if value < 0:
                    raise TransitionError(f"usage {name} cannot be negative")
        if self.max_duration_seconds is not None:
            used = usage.get("duration_seconds", 0)
            if used > self.max_duration_seconds:
                return "duration_seconds"
        if self.max_tokens is not None:
            used = usage.get("tokens", 0)
            if used > self.max_tokens:
                return "tokens"
        if self.max_cost is not None:
            used = usage.get("cost", 0.0)
            if used > self.max_cost:
                return "cost"
        if self.max_attempts is not None:
            used = usage.get("attempts", 0)
            if used > self.max_attempts:
                return "attempts"
        return None


# 重试策略三类：safe_auto（安全自动）、manual（需人工）、never（禁止）。
RETRY_POLICIES = frozenset({"safe_auto", "manual", "never"})


def retry_allowed(policy: str, attempts_used: int, max_attempts: int) -> bool:
    if policy not in RETRY_POLICIES:
        raise TransitionError(f"unknown retry policy: {policy!r}")
    if policy == "never":
        return False
    if policy == "manual":
        return False  # 人工策略下自动重试一律不允许，需人工决策
    return attempts_used < max_attempts


# ---------------------------------------------------------------------------
# 幂等与并发所有权
# ---------------------------------------------------------------------------


class OwnershipConflict(ValueError):
    """产物所有权冲突：两个活跃任务声明了同一产物。"""


def check_artifact_ownership(
    active_tasks: dict[str, dict[str, Any]],
    claimed_artifacts: frozenset[str],
) -> None:
    """拒绝两个未终态任务对同一产物的所有权声明。"""
    for task_id, task in active_tasks.items():
        if task.get("state") in ("delivered", "failed", "cancelled"):
            continue
        existing = frozenset(task.get("artifacts_owned", []))
        clash = existing & claimed_artifacts
        if clash:
            raise OwnershipConflict(
                f"artifact ownership conflict between {task_id} and claim: "
                f"{sorted(clash)}"
            )


def idempotency_key(actor: str, action: str, payload: Any) -> str:
    """幂等键：同键必须返回同一结果或明确冲突。"""
    from .identity import content_fingerprint  # 局部引入避免循环

    return content_fingerprint({"actor": actor, "action": action, "payload": payload})
