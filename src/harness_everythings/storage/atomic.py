"""原子写与独占创建。

Canonical 写入流程：同目录临时文件 -> 刷盘 -> 原子替换；失败时清理
临时文件，不得留下半写 JSON，也不得破坏旧数据（HANDOFF 第 7 节）。
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.identity import bytes_fingerprint, canonical_bytes, content_fingerprint
from .paths import PathBoundaryError, resolve_in_root


class AtomicWriteError(RuntimeError):
    """原子写失败（不含权限语义，由调用方映射稳定错误类别）。"""


_WRITE_LOCK = threading.RLock()
_LOCK_STATE = threading.local()
_LOCK_TIMEOUT_SECONDS = 30.0


def _workspace_lock_path(root: Path) -> Path:
    root_id = content_fingerprint({"workspace_root": str(root.resolve())}).split(":", 1)[1]
    lock_dir = Path(tempfile.gettempdir()) / "harness-everythings-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{root_id}.lock"


def _acquire_process_lock(handle: Any, *, deadline: float) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    while True:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise AtomicWriteError("timed out waiting for the workspace write lock") from exc
            time.sleep(0.01)


def _release_process_lock(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def write_lock(root: Path):
    """工作区级跨进程互斥；同一线程内允许嵌套写入。"""
    resolved = root.expanduser().resolve()
    key = os.path.normcase(str(resolved))
    with _WRITE_LOCK:
        depths = getattr(_LOCK_STATE, "depths", {})
        if depths.get(key, 0):
            depths[key] += 1
            _LOCK_STATE.depths = depths
            try:
                yield
            finally:
                depths[key] -= 1
            return

        lock_path = _workspace_lock_path(resolved)
        handle = lock_path.open("a+b")
        try:
            _acquire_process_lock(handle, deadline=time.monotonic() + _LOCK_TIMEOUT_SECONDS)
            depths[key] = 1
            _LOCK_STATE.depths = depths
            try:
                yield
            finally:
                depths.pop(key, None)
                _release_process_lock(handle)
        finally:
            handle.close()


def _write_bytes_atomic(
    root: Path,
    rel: str,
    data: bytes,
    *,
    exclusive: bool = False,
    expected_before_fingerprint: str | None = None,
) -> dict[str, Any]:
    """在根目录内原子写入原始字节，返回写入前后指纹。

    exclusive=True 时目标已存在即拒绝（独占创建，保护用户文件）。
    """
    with write_lock(root):
        target = resolve_in_root(root, rel)
        before = None
        if target.exists():
            if exclusive:
                raise AtomicWriteError(
                    f"exclusive create refused, target exists: {rel!r}"
                )
            before = bytes_fingerprint(target.read_bytes())
        if expected_before_fingerprint is not None and before != expected_before_fingerprint:
            raise AtomicWriteError(f"target fingerprint changed before atomic replace: {rel!r}")
        if expected_before_fingerprint is None and not exclusive and target.exists():
            # Unbound direct writes remain supported for low-level callers; manifest
            # writes always provide a bound fingerprint for existing targets.
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".hxtmp-", dir=str(target.parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if exclusive:
                try:
                    # 同目录 hard link 以原子方式实现独占创建，避免检查后覆盖。
                    os.link(tmp_path, target)
                except FileExistsError as exc:
                    raise AtomicWriteError(
                        f"exclusive create refused, target exists: {rel!r}"
                    ) from exc
                tmp_path.unlink()
            else:
                # 最终替换前再次读取旧目标；manifest 的期望指纹在此处生效。
                current = bytes_fingerprint(target.read_bytes()) if target.is_file() else None
                if expected_before_fingerprint is not None and current != expected_before_fingerprint:
                    raise AtomicWriteError(f"target fingerprint changed at atomic replace: {rel!r}")
                os.replace(tmp_path, target)
        except BaseException:
            # 中断或失败：清理临时文件，旧数据保持原样。
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        after = bytes_fingerprint(data)
        return {"rel": rel, "before": before, "after": after}


def write_atomic(
    root: Path,
    rel: str,
    value: Any,
    *,
    exclusive: bool = False,
    expected_before_fingerprint: str | None = None,
) -> dict[str, Any]:
    """在根目录内原子写入 canonical JSON，返回写入前后指纹。"""
    return _write_bytes_atomic(
        root,
        rel,
        canonical_bytes(value),
        exclusive=exclusive,
        expected_before_fingerprint=expected_before_fingerprint,
    )


def read_json(root: Path, rel: str) -> Any:
    """读取根目录内 canonical JSON。"""
    target = resolve_in_root(root, rel)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    from ..core.identity import load_canonical

    return load_canonical(target.read_bytes())


__all__ = [
    "AtomicWriteError",
    "PathBoundaryError",
    "read_json",
    "write_lock",
    "write_atomic",
]
