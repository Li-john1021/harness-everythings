"""稳定 ID 与实体封套规则。

所有 canonical 实体至少带 schema_version、稳定 ID、创建/更新时间
和来源引用。字段命名以本模块冻结的 Schema 为准（clean-room 新设计）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .identity import content_fingerprint

# 稳定 ID：小写命名空间 + 冒号 + 由 canonical 输入派生的指纹片段。
# 不使用递增序号，保证同一逻辑输入在重复初始化时得到同一 ID。
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*:[0-9a-f]{16}$")

ENTITY_TYPES = frozenset({
    "workspace",
    "profile-record",
    "plan",
    "output-contract",
    "role",
    "task",
    "artifact",
    "evidence",
    "approval",
    "handoff",
    "governance-proposal",
    "application-manifest",
    "content-brief",
    "content-output-contract",
    "content-variant",
    "content-review",
    "verification-results",
    "traceability",
    "evaluation",
    "governance-effect",
    "checkpoint",
})


class IdentityError(ValueError):
    """ID 或封套不符合冻结规则。"""


def derive_id(namespace: str, seed: Any) -> str:
    """从确定性输入派生稳定 ID：namespace + 指纹前 16 位。"""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", namespace):
        raise IdentityError(f"invalid id namespace: {namespace!r}")
    digest = content_fingerprint({"namespace": namespace, "seed": seed})
    return f"{namespace}:{digest.removeprefix('sha256:')[:16]}"


def validate_id(entity_type: str, entity_id: str) -> None:
    if entity_type not in ENTITY_TYPES:
        raise IdentityError(f"unknown entity type: {entity_type!r}")
    if not _ID_PATTERN.fullmatch(entity_id):
        raise IdentityError(f"malformed id: {entity_id!r}")
    if not entity_id.startswith(entity_type.replace("_", "-") + ":"):
        raise IdentityError(
            f"id {entity_id!r} namespace does not match entity type {entity_type!r}"
        )


@dataclass(frozen=True)
class Envelope:
    """所有 canonical 实体的公共封套字段。"""

    schema_version: str
    entity_id: str
    entity_type: str
    created_at: str  # ISO-8601 UTC，调用方注入固定时钟
    updated_at: str
    source_ref: str  # 来源引用：决策 ID 或证据引用，禁止私有绝对路径
    fields: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        validate_id(self.entity_type, self.entity_id)
        return {
            "schema_version": self.schema_version,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_ref": self.source_ref,
            **self.fields,
        }


SCHEMA_VERSION_CURRENT = "1.0"


def make_envelope(
    entity_type: str,
    seed: Any,
    source_ref: str,
    now: str,
    fields: dict[str, Any] | None = None,
    schema_version: str = SCHEMA_VERSION_CURRENT,
) -> Envelope:
    """按冻结规则构造实体封套。"""
    entity_id = derive_id(entity_type, seed)
    return Envelope(
        schema_version=schema_version,
        entity_id=entity_id,
        entity_type=entity_type,
        created_at=now,
        updated_at=now,
        source_ref=source_ref,
        fields=dict(fields or {}),
    )
