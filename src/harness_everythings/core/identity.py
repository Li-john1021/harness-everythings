"""确定性 JSON 序列化与内容指纹。

固定输入必须产生相同 canonical 字节与相同指纹；不得引入时间戳、
随机数、区域设置或字典序差异。所有 canonical 记录统一使用本模块。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# canonical JSON：UTF-8、ensure_ascii=False、稳定键序、紧凑分隔符。
# 换行统一为 "\n"，避免 Windows/POSIX 差异影响指纹。
_DUMP_KW: dict[str, Any] = {
    "ensure_ascii": False,
    "sort_keys": True,
    "separators": (",", ":"),
    "allow_nan": False,
}

_CRLF = re.compile(b"\r\n")


def canonical_bytes(value: Any) -> bytes:
    """把 JSON 兼容值序列化为 canonical 字节。"""
    text = json.dumps(value, **_DUMP_KW)
    data = text.encode("utf-8")
    return _CRLF.sub(b"\n", data)


def canonical_json(value: Any) -> str:
    """canonical 字节的文本形式（用于展示与测试）。"""
    return canonical_bytes(value).decode("utf-8")


def content_fingerprint(value: Any) -> str:
    """canonical 字节的 SHA-256 指纹，hex 前缀固定为 sha256:。"""
    digest = hashlib.sha256(canonical_bytes(value)).hexdigest()
    return f"sha256:{digest}"


def bytes_fingerprint(data: bytes) -> str:
    """原始字节的 SHA-256 指纹（用于文件前后哈希比对）。"""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def load_canonical(data: bytes | str) -> Any:
    """解析 JSON；拒绝重复键，避免同一指纹对应多义记录。"""
    def _no_dupes(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, val in pairs:
            if key in result:
                raise ValueError(f"duplicate key in canonical record: {key}")
            result[key] = val
        return result

    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data, object_pairs_hook=_no_dupes)
