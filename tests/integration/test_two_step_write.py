"""集成测试：dry-run -> 批准 -> apply 两步写入全流程与幂等性。

使用最小中性 fixture（空临时目录）；H1 不执行工作区脚本、不联网。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness_everythings.core.identity import load_canonical
from harness_everythings.storage.atomic import AtomicWriteError, read_json, write_atomic
from harness_everythings.storage.manifest import (
    ManifestError,
    PlannedWrite,
    apply_manifest,
    build_manifest,
)
from harness_everythings.storage.paths import PathBoundaryError

NOW = "2026-08-16T00:00:00Z"


class TestTwoStepWriteFlow:
    def test_full_flow(self, tmp_path: Path):
        # 1. dry-run：构建清单，无副作用
        writes = [
            PlannedWrite(
                rel=".harness-everythings/harness.json",
                payload={"workspace_name": "中性演示", "v": 1},
                exclusive=True,
            ),
        ]
        manifest = build_manifest(tmp_path, writes, NOW)
        assert not any(tmp_path.iterdir()), "dry-run must not create files"

        # 2. 用户批准（记录清单指纹）
        approval = {
            "approver": "user",
            "scope": "init",
            "decision": "approved",
            "approved_manifest_fingerprint": manifest.fingerprint(),
        }

        # 3. apply：精确写入
        result = apply_manifest(tmp_path, manifest, approval)
        assert result["applied"] == 1
        stored = read_json(tmp_path, ".harness-everythings/harness.json")
        assert stored == {"workspace_name": "中性演示", "v": 1}

    def test_repeat_apply_conflicts_not_duplicates(self, tmp_path: Path):
        writes = [PlannedWrite(rel="a.json", payload={"v": 1})]
        manifest = build_manifest(tmp_path, writes, NOW)
        approval = {
            "approver": "user",
            "scope": "init",
            "decision": "approved",
            "approved_manifest_fingerprint": manifest.fingerprint(),
        }
        apply_manifest(tmp_path, manifest, approval)
        # 第二次 apply：指纹已变化（文件存在），必须拒绝而非重复写
        with pytest.raises(ManifestError):
            apply_manifest(tmp_path, manifest, approval)

    def test_dry_run_is_default_safe(self, tmp_path: Path):
        # 只构建清单从不落盘
        for i in range(3):
            writes = [PlannedWrite(rel=f"f{i}.json", payload={"i": i})]
            build_manifest(tmp_path, writes, NOW)
        assert list(tmp_path.iterdir()) == []


class TestFixtureByteStability:
    def test_fixture_unchanged_after_all_operations(self, tmp_path: Path):
        # 中性 fixture：用户文件
        user_file = tmp_path / "user-notes 中文.md"
        original = "# 用户手写笔记\n内容不变。\n"
        user_file.write_text(original, encoding="utf-8", newline="\n")

        writes = [
            PlannedWrite(rel=".harness-everythings/x.json", payload={"a": 1}),
            # 尝试写用户文件（独占）-> 应被拒绝
            PlannedWrite(rel="user-notes 中文.md", payload={"hijack": True}, exclusive=True),
        ]
        manifest = build_manifest(tmp_path, writes, NOW)
        approval = {
            "approver": "user",
            "scope": "init",
            "decision": "approved",
            "approved_manifest_fingerprint": manifest.fingerprint(),
        }
        with pytest.raises(ManifestError):
            apply_manifest(tmp_path, manifest, approval)
        assert not (tmp_path / ".harness-everythings" / "x.json").exists()
        assert user_file.read_text(encoding="utf-8") == original


class TestDeterminismUnderFixedClock:
    def test_identical_inputs_same_fingerprints(self, tmp_path: Path, tmp_path_factory):
        other = tmp_path_factory.mktemp("second-工作区")
        for root in (tmp_path, other):
            writes = [PlannedWrite(rel="same.json", payload={"k": "值"})]
            m = build_manifest(root, writes, NOW)
            assert m.workspace_fingerprint == build_manifest(
                tmp_path, writes, NOW
            ).workspace_fingerprint
        # 载荷指纹与位置无关
        from harness_everythings.core.identity import content_fingerprint

        assert content_fingerprint({"k": "值"}) == content_fingerprint({"k": "值"})


class TestPathEscapes:
    def test_manifest_rejects_escape(self, tmp_path: Path):
        writes = [PlannedWrite(rel="../evil.json", payload={"x": 1})]
        with pytest.raises(PathBoundaryError):
            build_manifest(tmp_path, writes, NOW)

    def test_absolute_rejected(self, tmp_path: Path):
        writes = [PlannedWrite(rel="/abs.json", payload={"x": 1})]
        with pytest.raises(PathBoundaryError):
            build_manifest(tmp_path, writes, NOW)

    def test_runtime_failure_rolls_back_prior_write(self, tmp_path: Path, monkeypatch):
        import harness_everythings.storage.manifest as manifest_module

        writes = [
            PlannedWrite("a.json", {"v": 1}),
            PlannedWrite("b.json", {"v": 2}),
        ]
        manifest = build_manifest(tmp_path, writes, NOW)
        approval = {
            "approver": "user",
            "scope": "init",
            "decision": "approved",
            "approved_manifest_fingerprint": manifest.fingerprint(),
        }
        real_write = manifest_module.write_atomic

        def fail_second(root, rel, payload, *, exclusive=False):
            if rel == "b.json":
                raise OSError("simulated I/O failure")
            return real_write(root, rel, payload, exclusive=exclusive)

        monkeypatch.setattr(manifest_module, "write_atomic", fail_second)
        with pytest.raises(ManifestError, match="rolled back"):
            apply_manifest(tmp_path, manifest, approval)
        assert not (tmp_path / "a.json").exists()
        assert not (tmp_path / "b.json").exists()
