"""通用、版本化的领域包资源加载器。

领域包是资源，不是内核分支。加载器只负责边界、版本、Schema、引用和
指纹校验；领域行为由上层合同消费已验证的 manifest。
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .identity import content_fingerprint
from .schema_registry import validate


class DomainPackLoadError(ValueError):
    """领域包资源不存在、越界、版本不兼容或合同无效。"""


_PACK_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_KERNEL_VERSION = "1.0"
_SCHEMA_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})


def _validate_output_schema(value: Any, path: str, root: Path, seen_refs: frozenset[str] = frozenset()) -> None:
    """Apply the pack boundary's small, deterministic JSON-Schema meta contract."""
    if not isinstance(value, dict):
        raise DomainPackLoadError(f"{path} must be a schema object")
    if "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref or "/" in ref or "\\" in ref or not ref.endswith(".schema.json"):
            raise DomainPackLoadError(f"unsafe schema reference in {path}")
        target = root / ref
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise DomainPackLoadError(f"schema reference escapes pack boundary in {path}") from exc
        if not target.is_file() or target.is_symlink():
            raise DomainPackLoadError(f"unresolved schema reference in {path}: {ref}")
        if ref not in seen_refs:
            try:
                referenced = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise DomainPackLoadError(f"invalid referenced schema in {path}: {ref}") from exc
            _validate_output_schema(referenced, ref, root, seen_refs | {ref})
    if "type" in value:
        schema_type = value["type"]
        if not isinstance(schema_type, str) or schema_type not in _SCHEMA_TYPES:
            raise DomainPackLoadError(f"invalid schema type in {path}")
    if "required" in value:
        required = value["required"]
        if not isinstance(required, list) or any(not isinstance(item, str) or not item for item in required):
            raise DomainPackLoadError(f"invalid required list in {path}")
    if "properties" in value:
        properties = value["properties"]
        if not isinstance(properties, dict):
            raise DomainPackLoadError(f"invalid properties map in {path}")
        for name, child in sorted(properties.items()):
            if not isinstance(name, str) or not name:
                raise DomainPackLoadError(f"invalid property name in {path}")
            _validate_output_schema(child, f"{path}.properties.{name}", root, seen_refs)
    if "items" in value:
        _validate_output_schema(value["items"], f"{path}.items", root, seen_refs)
    if "additionalProperties" in value and not isinstance(value["additionalProperties"], (bool, dict)):
        raise DomainPackLoadError(f"invalid additionalProperties in {path}")
    if isinstance(value.get("additionalProperties"), dict):
        _validate_output_schema(value["additionalProperties"], f"{path}.additionalProperties", root, seen_refs)


def _read_manifest(directory: Path) -> dict[str, Any]:
    if directory.is_symlink():
        raise DomainPackLoadError("domain pack directory must not be a symlink")
    root = directory.resolve()
    if not root.is_dir():
        raise DomainPackLoadError(f"domain pack directory is missing: {directory}")
    manifest_path = root / "pack.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise DomainPackLoadError("domain pack must contain a regular pack.json")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainPackLoadError(f"invalid domain pack JSON: {manifest_path}") from exc
    if not isinstance(value, dict):
        raise DomainPackLoadError("domain pack manifest must be an object")
    return value


def _read_resource(root: Path, relative: str) -> Any:
    resource = root / relative
    if not resource.is_file() or resource.is_symlink():
        raise DomainPackLoadError(f"required domain pack resource is missing or unsafe: {relative}")
    try:
        return json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainPackLoadError(f"invalid domain pack resource: {relative}") from exc


