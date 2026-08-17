"""确定性 Markdown 视图（Spec 第 6 节：D-005）。

Markdown 只能是 canonical 数据的确定性人类视图，不得增加 canonical
数据中不存在的结论。固定输入 -> 固定输出；显示时间由调用方注入，
不影响 canonical 指纹。
"""

from __future__ import annotations

from typing import Any

from ..core.identity import content_fingerprint


def _kv(label: str, value: Any) -> str:
    if value is None:
        value = ""
    elif isinstance(value, (dict, list)):
        value = content_fingerprint(value)
    return f"| {label} | `{str(value)}` |"


def render_entity(record: dict[str, Any]) -> str:
    """把 canonical 实体记录渲染为确定性 Markdown。"""
    lines: list[str] = []
    entity_type = record.get("entity_type", "record")
    lines.append(f"# {entity_type}: {record.get('entity_id', '(no id)')}")
    lines.append("")
    lines.append("| 字段 | 值 |")
    lines.append("|---|---|")
    for key in sorted(record):
        if key in ("entity_type",):
            continue
        lines.append(_kv(key, record[key]))
    lines.append("")
    lines.append(
        f"<!-- canonical-fingerprint: {content_fingerprint(record)} -->"
    )
    lines.append("")
    return "\n".join(lines)


def render_manifest(manifest_record: dict[str, Any]) -> str:
    """把 ApplicationManifest 记录渲染为可审核 Markdown。"""
    lines = [
        "# ApplicationManifest（dry-run 提案）",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        _kv("workspace_fingerprint", manifest_record.get("workspace_fingerprint")),
        _kv("idempotency_key", manifest_record.get("idempotency_key")),
        _kv("created_at", manifest_record.get("created_at")),
        "",
        "| # | 目标路径 | 独占创建 | 目标指纹 |",
        "|---|---|---|---|",
    ]
    for i, w in enumerate(manifest_record.get("writes", []), start=1):
        lines.append(
            f"| {i} | `{w['rel']}` | {w['exclusive']} | `{w['target_fingerprint']}` |"
        )
    lines.append("")
    lines.append(
        "<!-- 本视图不含超出 canonical 数据的结论；apply 需用户批准 -->"
    )
    lines.append("")
    return "\n".join(lines)


def render_task(task: dict[str, Any]) -> str:
    """把任务记录渲染为确定性 Markdown。"""
    lines = [
        f"# task: {task.get('task_id', '(no id)')}",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        _kv("state", task.get("state")),
    ]
    for key in sorted(k for k in task if k not in ("state", "task_id", "transitions")):
        lines.append(_kv(key, task[key]))
    transitions = task.get("transitions", [])
    lines.append("")
    lines.append(f"状态变化共 {len(transitions)} 次：")
    for t in transitions:
        lines.append(
            f"- {t['from_state']} -> {t['to_state']}（{t['actor']}，{t['at']}）"
        )
    lines.append("")
    return "\n".join(lines)
