"""Archive member validation without extraction or command execution."""

from __future__ import annotations

from typing import Iterable

from .paths import PathBoundaryError, normalize_rel_path


def validate_archive_members(members: Iterable[dict[str, object]]) -> list[str]:
    """Return canonical regular-file names; reject traversal, absolute, and link entries."""
    accepted: set[str] = set()
    for member in members:
        if not isinstance(member, dict):
            raise PathBoundaryError("archive member must be an object")
        name = member.get("name")
        if not isinstance(name, str) or not name:
            raise PathBoundaryError("archive member name is required")
        if member.get("is_symlink") is True or member.get("is_hardlink") is True:
            raise PathBoundaryError("archive links are not allowed")
        accepted.add(normalize_rel_path(name))
    return sorted(accepted)


__all__ = ["validate_archive_members"]
