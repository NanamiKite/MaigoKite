"""Single registry for native and MCP tools."""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from agent.tools.base import BaseTool

if TYPE_CHECKING:
    from agent.integrations.antenna import AntennaResults
    from agent.mcp.client import MCPClient


class ToolRegistry:
    DEFAULT_TOOLS = (
        "file_read", "file_write", "file_edit", "glob", "grep", "exec_command",
        "web_search", "web_fetch", "browser", "skill",
    )

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if not tool.name or tool.name in self._tools:
            raise ValueError(f"tool already registered or invalid: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, tool_name: str) -> None:
        self._tools.pop(tool_name, None)

    def get(self, tool_name: str) -> BaseTool | None:
        return self._tools.get(tool_name)

    def list_tools(self) -> list[BaseTool]:
        return [self._tools[name] for name in sorted(self._tools)]

    async def discover_mcp(
        self, client: MCPClient, namespace: str, *,
        enabled_tools: list[str] | None = None, disabled_tools: Sequence[str] = (),
        antenna_results: AntennaResults | None = None,
    ) -> list[BaseTool]:
        from agent.tools.mcp_wrapper import MCPToolWrapper
        discovered: list[BaseTool] = []
        for definition in await client.list_tools():
            source_name = str(definition.get("name", ""))
            if source_name in disabled_tools or (enabled_tools is not None and source_name not in enabled_tools):
                continue
            tool = MCPToolWrapper(client, definition, namespace, antenna_results=antenna_results)
            self.register(tool)
            discovered.append(tool)
        return discovered
