"""单元测试：路径边界、原子写与清单（含 Windows/UTF-8 用例）。"""

from __future__ import annotations

import os
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from harness_everythings.core.identity import load_canonical
from harness_everythings.storage.atomic import (
    AtomicWriteError,
    read_json,
    write_atomic,
)
from harness_everythings.storage.manifest import (
    ApplicationManifest,
    ManifestError,
    PlannedWrite,
    apply_manifest,
    build_manifest,
    tree_fingerprint,
    workspace_fingerprint,
)
from harness_everythings.core.identity import bytes_fingerprint
from harness_everythings.storage.paths import (
    PathBoundaryError,
    metadata_rel_path,
    normalize_rel_path,
    resolve_in_root,
)


class TestNormalizeRelPath:
    def test_simple(self):
        assert normalize_rel_path("a/b.json") == "a/b.json"

    def test_backslash_converted(self):
        assert normalize_rel_path("a\\b.json") == "a/b.json"

    def test_redundant_dots_resolved(self):
        assert normalize_rel_path("a/./b.json") == "a/b.json"

    def test_traversal_rejected(self):
        with pytest.raises(PathBoundaryError):
            normalize_rel_path("../escape.json")

    def test_absolute_rejected(self):
        with pytest.raises(PathBoundaryError):
            normalize_rel_path("/etc/passwd")

    def test_windows_drive_rejected(self):
        with pytest.raises(PathBoundaryError):
            normalize_rel_path("C:/tmp/x.json")

    def test_empty_rejected(self):
        with pytest.raises(PathBoundaryError):
            normalize_rel_path("")

    def test_metadata_rel_path(self):
        assert (
            metadata_rel_path("harness.json")
            == ".harness-everythings/harness.json"
        )

    @pytest.mark.parametrize("raw", ["CON.txt", "a:NUL", "bad. ", "x\x00y"])
    def test_non_portable_windows_names_rejected(self, raw: str):
        with pytest.raises(PathBoundaryError):
            normalize_rel_path(raw)


class TestResolveInRoot:
    def test_inside_ok(self, tmp_path: Path):
        target = resolve_in_root(tmp_path, "sub/file.json")
        assert target == tmp_path / "sub" / "file.json"

    def test_escape_rejected(self, tmp_path: Path):
        with pytest.raises(PathBoundaryError):
            resolve_in_root(tmp_path, "../outside.json")

    def test_symlink_escape_rejected(self, tmp_path: Path):
        outside = tmp_path.parent / "hxt-outside"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "link-out"
        if link.is_symlink():
            link.unlink()
        link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(PathBoundaryError):
            resolve_in_root(tmp_path, "link-out/x.json")

    def test_non_ascii_workspace(self, tmp_path: Path):
        target = resolve_in_root(tmp_path, "数据目录/文件 名.json")
        assert "数据目录" in str(target)

    def test_spaces_in_path(self, tmp_path: Path):
        target = resolve_in_root(tmp_path, "my dir/my file.json")
        assert target.parent.name == "my dir"


