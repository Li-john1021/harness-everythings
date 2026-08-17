"""Deterministic, role-scoped context routing with privacy boundaries."""

from __future__ import annotations

from typing import Any, Iterable

from .identity import content_fingerprint
from .schema_registry import validate

ALLOWED_SENSITIVITIES = frozenset({"public", "internal", "licensed"})
_FORBIDDEN_MARKERS = ("history", "evolution", "trace", "prompt", "private", "secret", "credential", "employer", "personal", "api_key", "token")


class ContextRoutingError(ValueError):
    """Invalid routing input or budget."""


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContextRoutingError(f"{field} must be a positive integer")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextRoutingError(f"{field} must be a non-empty string")
    return value


def _source_id(source_ref: str) -> str:
    return f"source:{content_fingerprint({'source_ref': source_ref})[7:23]}"


def _rejection(source: dict[str, Any], reason: str, basis: str) -> dict[str, Any]:
    source_ref = str(source.get("source_ref", ""))
    return {"source_id": _source_id(source_ref), "reason": reason, "basis": basis}


def _role_identity(role: dict[str, Any]) -> tuple[str, str]:
    role_id = _text(role.get("role_id") or role.get("entity_id"), "role.role_id")
    name = role.get("role_name", role_id)
    if not isinstance(name, str) or not name:
        name = role_id
    return role_id, name


def _authorized(source: dict[str, Any], role_id: str, role_name: str, purpose: str) -> bool:
    role_values = source.get("authorized_role_ids", source.get("authorized_roles"))
    if role_values is not None:
        if not isinstance(role_values, list) or not all(isinstance(item, str) and item for item in role_values):
            raise ContextRoutingError("authorized role list must contain strings")
        if role_id in role_values or role_name in role_values:
            return True
    owner = source.get("owner_role_id")
    if owner is not None:
        if not isinstance(owner, str) or not owner:
            raise ContextRoutingError("owner_role_id must be a non-empty string")
        if owner in {role_id, role_name}:
            return True
    purposes = source.get("authorized_purposes")
    if purposes is not None:
        if not isinstance(purposes, list) or not all(isinstance(item, str) and item for item in purposes):
            raise ContextRoutingError("authorized_purposes must contain strings")
        if purpose in purposes:
            return True
    return False


def build_context_routes(
    roles: Iterable[dict[str, Any]],
    sources: Iterable[dict[str, Any]],
    *,
    max_tokens: int = 4000,
    purpose: str | None = None,
) -> dict[str, Any]:
    """Build role-specific route packages without embedding source contents."""
    max_tokens = _positive_int(max_tokens, "max_tokens")
    role_list = sorted(list(roles), key=lambda role: _role_identity(role)[0])
    source_list = sorted(list(sources), key=lambda source: (str(source.get("source_fingerprint", "")), str(source.get("source_ref", ""))))
    all_rejected: dict[str, dict[str, Any]] = {}
    normalized: list[dict[str, Any]] = []
    for source in source_list:
        if not isinstance(source, dict):
            raise ContextRoutingError("source must be an object")
        source_ref = _text(source.get("source_ref"), "source.source_ref")
        fingerprint = _text(source.get("source_fingerprint"), "source.source_fingerprint")
        sensitivity = source.get("sensitivity")
        if sensitivity not in ALLOWED_SENSITIVITIES:
            item = _rejection(source, "sensitive_or_unknown_sensitivity", "privacy-boundary")
            all_rejected[item["source_id"]] = item
            continue
        if any(marker in source_ref.lower() for marker in _FORBIDDEN_MARKERS):
            item = _rejection(source, "forbidden_history_private_or_credential_source", "clean-room-boundary")
            all_rejected[item["source_id"]] = item
            continue
        estimated = _positive_int(source.get("estimated_tokens"), "source.estimated_tokens")
        normalized.append({
            "source_ref": source_ref,
            "source_fingerprint": fingerprint,
            "sensitivity": sensitivity,
            "estimated_tokens": estimated,
            "source": source,
        })

    routes: list[dict[str, Any]] = []
    for role in role_list:
        role_id, role_name = _role_identity(role)
        route_purpose = purpose or f"role:{role_name}"
        selected: list[dict[str, Any]] = []
        route_rejected: dict[str, dict[str, Any]] = {}
        for item in normalized:
            source = item["source"]
            if not _authorized(source, role_id, role_name, route_purpose):
                rejection = _rejection(source, "source_has_no_explicit_role_or_purpose_authorization", "default-deny-routing")
                route_rejected[rejection["source_id"]] = rejection
                all_rejected[rejection["source_id"]] = rejection
                continue
            if sum(entry["estimated_tokens"] for entry in selected) + item["estimated_tokens"] > max_tokens:
                rejection = _rejection(source, "route_token_budget_exceeded", "max_tokens")
                route_rejected[rejection["source_id"]] = rejection
                all_rejected[rejection["source_id"]] = rejection
                continue
            selected.append(item)
        source_refs = [item["source_ref"] for item in selected]
        source_fingerprints = [item["source_fingerprint"] for item in selected]
        estimated_tokens = sum(item["estimated_tokens"] for item in selected)
        route_base = {
            "role_id": role_id,
            "owner": role_id,
            "purpose": route_purpose,
            "source_refs": source_refs,
            "source_fingerprints": source_fingerprints,
            "sensitivity": sorted({item["sensitivity"] for item in selected}),
            "estimated_tokens": estimated_tokens,
            "max_tokens": max_tokens,
            "max_token_budget": max_tokens,
            "invalidation_conditions": ["source_fingerprint_changed", "role_contract_changed", "authorization_changed"],
            "rejected_sources": sorted(route_rejected.values(), key=lambda item: item["source_id"]),
        }
        route = {
            "schema_version": "1.0",
            "route_id": f"context-route:{content_fingerprint(route_base)[7:23]}",
            **route_base,
        }
        validate("context-route", route)
        routes.append(route)
    result_base = {
        "schema_version": "1.0",
        "entity_type": "context-routes",
        "routing_version": "1.0",
        "routes": routes,
        "rejected_sources": sorted(all_rejected.values(), key=lambda item: item["source_id"]),
    }
    result = {**result_base, "routing_fingerprint": content_fingerprint(result_base)}
    validate("context-routes", result)
    return result
