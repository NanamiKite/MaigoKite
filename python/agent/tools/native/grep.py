"""Read-only content search using ripgrep, not a second regex/search engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent.security.sensitive_paths import is_sensitive_path
from agent.tools.base import BaseTool, ExecutionContext, ToolResult
from agent.tools.native.file_search import RIPGREP_HINT, RipgrepUnavailableError, resolve_ripgrep, search
from agent.utils.sandbox import SandboxExecutor


class GrepParams(BaseModel):
    pattern: str = Field(min_length=1, description="Ripgrep (Rust regex) pattern, not Python regex.")
    path: str = Field(default=".", description="Workspace file or directory to search.")
    ignore_case: bool = False
    max_results: int = Field(default=30, ge=1, le=1000)
    context_lines: int = Field(default=0, ge=0, le=20)


class GrepTool(BaseTool):
    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search text with ripgrep. Respects ignore files, skips hidden/binary files and does not follow symlinks."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return GrepParams.model_json_schema()

    async def execute(self, context: ExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        args = GrepParams.model_validate(arguments)
        target = Path(args.path).resolve()
        full_access = context.metadata.get("permission_mode") == "danger_full_access"
        if not full_access and is_sensitive_path(target):
            raise PermissionError("protected credential path")
        root = target if target.is_dir() else target.parent
        workspace = Path(context.metadata.get("workspace_root", str(root))).resolve()
        try:
            if not full_access:
                result = await search(root, workspace=workspace, pattern=args.pattern, files_only=False, target=target,
                                      ignore_case=args.ignore_case, context_lines=args.context_lines)
            else:
                executable = resolve_ripgrep(root, workspace)
                executor = SandboxExecutor({
                    "workspace_root": str(workspace), "workdir": str(root),
                    "sandbox_mode": "danger_full_access", "allow_network": True,
                })
                command = [
                    executable, "--no-config", "--json", "--sort", "path", "--context", str(args.context_lines),
                ]
                if args.ignore_case:
                    command.append("--ignore-case")
                command.extend(["--", args.pattern, "." if target.is_dir() else str(target)])
                result = await executor.execute_argv(command)
        except RipgrepUnavailableError:
            return ToolResult.failure(
                {},
                "Trusted ripgrep is unavailable.",
                error_code="RIPGREP_UNAVAILABLE",
                user_hint=RIPGREP_HINT,
            )
        except TimeoutError:
            return ToolResult.failure({}, "Content search timed out.", error_code="FILE_SEARCH_TIMEOUT",
                                      user_hint="文件搜索超时，请缩小目录或匹配范围后重试。")
        if result.timed_out:
            return ToolResult.failure({}, "Content search timed out.", error_code="FILE_SEARCH_TIMEOUT",
                                      user_hint="文件搜索超时，请缩小目录或匹配范围后重试。")
        if result.exit_code not in {0, 1}:
            return ToolResult.failure({}, result.stderr or "Content search failed.", error_code="FILE_SEARCH_FAILED")
        matches: list[tuple[str, int]] = []
        lines: dict[str, dict[int, str]] = {}
        files_scanned = 0
        for row in result.stdout.splitlines():
            try:
                event = json.loads(row)
            except json.JSONDecodeError:
                if result.output_truncated:
                    break
                raise
            data = event.get("data", {})
            if event.get("type") == "summary":
                files_scanned = int(data.get("stats", {}).get("searches", 0))
            if event.get("type") not in {"match", "context"}:
                continue
            path = data.get("path", {}).get("text")
            text = data.get("lines", {}).get("text")
            if not isinstance(path, str) or not isinstance(text, str):
                continue
            if not full_access and is_sensitive_path((root / path).resolve()):
                continue
            filename = str(Path(path).relative_to(root)) if Path(path).is_absolute() else path.removeprefix("./")
            number = int(data["line_number"])
            lines.setdefault(filename, {})[number] = text.rstrip("\r\n")
            if event["type"] == "match":
                matches.append((filename, number))
        hits: list[dict[str, Any]] = [
            {
                "file": path,
                "line_number": number,
                "context": "\n".join(
                    lines[path][line]
                    for line in range(max(1, number - args.context_lines), number + args.context_lines + 1)
                    if line in lines[path]
                ),
            }
            for path, number in matches[: args.max_results]
        ]
        # Bound long matching lines too, without rewriting an earlier ToolMessage.
        budget = 16_000
        selected = []
        content_truncated = False
        for hit in hits:
            if budget <= 0:
                content_truncated = True
                break
            text = hit["context"]
            hit["context"] = text[:min(budget, 2000)]
            hit["context_truncated"] = len(hit["context"]) < len(text)
            content_truncated |= hit["context_truncated"]
            budget -= len(hit["context"]) + len(hit["file"]) + 80
            selected.append(hit)
        hits = selected
        truncated = result.output_truncated or len(matches) > len(hits) or content_truncated
        return ToolResult(
            {"pattern": args.pattern, "matches": hits, "total_count": len(hits),
             "files_scanned": files_scanned, "truncated": truncated},
            f"Found {len(hits)} match(es)" + (" (truncated)" if truncated else ""),
            raw="\n".join(f"{hit['file']}:{hit['line_number']}: {hit['context']}" for hit in hits),
        )