class TestAtomicWrite:
    def test_write_and_read_roundtrip(self, tmp_path: Path):
        result = write_atomic(tmp_path, "a/b.json", {"k": "v"})
        assert result["before"] is None
        assert read_json(tmp_path, "a/b.json") == {"k": "v"}

    def test_overwrite_keeps_before_fingerprint(self, tmp_path: Path):
        write_atomic(tmp_path, "f.json", {"v": 1})
        result = write_atomic(tmp_path, "f.json", {"v": 2})
        assert result["before"] is not None
        assert result["before"] != result["after"]

    def test_exclusive_refuses_existing(self, tmp_path: Path):
        write_atomic(tmp_path, "f.json", {"v": 1})
        with pytest.raises(AtomicWriteError):
            write_atomic(tmp_path, "f.json", {"v": 2}, exclusive=True)

    def test_exclusive_create_new_ok(self, tmp_path: Path):
        write_atomic(tmp_path, "new.json", {"v": 1}, exclusive=True)

    def test_no_tmp_left_behind_on_failure(self, tmp_path: Path):
        write_atomic(tmp_path, "f.json", {"v": 1})
        # 独占冲突走失败路径
        with pytest.raises(AtomicWriteError):
            write_atomic(tmp_path, "f.json", {"v": 2}, exclusive=True)
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".hxtmp-")]
        assert leftovers == []

    def test_canonical_bytes_written(self, tmp_path: Path):
        write_atomic(tmp_path, "f.json", {"b": 1, "a": 2})
        raw = (tmp_path / "f.json").read_bytes()
        assert raw == b'{"a":2,"b":1}'

    def test_non_ascii_content(self, tmp_path: Path):
        write_atomic(tmp_path, "f.json", {"名": "值"})
        assert read_json(tmp_path, "f.json") == {"名": "值"}

    def test_boundary_escape_rejected(self, tmp_path: Path):
        with pytest.raises(PathBoundaryError):
            write_atomic(tmp_path, "../out.json", {"k": 1})


