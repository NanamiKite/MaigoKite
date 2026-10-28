"""Format failures for model messages, persisted task records and UI events.

SDK exception strings may contain credentials, request bodies or local paths.
Only explicitly authored product diagnostics may cross these boundaries intact.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

type ErrorParameter = str | int | float | bool


class UserFacingError(ValueError):
    """A product-authored diagnostic containing no third-party exception text."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        params: Mapping[str, ErrorParameter] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        # Codes and safe scalar parameters travel separately from the fallback
        # copy. Never populate these fields from third-party exception bodies.
        self.code = code
        self.params = dict(params or {})
        self.status_code = status_code


class UserFacingCancellation(asyncio.CancelledError):
    """Cancellation with a safe product warning, such as unconfirmed cleanup.

    Remains a BaseException so recoverable tool-error handlers cannot swallow stop.
    """


def error_location(error: BaseException) -> str:
    """Locate internal failures without logging exception bodies, URLs or locals."""
    trace = error.__traceback__
    if trace is None:
        return type(error).__name__
    while trace.tb_next is not None:
        trace = trace.tb_next
    code = trace.tb_frame.f_code
    return f"{type(error).__name__} at {Path(code.co_filename).name}:{trace.tb_lineno} ({code.co_name})"


def public_error(error: BaseException) -> str:
    """Preserve actionable product errors without exposing arbitrary exception text."""
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    if isinstance(error, (UserFacingError, UserFacingCancellation)):
        return str(error)
    status = getattr(error, "status_code", None)
    if type(status) is int and 400 <= status <= 599:
        if status in {401, 403}:
            return f"服务拒绝访问（HTTP {status}），请检查凭证与访问权限。"
        if status == 429:
            return "服务请求受限（HTTP 429），请稍后重试或检查服务状态。"
        return f"服务请求失败（HTTP {status}），请检查接口地址、参数与服务状态。"
    if isinstance(error, FileNotFoundError):
        return "目标文件或程序不存在，请检查所选路径与安装情况。"
    if isinstance(error, PermissionError):
        return "操作被拒绝，请检查工作区、文件访问权限或当前授权。"
    if isinstance(error, TimeoutError):
        return "操作超时，请检查服务连接或缩小任务范围。"
    if isinstance(error, (ConnectionError, OSError)):
        return "无法访问文件、程序或服务，请检查路径、权限和连接。"
    # Keep the exception type for diagnosis; args/body/response are untrusted.
    return f"操作失败（{type(error).__name__}），请检查服务配置和输入。"
