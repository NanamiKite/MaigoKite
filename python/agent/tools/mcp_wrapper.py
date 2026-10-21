"""Expose an MCP tool through the common BaseTool contract."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from agent.mcp.client import MCPClient, MCPError
from agent.tools.base import BaseTool, ExecutionContext, ToolResult

if TYPE_CHECKING:
    from agent.integrations.antenna import AntennaResults


def _model_safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    if not normalized:
        raise ValueError("MCP namespace and tool names must contain letters or digits")
    return normalized


def mcp_tool_prefix(namespace: str) -> str:
    """Use the same namespace identity for discovery and approval restoration.

    Preserve existing tool names so persisted approvals/checkpoints remain valid.
    Configuration rejects namespaces that collapse to the same prefix.
    """
    return _model_safe_name(namespace) + "__"


class MCPToolWrapper(BaseTool):
    def __init__(
        self, client: MCPClient, definition: dict[str, Any], namespace: str,
        *, antenna_results: AntennaResults | None = None,
    ) -> None:
        source_name = str(definition.get("name") or "")
        schema = definition.get("inputSchema", {"type": "object"})
        if not source_name:
            raise ValueError("MCP tool is missing a name")
        if not isinstance(schema, dict) or schema.get("type", "object") != "object":
            raise ValueError(f"MCP tool {source_name!r} has an invalid input schema")
        if len(json.dumps(schema, ensure_ascii=False, default=str)) > 100_000:
            raise ValueError(f"MCP tool {source_name!r} schema is too large")

        self._client = client
        self._definition = definition
        self._source_name = source_name
        self._schema = dict(schema)
        self._namespace = namespace
        self._antenna_results = antenna_results
        self._name = mcp_tool_prefix(namespace) + _model_safe_name(source_name)
        if len(self._name) > 64:
            raise ValueError(f"MCP tool name is too long: {self._name}")

        # Server-provided annotations describe intent, but cannot grant permission.
        # An absent destructiveHint (or a claimed readOnlyHint) is not a locally
        # verified capability. Full-access mode must not silently trust it.
        self.risk_level = 5

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        description = str(self._definition.get("description", ""))[:4_000]
        if self._antenna_results is not None and self._source_name == "antenna.solve":
            description += (
                " MaigoKite imports verified result files into antenna-results/ in this conversation's workspace "
                "and shows PNG previews. This requires write permission. Approval also covers these resource reads "
                "and local copies. An import failure is not a reason to solve again."
            )
        return description

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self._schema

    async def execute(
        self, context: ExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        if not self._client.is_running:
            return ToolResult.failure(
                {}, "MCP 服务未连接，本次工具请求未发送。", error_code="MCP_UNAVAILABLE",
                user_hint="请在扩展页面恢复连接，再重新发起需要此工具的操作。",
                action={"label": "查看扩展", "to": "/integrations"},
            )
        try:
            response = await self._client.call_tool(self._source_name, arguments)
        except MCPError:
            # An SDK timeout/disconnect is not evidence that the remote action
            # stopped. Return a tool failure, without retrying or leaking errors.
            return ToolResult.failure(
                {}, "MCP 请求未取得可靠回执，远端操作可能已经执行。", error_code="MCP_OUTCOME_UNKNOWN",
                user_hint="先核实目标服务中的实际结果，不要自动重试；可继续处理不依赖此操作的任务。",
                action={"label": "查看扩展", "to": "/integrations"},
            )
        if (self._antenna_results is not None and self._source_name == "antenna.solve"
                and isinstance(response, dict) and response.get("isError") is not True):
            return await self._antenna_results.project(
                self._client, self._namespace, context, arguments, response,
            )
        text = json.dumps(response, ensure_ascii=False, default=str)
        metadata = {"tool": self.name, "source": "mcp"}
        if not isinstance(response, dict):
            return ToolResult.failure(
                data={"result": response},
                summary="MCP server returned an invalid tool result.",
                error_code="MCP_INVALID_RESULT",
                user_hint="MCP 服务返回了无法识别的结果，请检查服务版本和连接配置。",
                raw=text,
                metadata=metadata,
            )
        if response.get("isError") is True:
            return ToolResult.failure(
                data={"result": response},
                summary=f"MCP tool {self._source_name} reported a failure.",
                error_code="MCP_TOOL_ERROR",
                user_hint="MCP 工具执行失败，请查看返回详情，并检查服务连接、凭证和工具参数。",
                raw=text,
                metadata=metadata,
            )
        return ToolResult(
            data={"result": response},
            summary=text[:500],
            raw=text,
            metadata=metadata,
        )