class TestManifest:
    def test_tree_fingerprint_records_directory_symlink(self, tmp_path: Path):
        outside = tmp_path.parent / "hxt-tree-target"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "linked-dir"
        before = tree_fingerprint(tmp_path)
        link.symlink_to(outside, target_is_directory=True)
        after = tree_fingerprint(tmp_path)
        assert before != after

    def test_tree_fingerprint_changes_when_directory_symlink_target_changes(self, tmp_path: Path):
        first_target = tmp_path / "target-a"
        second_target = tmp_path / "target-b"
        first_target.mkdir()
        second_target.mkdir()
        link = tmp_path / "linked-dir"
        link.symlink_to(first_target, target_is_directory=True)
        before = tree_fingerprint(tmp_path)
        link.unlink()
        link.symlink_to(second_target, target_is_directory=True)
        after = tree_fingerprint(tmp_path)
        assert before != after

    def test_dry_run_no_side_effects(self, tmp_path: Path):
        writes = [PlannedWrite(rel=".harness-everythings/harness.json", payload={"v": 1}, exclusive=True)]
        manifest = build_manifest(tmp_path, writes, "2026-08-16T00:00:00Z")
        # dry-run：只构建清单，不写文件
        assert not (tmp_path / ".harness-everythings").exists()

    def test_apply_after_approval(self, tmp_path: Path):
        writes = [PlannedWrite(rel=".harness-everythings/harness.json", payload={"v": 1}, exclusive=True)]
        manifest = build_manifest(tmp_path, writes, "2026-08-16T00:00:00Z")
        approval = {
            "approver": "user",
            "scope": "init",
            "decision": "approved",
            "approved_manifest_fingerprint": manifest.fingerprint(),
        }
        result = apply_manifest(tmp_path, manifest, approval)
        assert result["applied"] == 1
        assert read_json(tmp_path, ".harness-everythings/harness.json") == {"v": 1}

    def test_apply_without_approval_refused(self, tmp_path: Path):
        writes = [PlannedWrite(rel="x.json", payload={"v": 1})]
        manifest = build_manifest(tmp_path, writes, "2026-08-16T00:00:00Z")
        with pytest.raises(ManifestError):
            apply_manifest(tmp_path, manifest, {"decision": "nope"})
        assert not (tmp_path / "x.json").exists()

    def test_approval_requires_actor_and_scope(self, tmp_path: Path):
        manifest = build_manifest(
            tmp_path, [PlannedWrite(rel="x.json", payload={"v": 1})],
            "2026-08-16T00:00:00Z",
        )
        with pytest.raises(ManifestError):
            apply_manifest(
                tmp_path,
                manifest,
                {
                    "decision": "approved",
                    "approved_manifest_fingerprint": manifest.fingerprint(),
                },
            )

    def test_fingerprint_conflict_rejected(self, tmp_path: Path):
        writes = [PlannedWrite(rel="x.json", payload={"v": 1})]
        manifest = build_manifest(tmp_path, writes, "2026-08-16T00:00:00Z")
        approval = {
            "approver": "user",
            "scope": "init",
            "decision": "approved",
            "approved_manifest_fingerprint": manifest.fingerprint(),
        }
        # 在 apply 前工作区发生变化 -> 前置指纹不再匹配
        (tmp_path / "x.json").write_text("user touched", encoding="utf-8")
        with pytest.raises(ManifestError):
            apply_manifest(tmp_path, manifest, approval)
        assert (tmp_path / "x.json").read_text(encoding="utf-8") == "user touched"

    def test_user_file_protection(self, tmp_path: Path):
        (tmp_path / "user-file.json").write_text("{}", encoding="utf-8")
        writes = [PlannedWrite(rel="user-file.json", payload={"v": 1}, exclusive=True)]
        manifest = build_manifest(tmp_path, writes, "2026-08-16T00:00:00Z")
        approval = {
            "approver": "user",
            "scope": "init",
            "decision": "approved",
            "approved_manifest_fingerprint": manifest.fingerprint(),
        }
        with pytest.raises(ManifestError):
            apply_manifest(tmp_path, manifest, approval)
        assert (tmp_path / "user-file.json").read_text(encoding="utf-8") == "{}"

    def test_workspace_fingerprint_stable(self, tmp_path: Path):
        watched = ("a.json", "b.json")
        f1 = workspace_fingerprint(tmp_path, watched)
        f2 = workspace_fingerprint(tmp_path, watched)
        assert f1 == f2
        write_atomic(tmp_path, "a.json", {"v": 1})
        assert workspace_fingerprint(tmp_path, watched) != f1

    def test_manifest_record_shape(self, tmp_path: Path):
        writes = [PlannedWrite(rel="x.json", payload={"v": 1})]
        manifest = build_manifest(tmp_path, writes, "2026-08-16T00:00:00Z")
        record = manifest.to_record()
        assert record["entity_type"] == "application-manifest"
        assert record["writes"][0]["rel"] == "x.json"
        assert record["writes"][0]["exclusive"] is True

    def test_payload_changes_idempotency_key(self, tmp_path: Path):
        first = build_manifest(
            tmp_path, [PlannedWrite("x.json", {"v": 1})],
            "2026-08-16T00:00:00Z",
        )
        second = build_manifest(
            tmp_path, [PlannedWrite("x.json", {"v": 2})],
            "2026-08-16T00:00:00Z",
        )
        assert first.idempotency_key != second.idempotency_key

    def test_duplicate_normalized_target_rejected(self, tmp_path: Path):
        with pytest.raises(ManifestError):
            build_manifest(
                tmp_path,
                [
                    PlannedWrite("a/b.json", {"v": 1}),
                    PlannedWrite("a\\b.json", {"v": 2}),
                ],
                "2026-08-16T00:00:00Z",
            )

    def test_existing_target_requires_bound_fingerprint(self, tmp_path: Path):
        target = tmp_path / "generated.json"
        target.write_bytes(b'{"v":1}')
        unbound = build_manifest(
            tmp_path, [PlannedWrite("generated.json", {"v": 2})],
            "2026-08-16T00:00:00Z",
        )
        approval = {
            "approver": "user",
            "scope": "update",
            "decision": "approved",
            "approved_manifest_fingerprint": unbound.fingerprint(),
        }
        with pytest.raises(ManifestError):
            apply_manifest(tmp_path, unbound, approval)

        bound = build_manifest(
            tmp_path,
            [
                PlannedWrite(
                    "generated.json",
                    {"v": 2},
                    exclusive=False,
                    expected_before_fingerprint=bytes_fingerprint(target.read_bytes()),
                )
            ],
            "2026-08-16T00:00:00Z",
        )
        approval["approved_manifest_fingerprint"] = bound.fingerprint()
        apply_manifest(tmp_path, bound, approval)
        assert read_json(tmp_path, "generated.json") == {"v": 2}

    def test_manifest_sink_rejects_mutation_after_preflight(self, tmp_path: Path, monkeypatch):
        import harness_everythings.storage.manifest as manifest_module

        target = tmp_path / "generated.json"
        target.write_bytes(b'{"v":1}')
        manifest = build_manifest(
            tmp_path,
            [PlannedWrite("generated.json", {"v": 2}, exclusive=False, expected_before_fingerprint=bytes_fingerprint(target.read_bytes()))],
            "2026-08-16T00:00:00Z",
        )
        approval = {"approver": "user", "scope": "update", "decision": "approved", "approved_manifest_fingerprint": manifest.fingerprint()}
        real_write = manifest_module.write_atomic

        def mutate_then_write(root, rel, payload, **kwargs):
            target.write_bytes(b'{"user":"changed"}')
            return real_write(root, rel, payload, **kwargs)

        monkeypatch.setattr(manifest_module, "write_atomic", mutate_then_write)
        with pytest.raises(ManifestError, match="rolled back|fingerprint"):
            apply_manifest(tmp_path, manifest, approval)
        assert target.read_bytes() == b'{"user":"changed"}'

    def test_cross_process_writers_share_workspace_lock(self, tmp_path: Path):
        target = tmp_path / "generated.json"
        write_atomic(tmp_path, "generated.json", {"value": "original"}, exclusive=True)
        expected = bytes_fingerprint(target.read_bytes())
        child = r'''
import json
import sys
import time
from pathlib import Path
from harness_everythings.storage import atomic

root = Path(sys.argv[1])
identity = sys.argv[2]
expected = sys.argv[3]
hold_replace = sys.argv[4] == "hold"
real_replace = atomic.os.replace

def synchronized_replace(source, destination):
    (root / f"ready-{identity}").write_text("1", encoding="utf-8")
    deadline = time.monotonic() + 10
    while not (root / "release").is_file():
        if time.monotonic() >= deadline:
            raise RuntimeError("test barrier timeout")
        time.sleep(0.01)
    real_replace(source, destination)

if hold_replace:
    atomic.os.replace = synchronized_replace
(root / f"started-{identity}").write_text("1", encoding="utf-8")
try:
    result = atomic.write_atomic(
        root,
        "generated.json",
        {"value": identity},
        exclusive=False,
        expected_before_fingerprint=expected,
    )
    outcome = {"ok": True, "result": result}
except Exception as exc:
    outcome = {"ok": False, "error": str(exc)}
(root / f"result-{identity}.json").write_text(json.dumps(outcome), encoding="utf-8")
'''
        env = dict(os.environ)
        src_root = str(Path(__file__).resolve().parents[2] / "src")
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (src_root, env.get("PYTHONPATH"))))
        first = subprocess.Popen(
            [sys.executable, "-c", child, str(tmp_path), "first", expected, "hold"],
            env=env,
        )
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready-first").is_file():
            assert first.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        second = subprocess.Popen(
            [sys.executable, "-c", child, str(tmp_path), "second", expected, "normal"],
            env=env,
        )
        while not (tmp_path / "started-second").is_file():
            assert second.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        (tmp_path / "release").write_text("1", encoding="utf-8")
        assert first.wait(timeout=10) == 0
        assert second.wait(timeout=10) == 0
        first_result = json.loads((tmp_path / "result-first.json").read_text(encoding="utf-8"))
        second_result = json.loads((tmp_path / "result-second.json").read_text(encoding="utf-8"))
        assert first_result["ok"] is True
        assert second_result["ok"] is False
        assert "fingerprint changed" in second_result["error"]
        assert read_json(tmp_path, "generated.json") == {"value": "first"}
