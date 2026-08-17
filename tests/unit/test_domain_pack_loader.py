"""领域包物理边界、扩展加载和版本/指纹拒绝。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from harness_everythings.core.domain_pack_loader import (
    DomainPackLoadError,
    execute_domain_pack_fixture,
    load_builtin_domain_pack,
    load_domain_pack_directory,
)
from harness_everythings.core.identity import content_fingerprint


def _resign(pack: dict) -> None:
    pack["fingerprint"] = content_fingerprint({key: value for key, value in pack.items() if key != "fingerprint"})


def _copy_pack(tmp_path: Path, pack_id: str = "content-script", target_id: str = "test-pack") -> Path:
    source_dir = Path(__file__).resolve().parents[2] / "src" / "harness_everythings" / "domain_packs" / pack_id
    pack_dir = tmp_path / target_id
    shutil.copytree(source_dir, pack_dir)
    pack = json.loads((pack_dir / "pack.json").read_text(encoding="utf-8"))
    pack["pack_id"] = target_id
    for declaration in pack["fixtures"]:
        fixture_path = pack_dir / declaration["path"]
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixture["pack_id"] = target_id
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
        declaration["fingerprint"] = content_fingerprint(fixture)
    _resign(pack)
    (pack_dir / "pack.json").write_text(json.dumps(pack), encoding="utf-8")
    return pack_dir


def test_neutral_pack_extension_loads_without_kernel_pack_switch(tmp_path: Path):
    source = load_builtin_domain_pack("content-script")
    pack_dir = _copy_pack(tmp_path, target_id="neutral-review")
    loaded = load_domain_pack_directory(pack_dir)
    assert loaded["pack_id"] == "neutral-review"
    assert loaded["role_templates"] == source["role_templates"]


def test_pack_extension_rejects_path_escape_and_incompatible_version(tmp_path: Path):
    with pytest.raises(DomainPackLoadError):
        load_builtin_domain_pack("../content-script")
    source = load_builtin_domain_pack("content-script")
    incompatible = dict(source, pack_id="incompatible", kernel_min_version="9.0")
    incompatible["fingerprint"] = content_fingerprint({key: value for key, value in incompatible.items() if key != "fingerprint"})
    pack_dir = tmp_path / "incompatible"
    pack_dir.mkdir()
    (pack_dir / "pack.json").write_text(json.dumps(incompatible), encoding="utf-8")
    with pytest.raises(DomainPackLoadError):
        load_domain_pack_directory(pack_dir)


def test_pack_extension_rejects_invalid_output_schema_type(tmp_path: Path):
    source_dir = Path(__file__).resolve().parents[2] / "src" / "harness_everythings" / "domain_packs" / "content-script"
    pack_dir = tmp_path / "invalid-schema"
    shutil.copytree(source_dir, pack_dir)
    pack = json.loads((pack_dir / "pack.json").read_text(encoding="utf-8"))
    schema = json.loads((pack_dir / "output-contract.schema.json").read_text(encoding="utf-8"))
    schema["properties"]["variant_policy"]["type"] = "not-a-json-schema-type"
    (pack_dir / "output-contract.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    pack["pack_id"] = "invalid-schema"
    pack["fingerprint"] = content_fingerprint({key: value for key, value in pack.items() if key != "fingerprint"})
    (pack_dir / "pack.json").write_text(json.dumps(pack), encoding="utf-8")
    with pytest.raises(DomainPackLoadError):
        load_domain_pack_directory(pack_dir)


def test_pack_rejects_unresolved_output_schema_ref(tmp_path: Path):
    pack_dir = _copy_pack(tmp_path, target_id="missing-ref")
    schema_path = pack_dir / "output-contract.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["variant_policy"] = {"$ref": "missing.schema.json"}
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    with pytest.raises(DomainPackLoadError, match="unresolved schema reference"):
        load_domain_pack_directory(pack_dir)


@pytest.mark.parametrize("pack_id", ["software-engineering", "content-script"])
def test_builtin_pack_has_bound_closure_and_recovery_fixtures(pack_id: str):
    loaded = load_builtin_domain_pack(pack_id)
    assert {item["scenario_type"] for item in loaded["fixtures"]} == {
        "normal-closure",
        "controlled-failure-recovery",
    }
    assert loaded["supported_workspace_types"]
    assert loaded["supported_artifact_types"]
    assert set(loaded["runtime_capabilities"]) == {"required", "optional"}
    assert loaded["compatibility"]["migration_strategy"]
    assert loaded["deprecation"]["policy"]


@pytest.mark.parametrize(
    "missing_field",
    [
        "supported_workspace_types",
        "supported_artifact_types",
        "runtime_capabilities",
        "external_tools",
        "models",
        "network",
        "credentials",
        "publishing",
        "compatibility",
        "deprecation",
        "fixtures",
    ],
)
def test_pack_rejects_missing_contract_declaration(tmp_path: Path, missing_field: str):
    pack_dir = _copy_pack(tmp_path)
    manifest_path = pack_dir / "pack.json"
    pack = json.loads(manifest_path.read_text(encoding="utf-8"))
    del pack[missing_field]
    _resign(pack)
    manifest_path.write_text(json.dumps(pack), encoding="utf-8")
    with pytest.raises(DomainPackLoadError):
        load_domain_pack_directory(pack_dir)


@pytest.mark.parametrize("readme_state", ["missing", "empty"])
def test_pack_rejects_missing_or_empty_readme(tmp_path: Path, readme_state: str):
    pack_dir = _copy_pack(tmp_path)
    readme = pack_dir / "README.md"
    if readme_state == "missing":
        readme.unlink()
    else:
        readme.write_text("", encoding="utf-8")
    with pytest.raises(DomainPackLoadError):
        load_domain_pack_directory(pack_dir)


def test_pack_rejects_corrupt_fixture_json(tmp_path: Path):
    pack_dir = _copy_pack(tmp_path)
    (pack_dir / "fixtures" / "normal-closure.json").write_text("{", encoding="utf-8")
    with pytest.raises(DomainPackLoadError):
        load_domain_pack_directory(pack_dir)


def test_pack_rejects_fixture_not_bound_to_manifest(tmp_path: Path):
    pack_dir = _copy_pack(tmp_path)
    fixture_path = pack_dir / "fixtures" / "controlled-recovery.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture["pack_id"] = "another-pack"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(DomainPackLoadError, match="different pack|fingerprint"):
        load_domain_pack_directory(pack_dir)


def test_pack_rejects_fixture_without_controlled_recovery(tmp_path: Path):
    pack_dir = _copy_pack(tmp_path)
    manifest_path = pack_dir / "pack.json"
    pack = json.loads(manifest_path.read_text(encoding="utf-8"))
    declaration = pack["fixtures"][1]
    declaration["scenario_type"] = "normal-closure"
    fixture_path = pack_dir / declaration["path"]
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture["scenario_type"] = "normal-closure"
    fixture["expected_outcome"] = "completed"
    fixture["failure"] = {"injected_stage_id": "", "reason": "", "recovery_stage_id": ""}
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    declaration["fingerprint"] = content_fingerprint(fixture)
    _resign(pack)
    manifest_path.write_text(json.dumps(pack), encoding="utf-8")
    with pytest.raises(DomainPackLoadError, match="requires normal closure"):
        load_domain_pack_directory(pack_dir)


def test_pack_rejects_undeclared_fixture(tmp_path: Path):
    pack_dir = _copy_pack(tmp_path)
    (pack_dir / "fixtures" / "extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DomainPackLoadError, match="does not match manifest"):
        load_domain_pack_directory(pack_dir)


@pytest.mark.parametrize("pack_id", ["software-engineering", "content-script"])
def test_controlled_recovery_fixture_executes_failure_and_resume(pack_id: str):
    pack = load_builtin_domain_pack(pack_id)
    fixture_path = Path(__file__).resolve().parents[2] / "src" / "harness_everythings" / "domain_packs" / pack_id / "fixtures" / "controlled-recovery.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    result = execute_domain_pack_fixture(pack, fixture)
    assert result["outcome"] == "recovered"
    assert any(item["state"] == "failed" for item in result["events"])
    assert any(item["state"] == "not_run" for item in result["events"])
    assert result["events"][-1]["state"] == "completed"
    assert result["events"][-1]["attempt"] == 2
