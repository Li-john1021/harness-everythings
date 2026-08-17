"""路径边界与规范化（Spec 第 14、17 节）。

所有写入目标必须规范化后验证位于获准工作区根目录内；拒绝路径穿越
和越界符号链接。统一使用 POSIX 风格内部表示，落盘时按平台转换。
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

FORBIDDEN_NAMES = frozenset({".", ".."})
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class PathBoundaryError(ValueError):
    """路径逃逸、穿越或指向保留位置。"""


def normalize_rel_path(raw: str) -> str:
    """把用户/生成器提供的相对路径规范化为 POSIX 内部形式。

    拒绝：绝对路径、.. 穿越、反斜杠歧义（统一转换）、空段、驱动器盘符。
    """
    if not raw:
        raise PathBoundaryError("empty path")
    if "\\" in raw:
        raw = raw.replace("\\", "/")
    if raw.startswith("/") or re_drive_letter(raw):
        raise PathBoundaryError(f"absolute path not allowed: {raw!r}")
    posix = PurePosixPath(raw)
    parts = posix.parts
    if not parts:
        raise PathBoundaryError(f"empty path: {raw!r}")
    for part in parts:
        if part in FORBIDDEN_NAMES:
            raise PathBoundaryError(f"path traversal not allowed: {raw!r}")
        if ":" in part:
            raise PathBoundaryError(f"colon not portable in path: {raw!r}")
        if any(ord(char) < 32 for char in part):
            raise PathBoundaryError(f"control character not allowed: {raw!r}")
        if part.endswith((" ", ".")):
            raise PathBoundaryError(f"trailing dot/space not portable: {raw!r}")
        if part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
            raise PathBoundaryError(f"reserved Windows name: {raw!r}")
    if any(part == "" for part in raw.split("/")):
        raise PathBoundaryError(f"empty segment in path: {raw!r}")
    return str(posix)


def re_drive_letter(segment: str) -> bool:
    """检测 Windows 盘符（如 C: 或 C:/）。"""
    return (
        len(segment) >= 2
        and segment[0].isalpha()
        and segment[1] == ":"
    )


def resolve_in_root(root: Path, rel: str) -> Path:
    """把规范化相对路径解析到根目录内，检查符号链接越界。

    - rel 必须先通过 normalize_rel_path。
    - root 必须已存在（边界锚点）。
    - 逐级解析，任何已存在前缀若是符号链接并指向根外，即拒绝。
    """
    rel_norm = normalize_rel_path(rel)
    root_real = root.resolve(strict=True)
    target = root / Path(*PurePosixPath(rel_norm).parts)
    # 逐级检查：已存在的祖先若为符号链接，必须仍解析在根内。
    current = root_real
    for part in PurePosixPath(rel_norm).parts:
        current = current / part
        if current.is_symlink():
            resolved = current.resolve(strict=False)
            try:
                resolved.relative_to(root_real)
            except ValueError as exc:
                raise PathBoundaryError(
                    f"symlink escapes workspace root: {rel_norm!r}"
                ) from exc
    # 最终目标（若已存在）也必须在根内。
    if target.exists():
        resolved = target.resolve(strict=True)
        try:
            resolved.relative_to(root_real)
        except ValueError as exc:
            raise PathBoundaryError(
                f"resolved path escapes workspace root: {rel_norm!r}"
            ) from exc
    return target


def workspace_dir_name() -> str:
    """被治理工作区内的元数据目录名（D-003）。"""
    return ".harness-everythings"


def metadata_rel_path(*parts: str) -> str:
    """构造 .harness-everythings/ 下的规范化相对路径。"""
    return normalize_rel_path("/".join((workspace_dir_name(), *parts)))
