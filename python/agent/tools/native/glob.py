"""Read-only file discovery using ripgrep's maintained glob/ignore engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent.security.sensitive_paths import is_sensitive_path
from agent.tools.base import BaseTool, ExecutionContext, ToolResult
from agent.tools.native.file_search import RIPGREP_HINT, RipgrepUnavailableError, resolve_ripgrep, search
from agent.utils.sandbox import SandboxExecutor


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
        try:
            if not full_access:
                # PermissionPolicy has resolved/approved this root. Search only
                # this directory; it never requests arbitrary host execution.
                result = await search(root, workspace=workspace, pattern=args.pattern, files_only=True,
                                      max_files=args.max_results)
            else:
                executable = resolve_ripgrep(root, workspace)
                executor = SandboxExecutor({
                    "workspace_root": str(workspace), "workdir": str(root),
                    "sandbox_mode": "danger_full_access", "allow_network": True,
                })
                result = await executor.execute_argv([
                    executable, "--no-config", "--files", "--null", "--sort", "path", "--glob", args.pattern,
                    ".",
                ])
        except RipgrepUnavailableError:
            return ToolResult.failure(
                {},
                "Trusted ripgrep is unavailable.",
                error_code="RIPGREP_UNAVAILABLE",
                user_hint=RIPGREP_HINT,
            )
        except TimeoutError:
            return ToolResult.failure({}, "File search timed out.", error_code="FILE_SEARCH_TIMEOUT",
                                      user_hint="文件搜索超时，请缩小目录或匹配范围后重试。")
        if result.timed_out:
            return ToolResult.failure({}, "File search timed out.", error_code="FILE_SEARCH_TIMEOUT",
                                      user_hint="文件搜索超时，请缩小目录或匹配范围后重试。")
        if result.exit_code not in {0, 1}:
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
