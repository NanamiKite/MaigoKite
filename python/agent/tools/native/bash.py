"""Native command execution, including PowerShell; BashTool is a legacy alias."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, BinaryIO, Literal

from pydantic import BaseModel, Field

from agent.errors import public_error
from agent.tools.base import BaseTool, ExecutionContext, ToolResult
from agent.utils.sandbox import SandboxExecutor, SandboxUnavailableError


class BashParams(BaseModel):
    """Parameters for executing a command."""

    command: str = Field(
        min_length=1,
        max_length=8_192,
        description=(
            "Command text: auto uses PowerShell on Windows, direct argv elsewhere. "
            "For PowerShell pass the original script, not pwsh -Command or Base64. "
            "Use concise commands; file_read/file_write avoid shell file-encoding differences."
        ),
    )
    shell: Literal["auto", "direct", "powershell", "pwsh"] = Field(
        default="auto",
        description=(
            "auto: Windows prefers PowerShell 7 then Windows PowerShell 5.1; other OSes use direct. "
            "powershell: Windows 5.1 only; pwsh: 7+ only (no fallback). "
            "direct: legacy POSIX-quoted argv, no shell expansion; not Windows command-line syntax. "
            "Scripts receive/send UTF-8 text; native programs with other encodings need explicit handling. "
            "File encoding is separate: 5.1 -Encoding UTF8 writes a BOM; 7 writes no BOM. "
            "Do not use utf8NoBOM in 5.1; prefer file_read/file_write."
        ),
    )
    timeout: int = Field(
        default=30,
        ge=1,
        le=120,
        description="Maximum seconds to wait for the command before killing it.",
    )
    workdir: str | None = Field(
        default=None,
        description="Working directory; outside-workspace access requires approval or full access.",
    )
    sandbox_permissions: Literal["use_default", "require_escalated"] = Field(
        default="use_default",
        description=(
            "Request one host execution only when needed. Also request network_access=true "
            "because host execution cannot isolate networking. Both require approval, never grant it."
        ),
    )
    network_access: bool = Field(
        default=False, description="Request network access for this command; requires approval in confined modes."
    )
    max_output_bytes: int = Field(
        default=8000,
        ge=256,
        le=32_000,
        description="Preview bytes per stream. Logs share an 8 MiB total cap; exceeding it stops the command.",
    )
    interactive: bool = Field(
        default=False,
        description=(
            "Allow user stdin from the process panel. Requires approval; not a terminal or detached job. "
            "Not supported by the native Windows sandbox; do not escalate just to enable it. Timeout still applies."
        ),
    )


BASH_DESCRIPTION = (
    "Execute a command using the selected shell. PowerShell supports Chinese text, variables and pipelines. "
    "Direct argv has no shell expansion. Linux restricted commands use a workspace sandbox with no network. "
    "Windows restricted commands use the optional native SRT backend when installed and enabled; "
    "otherwise report sandbox unavailable. Never silently fall back to host execution. "
    "Request network_access for networking within the sandbox, "
    "or require_escalated plus network_access for host execution; policy decides approval. "
    "Returns bounded stdout/stderr, exit code and log paths. Use file_read with offset to inspect more output."
)


class ExecCommandTool(BaseTool):
    @property
    def name(self) -> str:
        return "exec_command"

    @property
    def description(self) -> str:
        host = "Windows (auto selects PowerShell)" if os.name == "nt" else "non-Windows (auto selects direct argv)"
        return f"{BASH_DESCRIPTION} Current host: {host}."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return BashParams.model_json_schema()

    async def execute(
        self, context: ExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        workspace_root = Path(
            str(context.metadata.get("workspace_root") or arguments.get("workdir") or Path.cwd())
        ).expanduser().resolve()
        workdir = Path(str(arguments.get("workdir") or workspace_root)).expanduser().resolve()
        limits = context.metadata.get("command_limits", {})
        executor = SandboxExecutor(
            {"workspace_root": str(workspace_root), "workdir": str(workdir),
             "sandbox_mode": context.metadata.get("sandbox_mode", "workspace"),
             "allow_network": context.metadata.get("allow_network", False),
             "output_limit": int(arguments.get("max_output_bytes", 8000)),
             "cpu_seconds": limits.get("cpu_seconds", 0),
             "address_space_mb": limits.get("address_space_mb", 0),
             "windows_sandbox": context.metadata.get("windows_sandbox", {}),
             "output_directory": str(workspace_root / ".maigokite" / "outputs")}
        )
        registry = context.metadata.get("process_registry")
        def on_spawn(process: asyncio.subprocess.Process, stdout: BinaryIO, stderr: BinaryIO,
                     stop: Callable[[asyncio.subprocess.Process], Awaitable[None]]) -> None:
            if registry is not None:
                registry.attach(context.step_id, str(context.metadata.get("workspace_id", "personal")),
                                context.task_id, process, stdout, stderr, stop,
                                interactive=bool(arguments.get("interactive", False)))
        def on_exit() -> None:
            if registry is not None:
                registry.detach(context.step_id)
        try:
            result = await executor.execute_command(
                str(arguments["command"]), int(arguments.get("timeout", 30)), on_spawn=on_spawn, on_exit=on_exit,
                interactive=bool(arguments.get("interactive", False)),
                shell=str(arguments.get("shell", "auto")),
            )
        except SandboxUnavailableError as exc:
            return ToolResult.failure({}, public_error(exc), error_code="SANDBOX_UNAVAILABLE", user_hint=exc.user_hint)
        except FileNotFoundError as exc:
            return ToolResult.failure(
                {},
                public_error(exc),
                error_code="COMMAND_UNAVAILABLE",
                user_hint="请检查程序安装和 PATH。pwsh 需要 PowerShell 7；powershell 指 Windows PowerShell 5.1。",
            )

        parts = [f"Exit code {result.exit_code}"]
        if result.timed_out:
            parts.insert(0, "Timed out")
        if result.output_limit_exceeded:
            parts.insert(0, "Stopped: combined stdout/stderr log limit exceeded")
        parts.append(f"Duration: {result.duration_ms / 1000:.2f}s")

        return ToolResult(
            data={
                "return_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_seconds": round(result.duration_ms / 1000, 3),
                "timed_out": result.timed_out,
                "output_truncated": result.output_truncated,
                "output_limit_exceeded": result.output_limit_exceeded,
                "output_bytes_limit": result.output_bytes_limit,
                "process_limits": {"cpu_seconds": executor.cpu_seconds,
                                   "address_space_mb": executor.address_space_mb,
                                   "scope": "per_process"},
                "sandboxed": result.sandboxed,
                "stdout_path": result.stdout_path,
                "stderr_path": result.stderr_path,
                "shell": result.shell,
                "executable": result.executable,
                "expected_text_encoding": "utf-8",
                "output_decoding_errors": result.output_decoding_errors,
            },
            summary="; ".join(parts),
            raw=result.stdout or result.stderr or "",
            metadata={
                "tool": self.name,
                "type": "process",
                "exit_code": result.exit_code,
                "sandboxed": result.sandboxed,
            },
            success=result.exit_code == 0 and not result.timed_out,
            user_hint=("命令标准输出与错误输出合计超过日志上限，已请求停止。请缩小输出范围；日志仅保留上限内的部分。"
                       if result.output_limit_exceeded else
                       "外部程序输出包含非 UTF-8 字节；预览可能乱码，请显式配置该程序的输出编码。"
                       if result.output_decoding_errors else None),
            error_code=("COMMAND_OUTPUT_LIMIT" if result.output_limit_exceeded else
                        "COMMAND_TIMEOUT" if result.timed_out else "COMMAND_FAILED")
            if result.exit_code != 0 or result.timed_out or result.output_limit_exceeded else None,
        )


class BashTool(ExecCommandTool):
    """Historical command alias; not exposed as a second model tool."""

    @property
    def name(self) -> str:
        return "bash"
