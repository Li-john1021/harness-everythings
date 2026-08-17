"""Workspace profile records and privacy-safe representations."""

from __future__ import annotations

import re
from typing import Any

from .identity import content_fingerprint

PROFILE_STATUSES = frozenset(
    {"observed", "inferred", "user_confirmed", "unresolved", "disproved"}
)
SENSITIVITIES = frozenset(
    {"public", "internal", "personal", "secret", "employer", "licensed"}
)

_SECRET_KEY = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|secret|token|password|passwd|authorization|cookie|private[_-]?key)"
)
_SECRET_VALUE = re.compile(
    r"(?i)\b(?:sk|ghp|glpat|xoxb|token|secret)[_-]?[A-Za-z0-9_.:/+=-]{8,}\b"
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\[^\s\"']+")
_POSIX_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s/]+/){1,}[^\s]+")


def sanitize_text(value: str) -> str:
    """Remove credentials, personal contact data, and absolute paths."""
    value = _SECRET_VALUE.sub("[REDACTED:secret]", value)
    value = _EMAIL.sub("[REDACTED:personal]", value)
    value = _WINDOWS_PATH.sub("[REDACTED:path]", value)
    value = _POSIX_PATH.sub("[REDACTED:path]", value)
    return value


def sanitize_value(value: Any, *, key: str = "") -> Any:
    """Return a JSON-safe value that is suitable for a model-visible summary."""
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED:secret]" if _SECRET_KEY.search(str(k)) else sanitize_value(v, key=str(k))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize_value(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [sanitize_value(item, key=key) for item in value]
    if isinstance(value, str):
        if _SECRET_KEY.search(key):
            return "[REDACTED:secret]"
        return sanitize_text(value)
    return value


def safe_source_ref(source_ref: str, *, sensitive: bool = False) -> str:
    """Keep sensitive source locations referential without exposing their paths."""
    if sensitive:
        return f"source:{content_fingerprint({'source': source_ref})[7:23]}"
    return sanitize_text(source_ref)


def make_profile_record(
    *,
    fact_key: str,
    fact_value: Any,
    status: str,
    sensitivity: str,
    source_ref: str,
    source_fingerprint: str,
    observed_at: str,
    confidence: float | None = None,
    freshness: str | None = None,
    decided_at: str | None = None,
) -> dict[str, Any]:
    """Create a profile item with a stable content fingerprint."""
    if status not in PROFILE_STATUSES:
        raise ValueError(f"unknown profile status: {status!r}")
    if sensitivity not in SENSITIVITIES:
        raise ValueError(f"unknown sensitivity: {sensitivity!r}")
    if not 0 <= (confidence if confidence is not None else 0.0) <= 1:
        raise ValueError("confidence must be between 0 and 1")
    clean_value = sanitize_value(fact_value, key=fact_key)
    record = {
        "schema_version": "1.0",
        "entity_id": f"profile-record:{content_fingerprint({'fact_key': fact_key, 'source': source_fingerprint})[7:23]}",
        "entity_type": "profile-record",
        "created_at": observed_at,
        "updated_at": observed_at,
        "source_ref": safe_source_ref(source_ref, sensitive=sensitivity in {"secret", "personal", "employer"}),
        "status": status,
        "sensitivity": sensitivity,
        "fact_key": fact_key,
        "fact_value": clean_value,
        "confidence": confidence if confidence is not None else 0.0,
        "freshness": freshness or f"current:{source_fingerprint}",
        "source_fingerprint": source_fingerprint,
        "observed_at": observed_at,
    }
    if decided_at is not None:
        record["decided_at"] = decided_at
    record["record_fingerprint"] = content_fingerprint(
        {key: value for key, value in record.items() if key != "record_fingerprint"}
    )
    return record


def safe_summary(value: Any) -> Any:
    """Recursively filter a discovery result before it is displayed or routed."""
    return sanitize_value(value)


def change_profile_status(
    record: dict[str, Any],
    status: str,
    *,
    decided_at: str,
    source_ref: str,
) -> dict[str, Any]:
    """Record a user confirmation or disproof without changing the fact identity."""
    if status not in {"user_confirmed", "disproved"}:
        raise ValueError("status change must be user_confirmed or disproved")
    updated = dict(record)
    updated["status"] = status
    updated["updated_at"] = decided_at
    updated["decided_at"] = decided_at
    updated["source_ref"] = safe_source_ref(source_ref)
    updated["confidence"] = 1.0
    updated["freshness"] = f"current:{record.get('source_fingerprint', 'unknown')}"
    updated["record_fingerprint"] = content_fingerprint(
        {key: value for key, value in updated.items() if key != "record_fingerprint"}
    )
    return updated
