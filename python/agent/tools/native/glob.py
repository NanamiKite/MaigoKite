"""Read-only file discovery using ripgrep's maintained glob/ignore engine."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent.security.sensitive_paths import is_sensitive_path, search_exclusions
from agent.tools.base import BaseTool, ExecutionContext, ToolResult
from agent.utils.sandbox import SandboxExecutor, SandboxUnavailableError


class GlobParams(BaseModel):
    pattern: str = Field(min_length=1, description="Ripgrep glob, e.g. '**/*.py' or 'src/**/*.rs'.")
    root_dir: str = Field(default=".", description="Directory to search inside the workspace.")
    max_results: int = Field(default=1000, ge=1, le=10000)


class GlobTool(BaseTool):
    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return "Find files using ripgrep globs. Respects ignore files, skips hidden files and does not follow symlinks."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return GlobParams.model_json_schema()

    async def execute(self, context: ExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        args = GlobParams.model_validate(arguments)
        root = Path(args.root_dir).resolve()
        full_access = context.metadata.get("permission_mode") == "danger_full_access"
        if not full_access and is_sensitive_path(root):
            raise PermissionError("protected credential path")
        workspace = Path(context.metadata.get("workspace_root", str(root))).resolve()
        executor = SandboxExecutor({
            # After an exact outside-path approval, keep rg confined to that
            # read-only directory rather than silently giving it host/network access.
            "workspace_root": str(workspace if full_access or root.is_relative_to(workspace) else root),
            "workdir": str(root),
            "sandbox_mode": "danger_full_access" if full_access else "read_only",
            "allow_network": full_access,
        })
        try:
            if os.name == "nt" and not full_access:
                from agent.tools.native.windows_search import search
                result = await search(root, workspace=workspace, pattern=args.pattern, files_only=True)
            else:
                result = await executor.execute_argv([
                    "rg", "--no-config", "--files", "--null", "--sort", "path", "--glob", args.pattern,
                    *([] if full_access else search_exclusions()), ".",
                ])
        except FileNotFoundError:
            return ToolResult.failure(
                {},
                "ripgrep is not installed.",
                error_code="RIPGREP_UNAVAILABLE",
                user_hint="文件搜索需要 ripgrep（rg）。Windows 请安装在工作区之外的可信 PATH 目录。",
            )
        except SandboxUnavailableError as exc:
            return ToolResult.failure({}, str(exc), error_code="SANDBOX_UNAVAILABLE", user_hint=exc.user_hint)
        if result.timed_out or result.exit_code not in {0, 1}:
            return ToolResult.failure({}, result.stderr or "File search failed.", error_code="FILE_SEARCH_FAILED")
        # A capped output can end in half a filename; never present it as real.
        paths = [
            name
            for name in result.stdout.split("\0")[:-1]
            if full_access or not is_sensitive_path((root / name).resolve())
        ]
        matches = [name.removeprefix("./") for name in paths[:args.max_results]]
        truncated = result.output_truncated or len(paths) > args.max_results
        return ToolResult(
            {"pattern": args.pattern, "matches": matches, "count": len(matches), "truncated": truncated},
            f"Found {len(matches)} file(s)" + (" (truncated)" if truncated else ""),
            raw="\n".join(matches),
        )
