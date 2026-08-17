"""Read-only workspace discovery for H2.

This module never runs a project command. Git inspection uses only fixed,
read-only subcommands and is optional; all other signals come from filesystem
metadata and bounded file reads.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .identity import bytes_fingerprint, content_fingerprint
from .profile import make_profile_record, safe_summary, sanitize_text
from ..storage.paths import resolve_in_root

_SKIP_DIRS = frozenset({".git", ".harness-everythings", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"})
_INSTRUCTION_NAMES = frozenset({"AGENTS.md", "CLAUDE.md", "README.md", "SPEC.md", "PLAN.md", "TASK-PROMPT.md", "CONTRIBUTING.md"})
_SECRET_NAMES = re.compile(r"(?i)(^\.env(?:\..*)?$|\.pem$|\.key$|id_rsa|credentials?|secrets?)")
_DANGEROUS_TEXT = re.compile(r"(?i)(ignore\s+(?:all\s+)?previous|rm\s+-rf|remove-item|invoke-webrequest|curl\s+https?://|powershell\s+-enc|upload|exfiltrat|print\s+(?:the\s+)?api[_-]?key)")
_SOFTWARE_MARKERS = frozenset({"pyproject.toml", "package.json", "cargo.toml", "go.mod", "pom.xml", "build.gradle", "setup.py", "pytest.ini", "tox.ini", "Makefile"})
_CONTENT_EXTENSIONS = frozenset({".txt", ".rst", ".srt", ".vtt", ".fountain"})
_VERIFY_NAMES = frozenset({"pytest.ini", "tox.ini", "mypy.ini", "ruff.toml", "tsconfig.json", "jest.config.js", "vitest.config.ts", ".github"})


@dataclass(frozen=True)
class WorkspaceDiscovery:
    root: Path
    workspace_name: str
    workspace_kind: str
    source_fingerprint: str
    source_paths: tuple[str, ...]
    profile_records: tuple[dict[str, Any], ...]
    unresolved: tuple[dict[str, Any], ...]
    authority_map: dict[str, Any]
    summary: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return {
            "workspace_name": self.workspace_name,
            "workspace_kind": self.workspace_kind,
            "source_fingerprint": self.source_fingerprint,
            "source_paths": list(self.source_paths),
            "profile_records": list(self.profile_records),
            "unresolved": list(self.unresolved),
            "authority_map": self.authority_map,
            "summary": safe_summary(self.summary),
        }


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _file_fingerprint(path: Path) -> str:
    try:
        return bytes_fingerprint(path.read_bytes())
    except OSError:
        return content_fingerprint({"unreadable": True, "name": path.name})


def _walk_files(root: Path) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    escaped: list[str] = []
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            name for name in dirs
            if name not in _SKIP_DIRS or (name == ".git" and current_path != root)
        ]
        for name in sorted(dirs):
            candidate = current_path / name
            if name == ".git" and current_path != root:
                files.append(candidate / "HEAD") if (candidate / "HEAD").is_file() else None
                dirs.remove(name)
                continue
            if candidate.is_symlink():
                try:
                    resolve_in_root(root, _relative(root, candidate))
                except Exception:
                    escaped.append(_relative(root, candidate))
        for name in sorted(names):
            candidate = current_path / name
            rel = _relative(root, candidate)
            if candidate.is_symlink():
                try:
                    resolve_in_root(root, rel)
                except Exception:
                    escaped.append(rel)
                    continue
            files.append(candidate)
    return files, escaped


def _read_bounded(path: Path, limit: int = 65536) -> str:
    try:
        return path.read_bytes()[:limit].decode("utf-8", errors="replace")
    except OSError:
        return ""


def _git_info(root: Path) -> dict[str, Any]:
    git_marker = (root / ".git").exists()
    result: dict[str, Any] = {"is_git": git_marker, "available": False, "dirty": None}
    if not git_marker:
        return result
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            return result
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        result.update({"available": True, "dirty": bool(status.stdout.strip()), "status_count": len(status.stdout.splitlines())})
    except (OSError, subprocess.SubprocessError):
        result["error"] = "git_inspection_unavailable"
    return result


def _instruction_entries(root: Path, files: Iterable[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    for path in files:
        if path.name not in _INSTRUCTION_NAMES:
            continue
        rel = _relative(root, path)
        text = _read_bounded(path)
        dangerous = bool(_DANGEROUS_TEXT.search(text))
        entry = {
            "source_ref": f"file:{rel}",
            "source_fingerprint": _file_fingerprint(path),
            "authority": "candidate",
            "trusted": False,
            "dangerous_signal": dangerous,
        }
        entries.append(entry)
        if dangerous:
            risks.append({"kind": "untrusted_instruction", "source_ref": f"file:{rel}", "reason": "instruction contains command, credential, or prompt-injection signal"})
    if len(entries) > 1 and any(item["dangerous_signal"] for item in entries):
        risks.append({"kind": "instruction_conflict", "reason": "multiple candidate instruction sources require user authority mapping"})
    return entries, risks


def _profile(
    key: str, value: Any, status: str, sensitivity: str, source_ref: str,
    source_fingerprint: str, now: str, confidence: float = 1.0,
) -> dict[str, Any]:
    return make_profile_record(
        fact_key=key, fact_value=value, status=status, sensitivity=sensitivity,
        source_ref=source_ref, source_fingerprint=source_fingerprint,
        observed_at=now, confidence=confidence,
    )


def discover_workspace(root: Path, now: str, *, workspace_kind: str = "existing") -> WorkspaceDiscovery:
    """Discover a workspace without executing project-provided instructions."""
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"workspace is not a directory: {root}")
    files, escaped = _walk_files(root)
    rel_paths = tuple(sorted(_relative(root, path) for path in files))
    source_entries = [(rel, _file_fingerprint(root / rel)) for rel in rel_paths]
    source_fingerprint = content_fingerprint({"files": source_entries, "escaped": sorted(escaped)})
    git = _git_info(root)
    instruction_entries, instruction_risks = _instruction_entries(root, files)
    names = {path.name for path in files}
    suffixes = {path.suffix.lower() for path in files}
    software_markers = sorted(names & _SOFTWARE_MARKERS)
    content_count = sum(
        path.suffix.lower() in _CONTENT_EXTENSIONS
        or any(part.lower() in {"content", "scripts", "editorial", "briefs"} for part in path.parts)
        for path in files
        if path.name not in _INSTRUCTION_NAMES
    )
    verification = sorted(name for name in names if name in _VERIFY_NAMES) + sorted(
        str(path.parent.relative_to(root)).replace("\\", "/") for path in files if path.name == ".github"
    )
    nested = sorted(_relative(root, path.parent.parent) for path in files if path.name == "HEAD" and path.parent.name == ".git" and path.parent.parent != root)
    monorepo = len([name for name in names if name in _SOFTWARE_MARKERS]) > 1 or any("workspace" in name.lower() or "lerna" in name.lower() for name in names)
    secret_files = sorted(_relative(root, path) for path in files if _SECRET_NAMES.search(path.name))
    ownership = {
        "license_signals": sorted(name for name in names if name.lower() in {"license", "license.md", "copying", "notice"}),
        "source_of_ownership": "unresolved",
    }
    domain_candidates: list[str] = []
    if software_markers:
        domain_candidates.append("software-engineering")
    if content_count:
        domain_candidates.append("editorial-social-script")
    if not domain_candidates:
        domain_candidates.append("unknown")
    risks = list(instruction_risks)
    if secret_files:
        risks.append({"kind": "secret_signal", "count": len(secret_files), "locations": [f"source:{content_fingerprint({'path': item})[7:23]}" for item in secret_files]})
    if escaped:
        risks.append({"kind": "symlink_escape", "count": len(escaped)})
    if git.get("dirty"):
        risks.append({"kind": "dirty_workspace"})
    if "unknown" in domain_candidates:
        risks.append({"kind": "unknown_domain"})
    profiles: list[dict[str, Any]] = []
    root_source = content_fingerprint({"root": root.name, "source": source_fingerprint})
    profiles.append(_profile("workspace.kind", workspace_kind, "observed", "internal", "workspace:discovery", root_source, now))
    profiles.append(_profile("workspace.git.is_git", git["is_git"], "observed", "internal", "workspace:discovery", source_fingerprint, now))
    profiles.append(_profile("workspace.git.dirty", git.get("dirty"), "observed", "internal", "workspace:git", source_fingerprint, now))
    profiles.append(_profile("workspace.topology.monorepo", monorepo, "inferred", "internal", "workspace:topology", source_fingerprint, now, 0.75))
    profiles.append(_profile("workspace.topology.nested_repositories", nested, "observed", "internal", "workspace:topology", source_fingerprint, now))
    profiles.append(_profile("workspace.instructions", instruction_entries, "observed", "internal", "workspace:instructions", source_fingerprint, now))
    profiles.append(_profile("workspace.domains.candidates", domain_candidates, "inferred", "internal", "workspace:domain-signals", source_fingerprint, now, 0.7))
    profiles.append(_profile("workspace.verification.signals", verification, "observed", "internal", "workspace:verification", source_fingerprint, now))
    profiles.append(_profile("workspace.ownership", ownership, "unresolved", "licensed", "workspace:ownership", source_fingerprint, now, 0.0))
    profiles.append(_profile("workspace.risks", risks, "observed" if risks else "observed", "internal", "workspace:risk-scan", source_fingerprint, now))
    unresolved: list[dict[str, Any]] = [
        {"key": "workspace.authority", "question": "哪些工作区指令文件可被信任？", "status": "unresolved", "evidence_ref": "workspace:instructions"},
        {"key": "workspace.ownership", "question": "工作区及其产物的所有权、许可证和发布边界是什么？", "status": "unresolved", "evidence_ref": "workspace:ownership"},
        {"key": "workspace.domain", "question": "启用哪个领域包？", "candidates": domain_candidates, "status": "unresolved", "evidence_ref": "workspace:domain-signals"},
    ]
    unresolved.extend({"key": risk["kind"], "question": risk.get("reason", "需要用户处理的工作区风险"), "status": "unresolved", "evidence_ref": "workspace:risk-scan"} for risk in risks if risk["kind"] in {"instruction_conflict", "untrusted_instruction", "secret_signal", "symlink_escape", "unknown_domain"})
    authority_map = {
        "trusted": [],
        "candidate": instruction_entries,
        "untrusted_content": [item["source_ref"] for item in instruction_entries if not item["trusted"]],
        "decision_required": True,
    }
    summary = {
        "root_ref": f"workspace:{content_fingerprint({'path': str(root)})[7:23]}",
        "file_count": len(files),
        "git": git,
        "topology": {"monorepo": monorepo, "nested_repositories": nested},
        "documents": sorted(rel for rel in rel_paths if Path(rel).name in _INSTRUCTION_NAMES),
        "artifacts": {"extensions": sorted(suffixes), "content_files": content_count},
        "verification_signals": verification,
        "domain_candidates": domain_candidates,
        "risks": risks,
        "secret_file_count": len(secret_files),
        "ownership": ownership,
    }
    return WorkspaceDiscovery(root, root.name, "existing", source_fingerprint, rel_paths, tuple(profiles), tuple(unresolved), authority_map, summary)
