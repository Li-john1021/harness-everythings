"""Dry-run ApplicationManifest 与两步写入合同。

写入遵循：发现/计算 -> 生成 ApplicationManifest 与 diff -> 用户批准
-> apply 精确清单。apply 前重新验证工作区指纹；指纹变化即拒绝，
不得在旧批准上继续（HANDOFF 第 6 节）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.identity import bytes_fingerprint, content_fingerprint
from .atomic import _write_bytes_atomic, write_atomic, write_lock
from .paths import normalize_rel_path, resolve_in_root


class ManifestError(ValueError):
    """清单无效、前置指纹冲突或目标被用户占用。"""


@dataclass(frozen=True)
class PlannedWrite:
    """清单中的一项计划写入。"""

    rel: str
    payload: Any
    exclusive: bool = True  # 新文件安全默认：目标必须不存在
    expected_before_fingerprint: str | None = None

    def key(self) -> str:
        return content_fingerprint(
            {
                "rel": normalize_rel_path(self.rel),
                "exclusive": self.exclusive,
                "expected_before_fingerprint": self.expected_before_fingerprint,
                "target_fingerprint": content_fingerprint(self.payload),
            }
        )


@dataclass(frozen=True)
class ApplicationManifest:
    """dry-run 产物：精确目标、前置指纹、目标哈希与回退信息。"""

    workspace_fingerprint: str
    writes: tuple[PlannedWrite, ...]
    created_at: str
    idempotency_key: str
    snapshot_workspace: bool = False
    snapshot_excludes: tuple[str, ...] = ()

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "entity_type": "application-manifest",
            "workspace_fingerprint": self.workspace_fingerprint,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            **(
                {
                    "snapshot_workspace": True,
                    "snapshot_excludes": list(self.snapshot_excludes),
                }
                if self.snapshot_workspace
                else {}
            ),
            "writes": [
                {
                    "rel": w.rel,
                    "exclusive": w.exclusive,
                    "target_fingerprint": content_fingerprint(w.payload),
                    **(
                        {"expected_before_fingerprint": w.expected_before_fingerprint}
                        if w.expected_before_fingerprint is not None
                        else {}
                    ),
                }
                for w in self.writes
            ],
        }

    def fingerprint(self) -> str:
        return content_fingerprint(self.to_record())


def workspace_fingerprint(root: Path, watched: tuple[str, ...]) -> str:
    """对受监视相对路径集合计算工作区指纹（固定输入 -> 固定输出）。

    只对清单声明的路径计算，避免整个工作区扫描引入平台差异。
    缺失文件以 "absent" 参与，目录内容不递归。
    """
    entries: dict[str, Any] = {}
    for rel in sorted(watched):
        target = resolve_in_root(root, rel)
        if target.is_file():
            entries[rel] = bytes_fingerprint(target.read_bytes())
        elif target.is_dir():
            entries[rel] = "dir"
        else:
            entries[rel] = "absent"
    return content_fingerprint({"workspace": entries})


def tree_fingerprint(root: Path, excludes: tuple[str, ...] = ()) -> str:
    """Fingerprint all readable regular files below root without following links."""
    excluded = {item.strip("/").replace("\\", "/") for item in excludes}
    entries: list[tuple[str, str]] = []
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        rel_current = current_path.relative_to(root).as_posix() if current_path != root else ""
        kept_dirs: list[str] = []
        for name in sorted(dirs):
            rel = f"{rel_current}/{name}".strip("/")
            if name == ".git" or rel in excluded or any(rel.startswith(item + "/") for item in excluded):
                continue
            path = current_path / name
            if path.is_symlink():
                try:
                    target = os.readlink(path)
                except OSError as exc:
                    raise ManifestError(f"cannot read symlink target: {rel}") from exc
                entries.append((rel, f"symlink-dir:{content_fingerprint(target)}"))
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(names):
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            if any(rel == item or rel.startswith(item + "/") for item in excluded):
                continue
            if path.is_symlink():
                try:
                    target = os.readlink(path)
                except OSError as exc:
                    raise ManifestError(f"cannot read symlink target: {rel}") from exc
                entries.append((rel, f"symlink:{content_fingerprint(target)}"))
                continue
            if path.is_file():
                entries.append((rel, bytes_fingerprint(path.read_bytes())))
    return content_fingerprint({"tree": entries})


def build_manifest(
    root: Path,
    writes: list[PlannedWrite],
    now: str,
    *,
    snapshot_workspace: bool = False,
    snapshot_excludes: tuple[str, ...] = (),
) -> ApplicationManifest:
    """构建 dry-run 清单：捕获前置指纹与独占创建要求。"""
    normalized: list[PlannedWrite] = []
    seen: set[str] = set()
    for write in writes:
        rel = normalize_rel_path(write.rel)
        resolve_in_root(root, rel)
        if rel in seen:
            raise ManifestError(f"duplicate target in manifest: {rel!r}")
        if write.exclusive and write.expected_before_fingerprint is not None:
            raise ManifestError(
                f"exclusive write cannot declare an existing fingerprint: {rel!r}"
            )
        seen.add(rel)
        normalized.append(
            PlannedWrite(
                rel=rel,
                payload=write.payload,
                exclusive=write.exclusive,
                expected_before_fingerprint=write.expected_before_fingerprint,
            )
        )
    watched = tuple(sorted(seen))
    preflight = tree_fingerprint(root, snapshot_excludes) if snapshot_workspace else workspace_fingerprint(root, watched)
    return ApplicationManifest(
        workspace_fingerprint=preflight,
        writes=tuple(normalized),
        created_at=now,
        idempotency_key=content_fingerprint(
            {
                "watched": list(watched),
                "writes": [w.key() for w in normalized],
                "snapshot_workspace": snapshot_workspace,
                "snapshot_excludes": list(snapshot_excludes),
            }
        ),
        snapshot_workspace=snapshot_workspace,
        snapshot_excludes=tuple(snapshot_excludes),
    )


def _apply_manifest_locked(
    root: Path,
    manifest: ApplicationManifest,
    approval: dict[str, Any],
) -> dict[str, Any]:
    """应用已批准的清单；前置校验失败即拒绝且零写入。

    approval 必须包含：approver、scope、decision=approved、
    approved_manifest_fingerprint 与清单指纹一致。
    """
    expected = manifest.fingerprint()
    if approval.get("decision") != "approved":
        raise ManifestError("manifest is not approved")
    if not approval.get("approver") or not approval.get("scope"):
        raise ManifestError("manifest approval requires approver and scope")
    if approval.get("approved_manifest_fingerprint") != expected:
        raise ManifestError("approval fingerprint does not match manifest")
    current = (
        tree_fingerprint(root, manifest.snapshot_excludes)
        if manifest.snapshot_workspace
        else workspace_fingerprint(root, tuple(sorted({w.rel for w in manifest.writes})))
    )
    if current != manifest.workspace_fingerprint:
        raise ManifestError(
            "workspace fingerprint changed since manifest; re-diff required"
        )
    snapshots: dict[str, bytes | None] = {}
    seen: set[str] = set()
    for w in manifest.writes:
        rel = normalize_rel_path(w.rel)
        if rel != w.rel or rel in seen:
            raise ManifestError(f"invalid or duplicate manifest target: {w.rel!r}")
        seen.add(rel)
        target = resolve_in_root(root, rel)
        if target.exists() and not target.is_file():
            raise ManifestError(f"manifest target is not a file: {rel!r}")
        before = target.read_bytes() if target.is_file() else None
        if w.exclusive and before is not None:
            raise ManifestError(f"exclusive target already exists: {rel!r}")
        if before is not None:
            actual = bytes_fingerprint(before)
            if not w.expected_before_fingerprint:
                raise ManifestError(
                    f"overwrite requires expected_before_fingerprint: {rel!r}"
                )
            if actual != w.expected_before_fingerprint:
                raise ManifestError(f"target fingerprint changed: {rel!r}")
        elif w.expected_before_fingerprint is not None:
            raise ManifestError(f"expected existing target is absent: {rel!r}")
        elif not w.exclusive:
            raise ManifestError(
                f"new target must use exclusive creation: {rel!r}"
            )
        snapshots[rel] = before

    results = []
    try:
        for w in manifest.writes:
            write_kwargs: dict[str, Any] = {"exclusive": w.exclusive}
            if w.expected_before_fingerprint is not None:
                write_kwargs["expected_before_fingerprint"] = w.expected_before_fingerprint
            result = write_atomic(root, w.rel, w.payload, **write_kwargs)
            results.append(result)
    except BaseException as exc:
        rollback_errors: list[str] = []
        for result in reversed(results):
            rel = result["rel"]
            target = resolve_in_root(root, rel)
            try:
                before = snapshots[rel]
                if not target.is_file():
                    rollback_errors.append(f"{rel}: applied target is missing")
                    continue
                if bytes_fingerprint(target.read_bytes()) != result["after"]:
                    rollback_errors.append(f"{rel}: target changed during rollback")
                    continue
                if before is None:
                    target.unlink()
                else:
                    _write_bytes_atomic(root, rel, before)
            except Exception as rollback_exc:
                rollback_errors.append(f"{rel}: {rollback_exc}")
        if not isinstance(exc, Exception):
            raise
        detail = f"manifest apply failed and was rolled back: {exc}"
        if rollback_errors:
            detail += f"; rollback errors: {rollback_errors}"
        raise ManifestError(detail) from exc
    return {
        "applied": len(results),
        "results": results,
        "manifest_fingerprint": expected,
    }


def apply_manifest(
    root: Path,
    manifest: ApplicationManifest,
    approval: dict[str, Any],
) -> dict[str, Any]:
    """在同一互斥临界区内预检并应用已批准清单。"""
    with write_lock(root):
        return _apply_manifest_locked(root, manifest, approval)


__all__ = [
    "ApplicationManifest",
    "ManifestError",
    "PlannedWrite",
    "apply_manifest",
    "build_manifest",
    "tree_fingerprint",
    "workspace_fingerprint",
]
