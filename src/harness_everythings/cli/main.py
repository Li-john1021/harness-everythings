"""Command line entry point for the H2/H3 lifecycle contract."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from harness_everythings import __version__
from harness_everythings.cli.errors import ExitCode, HarnessError
from harness_everythings.core.lifecycle import (
    LifecycleError,
    apply_init_proposal,
    build_init_proposal,
    diff_workspace,
    doctor_workspace,
    inspect_workspace,
    reconcile_workspace,
    retire_generated,
    status_workspace,
    upgrade_workspace,
)
from harness_everythings.core.schema_registry import SCHEMA_VERSIONS, load_schema
from harness_everythings.storage.manifest import ManifestError, apply_manifest

COMMAND_RISK: dict[str, dict[str, Any]] = {
    "init": {"risk": "write", "dry_run_default": True},
    "inspect": {"risk": "read", "dry_run_default": False},
    "diff": {"risk": "read", "dry_run_default": False},
    "reconcile": {"risk": "write", "dry_run_default": True},
    "doctor": {"risk": "read", "dry_run_default": False},
    "doctor-schemas": {"risk": "read", "dry_run_default": False},
    "status": {"risk": "read", "dry_run_default": False},
    "upgrade": {"risk": "write", "dry_run_default": True},
    "retire": {"risk": "write", "dry_run_default": True},
}


def _default_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness-everythings", description="领域无关 Agent 生产治理内核")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser, *, dry_run: bool) -> None:
        p.add_argument("--workspace", default=".")
        p.add_argument("--format", choices=["json", "markdown"], default="json")
        p.add_argument("--now", default=None, help="固定 canonical 时间，便于复现")
        if dry_run:
            p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
            p.add_argument("--approval-file", default=None, help="批准 JSON 文件；仅在 --no-dry-run 时读取")

    init_parser = sub.add_parser("init", help="建立工作区画像和治理接入 Plan")
    init_parser.add_argument("mode", choices=["new", "existing"])
    add_common(init_parser, dry_run=True)
    add_common(sub.add_parser("inspect", help="只读刷新观察事实"), dry_run=False)
    add_common(sub.add_parser("diff", help="比较当前来源与已保存画像"), dry_run=False)
    add_common(sub.add_parser("reconcile", help="生成漂移对账提案"), dry_run=True)
    add_common(sub.add_parser("doctor", help="检查画像、Schema 和生命周期不变量"), dry_run=False)
    add_common(sub.add_parser("doctor-schemas", help="校验全部 JSON Schema 可加载"), dry_run=False)
    add_common(sub.add_parser("status", help="展示当前生命周期状态"), dry_run=False)
    add_common(sub.add_parser("upgrade", help="生成版本升级提案"), dry_run=True)
    add_common(sub.add_parser("retire", help="移除哈希仍匹配的 Harness 生成文件"), dry_run=True)
    return parser


def cmd_doctor_schemas(_args: argparse.Namespace) -> dict[str, Any]:
    entity_types = (
        "workspace", "profile-record", "plan", "output-contract", "role", "task",
        "artifact", "evidence", "approval", "handoff", "governance-proposal",
        "application-manifest", "workspace-profile", "unresolved", "authority-map",
        "generated-files", "role-registry", "role-reconciliation", "context-routes",
        "context-route", "adapter-contract", "adapter-state", "domain-pack-manifest",
        "software-output-contract", "traceability", "verification-result", "verification-results",
        "content-brief", "content-output-contract", "content-variant", "content-review", "content-review-check", "content-variants",
        "evaluation", "governance-effect", "checkpoint",
        "artifact-ledger", "evidence-ledger", "evaluation-ledger", "software-review", "software-delivery-state",
        "artifact-binding", "evidence-binding", "verification-binding", "artifact-events", "evidence-events", "evaluation-events", "content-delivery-state",
    )
    loaded = []
    for entity_type in entity_types:
        for version in SCHEMA_VERSIONS:
            schema = load_schema(entity_type, version)
            loaded.append({"entity_type": entity_type, "version": version})
            if not schema.get("properties"):
                raise HarnessError("internal_invariant", f"schema {entity_type}@{version} has no properties")
    return {"schemas_checked": len(loaded), "ok": True}


def _approval(path: str | None) -> dict[str, Any]:
    if not path:
        raise LifecycleError("approval_missing", "--approval-file is required for a writing apply")
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LifecycleError("invalid_input", "approval file is not valid JSON") from exc
    if not isinstance(value, dict):
        raise LifecycleError("invalid_input", "approval file must contain an object")
    return value


def _write_apply(root: Path, manifest: Any, approval: dict[str, Any]) -> dict[str, Any]:
    try:
        return apply_manifest(root, manifest, approval)
    except ManifestError as exc:
        message = str(exc)
        category = "fingerprint_conflict" if "fingerprint" in message or "workspace" in message else "approval_missing"
        raise LifecycleError(category, message) from exc


def _run(args: argparse.Namespace) -> dict[str, Any]:
    now = args.now or _default_now()
    root = Path(args.workspace)
    if args.command == "doctor-schemas":
        return cmd_doctor_schemas(args)
    if args.command == "init":
        proposal = build_init_proposal(root, args.mode, now)
        result = proposal.to_result(include_payloads=False)
        if not args.dry_run and proposal.manifest is not None:
            result["apply"] = apply_init_proposal(root, proposal, _approval(args.approval_file))
        return result
    if args.command == "inspect":
        return inspect_workspace(root, now)
    if args.command == "diff":
        return diff_workspace(root, now)
    if args.command == "reconcile":
        result, manifest = reconcile_workspace(root, now)
        if not args.dry_run and manifest is not None:
            result["apply"] = _write_apply(root, manifest, _approval(args.approval_file))
        return result
    if args.command == "doctor":
        result = doctor_workspace(root, now)
        if not result["ok"]:
            raise LifecycleError("verification_failed", "doctor found lifecycle issues")
        return result
    if args.command == "status":
        return status_workspace(root, now)
    if args.command == "upgrade":
        result = upgrade_workspace(root, now)
        manifest = result.pop("_manifest", None)
        if not args.dry_run and manifest is not None:
            manifest_result = _write_apply(root, manifest, _approval(args.approval_file))
            result["apply"] = manifest_result
        return result
    if args.command == "retire":
        return retire_generated(root, _approval(args.approval_file) if not args.dry_run else None, apply=not args.dry_run)
    raise LifecycleError("invalid_input", f"unknown command: {args.command!r}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = _run(args)
    except HarnessError as exc:
        print(json.dumps({"error": {"category": exc.category, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return int(exc.exit_code)
    except LifecycleError as exc:
        from harness_everythings.cli.errors import ERROR_CATEGORIES
        code = ERROR_CATEGORIES.get(exc.category, ExitCode.INTERNAL_INVARIANT)
        print(json.dumps({"error": {"category": exc.category, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return int(code)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": {"category": "invalid_input", "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return int(ExitCode.INVALID_INPUT)
    if args.format == "markdown":
        print("```json")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        print("```")
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.exit(main())