def _validate_string_list(value: Any, name: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise DomainPackLoadError(f"{name} must be a non-empty list" if not allow_empty else f"{name} must be a list")
    if any(not isinstance(item, str) or not item for item in value):
        raise DomainPackLoadError(f"{name} must contain non-empty strings")
    if len(set(value)) != len(value):
        raise DomainPackLoadError(f"{name} must not contain duplicates")
    return value


def _validate_fixture(root: Path, pack: dict[str, Any], declaration: dict[str, Any]) -> str:
    relative = declaration["path"]
    expected_relative = f"fixtures/{Path(relative).name}"
    if relative != expected_relative or not relative.endswith(".json"):
        raise DomainPackLoadError(f"unsafe fixture path: {relative}")
    fixture = _read_resource(root, relative)
    if not isinstance(fixture, dict):
        raise DomainPackLoadError(f"fixture must be an object: {relative}")
    required = {
        "schema_version",
        "entity_type",
        "fixture_id",
        "pack_id",
        "pack_version",
        "scenario_type",
        "workspace_type",
        "artifact_types",
        "stage_sequence",
        "validator_ids",
        "expected_outcome",
        "failure",
    }
    if set(fixture) != required:
        missing = sorted(required - set(fixture))
        extra = sorted(set(fixture) - required)
        raise DomainPackLoadError(f"invalid fixture fields in {relative}: missing={missing}, extra={extra}")
    if fixture["schema_version"] != "1.0" or fixture["entity_type"] != "domain-pack-fixture":
        raise DomainPackLoadError(f"unsupported fixture schema or type: {relative}")
    for field in ("fixture_id", "pack_id", "pack_version", "workspace_type", "expected_outcome"):
        if not isinstance(fixture[field], str) or not fixture[field]:
            raise DomainPackLoadError(f"fixture field {field} must be a non-empty string: {relative}")
    if fixture["fixture_id"] != declaration["fixture_id"]:
        raise DomainPackLoadError(f"fixture ID does not match manifest: {relative}")
    if fixture["scenario_type"] != declaration["scenario_type"]:
        raise DomainPackLoadError(f"fixture scenario does not match manifest: {relative}")
    if fixture["pack_id"] != pack["pack_id"] or fixture["pack_version"] != pack["pack_version"]:
        raise DomainPackLoadError(f"fixture is bound to a different pack: {relative}")
    if fixture["workspace_type"] not in pack["supported_workspace_types"]:
        raise DomainPackLoadError(f"fixture uses an unsupported workspace type: {relative}")
    artifact_types = _validate_string_list(fixture["artifact_types"], f"{relative}.artifact_types", allow_empty=False)
    if not set(artifact_types).issubset(pack["supported_artifact_types"]):
        raise DomainPackLoadError(f"fixture uses an unsupported artifact type: {relative}")
    stages = _validate_string_list(fixture["stage_sequence"], f"{relative}.stage_sequence", allow_empty=False)
    stage_ids = [stage["stage_id"] for stage in pack["stages"]]
    if any(stage not in stage_ids for stage in stages):
        raise DomainPackLoadError(f"fixture references an unknown stage: {relative}")
    validators = _validate_string_list(fixture["validator_ids"], f"{relative}.validator_ids", allow_empty=False)
    validator_ids = [item["validator_id"] for item in pack["validators"]]
    if set(validators) != set(validator_ids):
        raise DomainPackLoadError(f"fixture must exercise every declared validator: {relative}")
    failure = fixture["failure"]
    failure_fields = {"injected_stage_id", "reason", "recovery_stage_id"}
    if not isinstance(failure, dict) or set(failure) != failure_fields:
        raise DomainPackLoadError(f"fixture failure declaration is invalid: {relative}")
    if any(not isinstance(failure[field], str) for field in failure_fields):
        raise DomainPackLoadError(f"fixture failure fields must be strings: {relative}")
    scenario = fixture["scenario_type"]
    if scenario == "normal-closure":
        if stages != stage_ids or fixture["expected_outcome"] != "completed" or any(failure.values()):
            raise DomainPackLoadError(f"normal closure fixture is incomplete: {relative}")
    elif scenario == "controlled-failure-recovery":
        if fixture["expected_outcome"] != "recovered" or any(not failure[field] for field in failure_fields):
            raise DomainPackLoadError(f"controlled recovery fixture is incomplete: {relative}")
        if failure["injected_stage_id"] not in stages or failure["recovery_stage_id"] not in stages:
            raise DomainPackLoadError(f"controlled recovery fixture references an unknown stage: {relative}")
    else:
        raise DomainPackLoadError(f"unsupported fixture scenario: {relative}")
    if content_fingerprint(fixture) != declaration["fingerprint"]:
        raise DomainPackLoadError(f"fixture fingerprint mismatch: {relative}")
    return scenario


def _validate_resources(root: Path, pack: dict[str, Any]) -> None:
    """Require and cross-check the physical resources frozen by the pack contract."""
    required_dirs = ("roles", "validators", "fixtures")
    for name in required_dirs:
        directory = root / name
        if not directory.is_dir() or directory.is_symlink():
            raise DomainPackLoadError(f"required domain pack directory is missing or unsafe: {name}")
    readme = root / "README.md"
    if not readme.is_file() or readme.is_symlink():
        raise DomainPackLoadError("domain pack must contain a regular README.md")
    try:
        if not readme.read_text(encoding="utf-8").strip():
            raise DomainPackLoadError("domain pack README.md must not be empty")
    except OSError as exc:
        raise DomainPackLoadError("domain pack README.md is unreadable") from exc
    output_schema = _read_resource(root, "output-contract.schema.json")
    _validate_output_schema(output_schema, "output-contract.schema.json", root)
    if output_schema.get("type") != "object":
        raise DomainPackLoadError("output-contract.schema.json must be an object schema")
    if _read_resource(root, "stages.json") != pack["stages"]:
        raise DomainPackLoadError("stages.json does not match pack manifest")
    roles = _read_resource(root, "roles/roles.json")
    if not isinstance(roles, dict) or roles.get("role_templates") != pack["role_templates"]:
        raise DomainPackLoadError("roles/roles.json does not match pack manifest")
    validators = _read_resource(root, "validators/validators.json")
    if not isinstance(validators, dict) or validators.get("validators") != pack["validators"]:
        raise DomainPackLoadError("validators/validators.json does not match pack manifest")
    if _read_resource(root, "context-routes.json") != pack["context_routes"]:
        raise DomainPackLoadError("context-routes.json does not match pack manifest")
    declared = pack["fixtures"]
    fixture_ids = [item["fixture_id"] for item in declared]
    fixture_paths = [item["path"] for item in declared]
    if len(set(fixture_ids)) != len(fixture_ids) or len(set(fixture_paths)) != len(fixture_paths):
        raise DomainPackLoadError("domain pack fixture IDs and paths must be unique")
    actual_paths = sorted(
        f"fixtures/{item.name}"
        for item in (root / "fixtures").iterdir()
        if item.is_file() and not item.is_symlink()
    )
    if sorted(fixture_paths) != actual_paths:
        raise DomainPackLoadError("fixture directory does not match manifest declarations")
    scenarios = {_validate_fixture(root, pack, item) for item in declared}
    required_scenarios = {"normal-closure", "controlled-failure-recovery"}
    if not required_scenarios.issubset(scenarios):
        raise DomainPackLoadError("domain pack requires normal closure and controlled failure recovery fixtures")


def validate_loaded_domain_pack(pack: dict[str, Any], *, expected_pack_id: str | None = None) -> None:
    """Validate the frozen generic domain-pack contract."""
    try:
        validate("domain-pack-manifest", pack)
    except Exception as exc:
        raise DomainPackLoadError(str(exc)) from exc
    pack_id = pack["pack_id"]
    if not isinstance(pack_id, str) or not _PACK_ID.fullmatch(pack_id):
        raise DomainPackLoadError("pack_id is not a safe canonical identifier")
    if expected_pack_id is not None and pack_id != expected_pack_id:
        raise DomainPackLoadError("pack directory name does not match pack_id")
    if pack["kernel_min_version"] != _KERNEL_VERSION:
        raise DomainPackLoadError("domain pack requires an incompatible kernel version")
    base = {key: value for key, value in pack.items() if key != "fingerprint"}
    if pack["fingerprint"] != content_fingerprint(base):
        raise DomainPackLoadError("domain pack fingerprint mismatch")
    roles = pack["role_templates"]
    role_ids = [role["role_id"] for role in roles]
    if len(set(role_ids)) != len(role_ids):
        raise DomainPackLoadError("domain pack role IDs must be unique")
    stage_ids = [stage["stage_id"] for stage in pack["stages"]]
    if len(set(stage_ids)) != len(stage_ids):
        raise DomainPackLoadError("domain pack stage IDs must be unique")
    validator_ids = [item["validator_id"] for item in pack["validators"]]
    if len(set(validator_ids)) != len(validator_ids):
        raise DomainPackLoadError("domain pack validator IDs must be unique")
    for route in pack["context_routes"]:
        owner = route["owner_role_id"]
        if owner != "domain-pack" and owner not in role_ids:
            raise DomainPackLoadError(f"context route owner is not a role: {owner}")
    for field in ("supported_workspace_types", "supported_artifact_types"):
        _validate_string_list(pack[field], field, allow_empty=False)
    required_caps = _validate_string_list(pack["runtime_capabilities"]["required"], "runtime_capabilities.required")
    optional_caps = _validate_string_list(pack["runtime_capabilities"]["optional"], "runtime_capabilities.optional")
    if set(required_caps) & set(optional_caps):
        raise DomainPackLoadError("runtime capabilities cannot be both required and optional")
    compatibility = pack["compatibility"]
    for field in ("supported_kernel_versions", "supported_pack_versions", "migration_from_pack_versions"):
        _validate_string_list(compatibility[field], f"compatibility.{field}", allow_empty=field == "migration_from_pack_versions")
    if pack["kernel_min_version"] not in compatibility["supported_kernel_versions"]:
        raise DomainPackLoadError("compatibility must include kernel_min_version")
    if pack["pack_version"] not in compatibility["supported_pack_versions"]:
        raise DomainPackLoadError("compatibility must include pack_version")
    for field in ("external_tools", "models"):
        _validate_string_list(pack[field], field)
    if pack["network"]["allowed"] is False and pack["network"]["destinations"]:
        raise DomainPackLoadError("network destinations require network access")
    if pack["credentials"]["required"] is False and pack["credentials"]["kinds"]:
        raise DomainPackLoadError("credential kinds require credentials")
    if pack["publishing"] != pack["publication"]:
        raise DomainPackLoadError("publishing declarations conflict")
    deprecation = pack["deprecation"]
    if deprecation["status"] == "active" and any(
        deprecation[field] for field in ("deprecated_since", "removal_version", "replacement_pack_id")
    ):
        raise DomainPackLoadError("active domain pack must not declare deprecation milestones")
    if deprecation["status"] == "deprecated" and not deprecation["deprecated_since"]:
        raise DomainPackLoadError("deprecated domain pack must declare deprecated_since")


def load_domain_pack_directory(directory: Path) -> dict[str, Any]:
    """Load a pack from an explicitly bounded directory for extension testing."""
    pack = _read_manifest(directory)
    validate_loaded_domain_pack(pack, expected_pack_id=directory.name)
    _validate_resources(directory.resolve(), pack)
    return deepcopy(pack)


def execute_domain_pack_fixture(pack: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    """Execute a bounded normal or controlled-recovery stage trace."""
    try:
        validate_loaded_domain_pack(pack, expected_pack_id=pack.get("pack_id"))
    except Exception as exc:
        raise DomainPackLoadError(f"cannot execute fixture for invalid pack: {exc}") from exc
    if not isinstance(fixture, dict):
        raise DomainPackLoadError("fixture execution requires an object")
    stage_ids = [item["stage_id"] for item in pack["stages"]]
    stages = fixture.get("stage_sequence")
    if stages != stage_ids:
        raise DomainPackLoadError("fixture execution stage sequence does not match pack")
    scenario = fixture.get("scenario_type")
    if scenario == "normal-closure":
        if fixture.get("expected_outcome") != "completed":
            raise DomainPackLoadError("normal fixture outcome is not completed")
        return {
            "fixture_id": fixture.get("fixture_id"),
            "scenario_type": scenario,
            "events": [{"stage_id": stage, "state": "completed", "attempt": 1} for stage in stages],
            "outcome": "completed",
        }
    if scenario != "controlled-failure-recovery":
        raise DomainPackLoadError("fixture execution scenario is unsupported")
    if fixture.get("expected_outcome") != "recovered":
        raise DomainPackLoadError("controlled recovery fixture outcome is not recovered")
    failure = fixture.get("failure")
    if not isinstance(failure, dict):
        raise DomainPackLoadError("fixture execution failure declaration is invalid")
    injected = failure.get("injected_stage_id")
    recovery = failure.get("recovery_stage_id")
    if injected not in stages or recovery not in stages:
        raise DomainPackLoadError("fixture execution references an unknown stage")
    failure_index = stages.index(injected)
    recovery_index = stages.index(recovery)
    if recovery_index > failure_index:
        raise DomainPackLoadError("fixture recovery must resume at or before the failed stage")
    events = [
        *({"stage_id": stage, "state": "completed", "attempt": 1} for stage in stages[:failure_index]),
        {"stage_id": injected, "state": "failed", "attempt": 1, "reason": failure.get("reason", "")},
        *({"stage_id": stage, "state": "not_run", "attempt": 1} for stage in stages[failure_index + 1:]),
        *({"stage_id": stage, "state": "completed", "attempt": 2} for stage in stages[recovery_index:]),
    ]
    return {
        "fixture_id": fixture.get("fixture_id"),
        "scenario_type": scenario,
        "events": events,
        "failure_stage_id": injected,
        "recovery_stage_id": recovery,
        "outcome": "recovered",
    }


def load_builtin_domain_pack(pack_id: str) -> dict[str, Any]:
    if not isinstance(pack_id, str) or not _PACK_ID.fullmatch(pack_id):
        raise DomainPackLoadError("invalid domain pack identifier")
    resource_root = Path(__file__).resolve().parent.parent / "domain_packs"
    pack_dir = resource_root / pack_id
    try:
        pack_dir.relative_to(resource_root)
    except ValueError as exc:
        raise DomainPackLoadError("domain pack path escaped resource boundary") from exc
    if not pack_dir.is_dir() or pack_dir.is_symlink():
        raise DomainPackLoadError(f"unknown domain pack: {pack_id!r}")
    return load_domain_pack_directory(pack_dir)


__all__ = ["DomainPackLoadError", "execute_domain_pack_fixture", "load_builtin_domain_pack", "load_domain_pack_directory", "validate_loaded_domain_pack"]
