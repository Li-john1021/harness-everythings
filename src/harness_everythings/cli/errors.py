"""CLI 上下文：稳定错误类别与退出码（H1 冻结）。

错误类别与退出码一经发布即稳定；具体编号在本模块冻结。
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    INVALID_INPUT = 2
    SCHEMA_INCOMPATIBLE = 3
    BOUNDARY_ESCAPE = 4
    APPROVAL_MISSING = 5
    FINGERPRINT_CONFLICT = 6
    USER_FILE_CONFLICT = 7
    CAPABILITY_MISSING = 8
    VERIFICATION_FAILED = 9
    EXTERNAL_TOOL_FAILED = 10
    INTERNAL_INVARIANT = 11


# 类别名 -> 退出码，供异常统一映射。
ERROR_CATEGORIES: dict[str, ExitCode] = {
    "invalid_input": ExitCode.INVALID_INPUT,
    "schema_incompatible": ExitCode.SCHEMA_INCOMPATIBLE,
    "boundary_escape": ExitCode.BOUNDARY_ESCAPE,
    "approval_missing": ExitCode.APPROVAL_MISSING,
    "fingerprint_conflict": ExitCode.FINGERPRINT_CONFLICT,
    "user_file_conflict": ExitCode.USER_FILE_CONFLICT,
    "capability_missing": ExitCode.CAPABILITY_MISSING,
    "verification_failed": ExitCode.VERIFICATION_FAILED,
    "external_tool_failed": ExitCode.EXTERNAL_TOOL_FAILED,
    "internal_invariant": ExitCode.INTERNAL_INVARIANT,
}


class HarnessError(Exception):
    """带稳定错误类别的 CLI 错误。"""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.exit_code = ERROR_CATEGORIES.get(
            category, ExitCode.INTERNAL_INVARIANT
        )
