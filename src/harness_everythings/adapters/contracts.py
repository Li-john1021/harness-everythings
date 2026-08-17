"""Vendor-neutral runtime and workspace adapter contracts."""

from __future__ import annotations

from typing import Any

from .capabilities import CAPABILITY_KEYS, CapabilitySet, DEGRADATION_PATHS
from ..core.identity import content_fingerprint
from ..core.schema_registry import validate


def _adapter_base(capabilities: CapabilitySet) -> dict[str, Any]:
    missing = sorted(CAPABILITY_KEYS - capabilities.capabilities)
    return {
        "schema_version": "1.0",
        "entity_type": "adapter-contract",
        "adapter_id": capabilities.adapter_id,
        "adapter_kind": capabilities.adapter_kind,
        "version": capabilities.version,
        "available": sorted(capabilities.capabilities),
        "missing": missing,
        "degradation": {key: DEGRADATION_PATHS[key] for key in missing},
        "state_contract": {
            "canonical_states": "unchanged",
            "evidence_required": True,
            "approval_boundary": "unchanged",
        },
        "execution": "parallel" if "parallel_execution" in capabilities.capabilities else "serial",
    }


def adapter_contract(capabilities: CapabilitySet) -> dict[str, Any]:
    """Return an honest capability report with a stable fallback per omission."""
    record = _adapter_base(capabilities)
    validate("adapter-contract", record)
    return record


def workspace_adapter_contract() -> dict[str, Any]:
    """Declare discovery, write, optional Git, and workspace-shape boundaries."""
    record = {
        "schema_version": "1.0",
        "entity_type": "adapter-contract",
        "adapter_id": "workspace-read-only",
        "adapter_kind": "workspace",
        "version": "1.0",
        "available": ["filesystem_metadata", "bounded_file_read", "git_read_only_probe"],
        "missing": ["project_execution", "workspace_write"],
        "degradation": {"project_execution": "manual", "workspace_write": "approval-bound-manifest"},
        "state_contract": {
            "canonical_states": "unchanged",
            "evidence_required": True,
            "approval_boundary": "ApplicationManifest + user approval",
        },
        "execution": "serial",
        "operations": ["filesystem_metadata", "bounded_file_read", "git_read_only_probe"],
        "executes_project_scripts": False,
        "network": False,
        "credentials": False,
        "discovery": {
            "read_only": True,
            "operations": ["filesystem_metadata", "bounded_file_read", "git_read_only_probe"],
            "executes_project_scripts": False,
        },
        "write": {"available": False, "requires_application_manifest": True, "requires_user_approval": True},
        "git": {"optional": True, "read_only": True, "supports_dirty": True, "supports_nested": True},
        "workspace_kinds": {
            "ordinary_directory": True,
            "non_git": True,
            "dirty_git": True,
            "nested_repository": True,
            "monorepo": True,
            "asset_collection": True,
        },
    }
    validate("adapter-contract", record)
    return record


def workspace_discovery_contract() -> dict[str, Any]:
    """Backward-compatible name for the complete workspace adapter contract."""
    return workspace_adapter_contract()


def adapter_state_contract(
    runtime: CapabilitySet | dict[str, Any],
    workspace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine runtime and workspace reports without changing governance state."""
    runtime_record = runtime if isinstance(runtime, dict) else adapter_contract(runtime)
    workspace_record = workspace or workspace_adapter_contract()
    record = {
        "schema_version": "1.0",
        "entity_type": "adapter-state",
        "state_version": "1.0",
        "runtime": runtime_record,
        "workspace": workspace_record,
        "state_fingerprint": content_fingerprint({"runtime": runtime_record, "workspace": workspace_record}),
        "evidence_refs": [f"adapter:{runtime_record['adapter_id']}", f"adapter:{workspace_record['adapter_id']}"],
    }
    validate("adapter-state", record)
    return record
