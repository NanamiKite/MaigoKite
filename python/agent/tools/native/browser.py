"""BrowserTool - controlled browser operations backed by Playwright."""

from __future__ import annotations

from typing import Any

from agent.browser_runtime import BrowserRuntime, BrowserRuntimeUnavailable, BrowserSessionUnavailable
from agent.errors import public_error
from agent.tools.base import BaseTool, ExecutionContext, ToolResult


class BrowserTool(BaseTool):
    def __init__(self, runtime: BrowserRuntime) -> None:
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Operate an isolated Chromium page. Open a public HTTP(S) URL, read a text snapshot, "
            "fill fields, click or press keys, save a screenshot inside the workspace, or close the session. "
            "Upload a workspace file to an input selector, or download by clicking a selector to a new workspace path. "
            "For upload/download, supply url from the latest snapshot to bind approval to that page. "
            "Open with a named profile to retain login across restarts; "
            "visible=true opens a window for the user to log in. "
            "Never request passwords in chat. Reuse the session_id in this conversation; "
            "close before reopening a profile. "
            "Sessions are bounded and idle pages expire. "
            "Reopen the named profile if its session expired; login data is retained."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["open", "snapshot", "fill", "click", "press", "screenshot", "upload", "download", "close"],
                },
                "session_id": {"type": "string", "maxLength": 128},
                "url": {
                    "type": "string",
                    "maxLength": 2048,
                    "description": "Navigation URL for open; expected current page URL for upload/download.",
                },
                "selector": {"type": "string", "maxLength": 1000},
                "text": {"type": "string", "maxLength": 10000},
                "key": {"type": "string", "maxLength": 100},
                "path": {"type": "string", "maxLength": 1000},
                "profile": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,48}$"},
                "visible": {"type": "boolean", "default": False},
            },
            "required": ["action"],
            "additionalProperties": False,
        }

    async def execute(self, context: ExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        action = str(arguments["action"])
        unrestricted = context.metadata.get("sandbox_mode") == "danger_full_access"
        owner = (
            str(context.metadata.get("workspace_id", "personal")),
            str(context.metadata.get("conversation_id", "")),
            str(context.metadata.get("workspace_root", self.runtime.workspace_root)),
        )
        try:
            if action == "open":
                result = await self.runtime.open(
                    self._required(arguments, "url"),
                    owner=owner,
                    profile=str(arguments.get("profile", "")),
                    visible=bool(arguments.get("visible", False)),
                )
            else:
                session_id = self._required(arguments, "session_id")
                async with self.runtime.operation(session_id, owner):
                    result = await self._session_action(context, arguments, session_id, unrestricted)
        except BrowserRuntimeUnavailable as exc:
            return ToolResult.failure(
                {"action": action, "error": public_error(exc)},
                "Browser runtime is unavailable.",
                error_code="BROWSER_RUNTIME_UNAVAILABLE",
                user_hint="请检查 Playwright、Chromium 和配置目录；打开登录窗口还需要可用的桌面显示环境。",
            )
        except BrowserSessionUnavailable:
            # Page IDs are process-local; only a named profile's login data
            # survives restart/idle cleanup. Give the model a recovery action,
            # not an opaque KeyError it may retry with the same expired handle.
            hint = (
                "浏览器页面已关闭、过期或随服务重启失效。请重新打开目标 URL；"
                "需要保留登录态时使用原 profile，不要重试旧 session_id。"
            )
            return ToolResult.failure(
                {"action": action}, hint,
                error_code="BROWSER_SESSION_EXPIRED", user_hint=hint,
            )
        except (KeyError, ValueError) as exc:
            return ToolResult.failure(
                {"action": action, "error": public_error(exc)},
                public_error(exc),
                error_code="BROWSER_ACTION_INVALID",
            )
        except Exception as exc:
            return ToolResult.failure(
                {"action": action, "error": type(exc).__name__},
                f"Browser action {action} failed.",
                error_code="BROWSER_ACTION_FAILED",
            )
        raw = result.get("text") if isinstance(result.get("text"), str) else None
        return ToolResult(result, f"Browser {action} completed.", raw=raw)

    async def _session_action(
        self,
        context: ExecutionContext,
        arguments: dict[str, Any],
        session_id: str,
        unrestricted: bool,
    ) -> dict[str, Any]:
        action = str(arguments["action"])
        if action == "snapshot":
            result = await self.runtime.snapshot(self._required(arguments, "session_id"))
        elif action == "fill":
            result = await self.runtime.fill(
                session_id,
                self._required(arguments, "selector"),
                self._required(arguments, "text", allow_empty=True),
            )
        elif action == "click":
            result = await self.runtime.click(session_id, self._required(arguments, "selector"))
        elif action == "press":
            result = await self.runtime.press(
                session_id,
                self._required(arguments, "selector"),
                self._required(arguments, "key"),
            )
        elif action == "screenshot":
            result = await self.runtime.screenshot(
                session_id,
                self._required(arguments, "path"),
                workspace_root=context.metadata.get("workspace_root"),
                unrestricted=unrestricted,
            )
        elif action in {"upload", "download"}:
            operation = self.runtime.upload if action == "upload" else self.runtime.download
            result = await operation(
                session_id,
                self._required(arguments, "selector"),
                self._required(arguments, "path"),
                expected_url=self._required(arguments, "url"),
                workspace_root=context.metadata.get("workspace_root"),
                unrestricted=unrestricted,
            )
        elif action == "close":
            result = await self.runtime.close_session(self._required(arguments, "session_id"))
        else:
            raise ValueError(f"unsupported browser action: {action}")
        return result

    @staticmethod
    def _required(arguments: dict[str, Any], name: str, *, allow_empty: bool = False) -> str:
        value = arguments.get(name)
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
            raise ValueError(f"{name} is required for this browser action")
        return value
