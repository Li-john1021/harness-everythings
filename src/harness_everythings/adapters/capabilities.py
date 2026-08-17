"""运行时与工作区适配器能力注册合同（Spec 第 16 节）。

只描述能力，不执行工作区脚本、不绑定供应商。每项首选能力都必须
有串行或人工退化路径；如实报告，不得伪装不存在的功能。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 能力键（clean-room 新设计，非历史接口复刻）。
CAPABILITY_KEYS = frozenset({
    "structured_questions",   # 结构化提问
    "subagents",              # 原生子代理
    "parallel_execution",     # 并行执行
    "hooks",                  # Hook
    "persistent_tasks",       # 持久任务
    "image_tools",            # 图像工具
    "audio_tools",            # 音频工具
    "video_tools",            # 视频工具
    "approval_ui",            # 批准界面
    "network_access",         # 网络访问
    "model_calls",            # 模型调用
})

# 每项能力的退化路径：serial（串行）或 manual（人工）。
DEGRADATION_PATHS: dict[str, str] = {
    "structured_questions": "manual",
    "subagents": "serial",
    "parallel_execution": "serial",
    "hooks": "manual",
    "persistent_tasks": "serial",
    "image_tools": "manual",
    "audio_tools": "manual",
    "video_tools": "manual",
    "approval_ui": "manual",
    "network_access": "manual",
    "model_calls": "manual",
}


class CapabilityError(ValueError):
    """能力声明无效或伪装了不存在的功能。"""


@dataclass(frozen=True)
class CapabilitySet:
    """一个适配器如实报告的能力集合。"""

    adapter_id: str
    adapter_kind: str  # runtime | workspace
    capabilities: frozenset[str]
    version: str = "1.0"

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_id, str) or not self.adapter_id.strip():
            raise CapabilityError("adapter_id must be a non-empty string")
        if not isinstance(self.adapter_kind, str):
            raise CapabilityError("adapter_kind must be a string")
        if not isinstance(self.version, str) or not self.version.strip():
            raise CapabilityError("version must be a non-empty string")
        if not isinstance(self.capabilities, frozenset):
            raise CapabilityError("capabilities must be a frozenset of strings")
        if not all(isinstance(capability, str) and capability for capability in self.capabilities):
            raise CapabilityError("capabilities must contain non-empty strings")
        unknown = self.capabilities - CAPABILITY_KEYS
        if unknown:
            raise CapabilityError(
                f"unknown capabilities declared: {sorted(unknown)}"
            )
        if self.adapter_kind not in ("runtime", "workspace"):
            raise CapabilityError(f"invalid adapter kind: {self.adapter_kind!r}")
        requirements = {
            "subagents": {"model_calls"},
            "persistent_tasks": {"model_calls"},
        }
        for capability, required in requirements.items():
            if capability in self.capabilities and not required.issubset(self.capabilities):
                raise CapabilityError(
                    f"capability {capability!r} requires {sorted(required)}"
                )

    def has(self, capability: str) -> bool:
        if capability not in CAPABILITY_KEYS:
            raise CapabilityError(f"unknown capability: {capability!r}")
        return capability in self.capabilities

    def degraded_plan(self) -> dict[str, str]:
        """返回缺失能力的退化路径映射（不缺失的能力不列）。"""
        return {
            cap: DEGRADATION_PATHS[cap]
            for cap in sorted(CAPABILITY_KEYS - self.capabilities)
        }

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "adapter_id": self.adapter_id,
            "adapter_kind": self.adapter_kind,
            "capabilities": sorted(self.capabilities),
            "degradation_paths": self.degraded_plan(),
            "version": self.version,
        }


# 能力注册表：H1 只注册内核自带的两个退化适配器，不做供应商绑定。
_REGISTRY: dict[str, CapabilitySet] = {}


def register(capability_set: CapabilitySet) -> None:
    _REGISTRY[capability_set.adapter_id] = capability_set


def lookup(adapter_id: str) -> CapabilitySet:
    if adapter_id not in _REGISTRY:
        raise CapabilityError(f"adapter not registered: {adapter_id!r}")
    return _REGISTRY[adapter_id]


def reset_registry() -> None:
    """仅供测试使用。"""
    _REGISTRY.clear()


def ensure_contract_intact(adapter_id: str) -> bool:
    """能力退化后仍保持相同状态与证据合同（H3 前置检查）。"""
    cs = lookup(adapter_id)
    # 合同完整性 = 所有缺失能力都有退化路径。
    return all(
        cap in DEGRADATION_PATHS for cap in CAPABILITY_KEYS - cs.capabilities
    )
