"""Schema 注册表与校验。

- Schema 随包分发于 schemas/v1/，本模块提供加载与结构校验。
- 校验为最小内建实现（不引入第三方依赖）：校验必需字段、类型与
  枚举；未知 schema_version 一律拒绝。
- schema 前向迁移（1.x -> 1.y）以纯函数注册，回退必须显式允许。
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any, Callable

from .identity import content_fingerprint

SCHEMA_VERSIONS = ("1.0",)
CURRENT_SCHEMA_VERSION = "1.0"

# schema_version -> 包内目录名（1.0 存于 v1/）。
_VERSION_DIRS = {"1.0": "v1"}


class SchemaError(ValueError):
    """记录不符合其声明的 schema 版本。"""


# ---------------------------------------------------------------------------
# 最小校验器：type / required / enum / additionalProperties
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def _check(value: Any, schema: dict[str, Any], path: str) -> None:
    ref = schema.get("$ref")
    if ref:
        entity_type = str(ref).rsplit("/", 1)[-1].removesuffix(".schema.json")
        _check(value, load_schema(entity_type), path)
        return
    expected_type = schema.get("type")
    if expected_type:
        python_type = _TYPE_MAP.get(expected_type)
        if python_type is None:
            raise SchemaError(f"{path}: unsupported schema type {expected_type!r}")
        if expected_type == "integer" and isinstance(value, bool):
            raise SchemaError(f"{path}: expected integer, got boolean")
        if expected_type == "number" and isinstance(value, bool):
            raise SchemaError(f"{path}: expected number, got boolean")
        if not isinstance(value, python_type):
            raise SchemaError(
                f"{path}: expected {expected_type}, got {type(value).__name__}"
            )
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: {value!r} not in enum {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{path}: expected const {schema['const']!r}")
    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if minimum is not None and len(value) < minimum:
            raise SchemaError(f"{path}: string is shorter than minLength {minimum}")
        if maximum is not None and len(value) > maximum:
            raise SchemaError(f"{path}: string is longer than maxLength {maximum}")
        pattern = schema.get("pattern")
        if pattern is not None and re.fullmatch(pattern, value) is None:
            raise SchemaError(f"{path}: value does not match pattern {pattern!r}")
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                raise SchemaError(f"{path}: missing required field {req!r}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and props:
            for key in value:
                if key not in props:
                    raise SchemaError(f"{path}: unexpected field {key!r}")
        for key, sub in props.items():
            if key in value:
                _check(value[key], sub, f"{path}.{key}")
    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            raise SchemaError(f"{path}: array has fewer than minItems {minimum}")
        if maximum is not None and len(value) > maximum:
            raise SchemaError(f"{path}: array has more than maxItems {maximum}")
        if "items" in schema:
            for i, item in enumerate(value):
                _check(item, schema["items"], f"{path}[{i}]")


# ---------------------------------------------------------------------------
# 加载与校验入口
# ---------------------------------------------------------------------------

_CACHE: dict[str, dict[str, Any]] = {}


def load_schema(entity_type: str, version: str = CURRENT_SCHEMA_VERSION) -> dict[str, Any]:
    """从包资源加载 JSON Schema；未知版本拒绝。"""
    if version not in SCHEMA_VERSIONS:
        raise SchemaError(f"unknown schema version: {version!r}")
    cache_key = f"{version}/{entity_type}"
    if cache_key not in _CACHE:
        try:
            base = resources.files("harness_everythings").joinpath("schemas")
            ref = base.joinpath(_VERSION_DIRS[version]).joinpath(
                f"{entity_type}.schema.json"
            )
            _CACHE[cache_key] = json.loads(ref.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SchemaError(f"schema not found: {cache_key}") from exc
    return _CACHE[cache_key]


def validate(entity_type: str, record: dict[str, Any]) -> None:
    """按记录声明的 schema_version 校验 canonical 记录。"""
    version = record.get("schema_version")
    if version not in SCHEMA_VERSIONS:
        raise SchemaError(f"unknown schema version: {version!r}")
    schema = load_schema(entity_type, version)
    _check(record, schema, entity_type)


# ---------------------------------------------------------------------------
# 前向迁移注册表（1.x 内）；回退必须显式注册且默认禁止
# ---------------------------------------------------------------------------

Migration = Callable[[dict[str, Any]], dict[str, Any]]
_MIGRATIONS: dict[tuple[str, str], Migration] = {}
_REVERSE_ALLOWED: set[tuple[str, str]] = set()


def register_migration(
    from_version: str, to_version: str, fn: Migration, *, allow_reverse: bool = False
) -> None:
    _MIGRATIONS[(from_version, to_version)] = fn
    if allow_reverse:
        _REVERSE_ALLOWED.add((to_version, from_version))


def migrate(record: dict[str, Any], target: str) -> dict[str, Any]:
    """把记录迁移到目标版本；只允许注册过的路径。"""
    current = record.get("schema_version")
    if current == target:
        return record
    key = (current, target)
    if key not in _MIGRATIONS:
        if (target, current) in _MIGRATIONS and key not in _REVERSE_ALLOWED:
            raise SchemaError(
                f"reverse migration {current} -> {target} not allowed"
            )
        raise SchemaError(f"no migration path: {current} -> {target}")
    return _MIGRATIONS[key](record)


def record_fingerprint(record: dict[str, Any]) -> str:
    """实体记录指纹（迁移对比与幂等校验用）。"""
    return content_fingerprint(record)
