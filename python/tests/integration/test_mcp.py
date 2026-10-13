from __future__ import annotations

import sys
from pathlib import Path

import pytest
from agent.mcp import MCPClient
from agent.tools.base import ExecutionContext
from agent.tools.tool_registry import ToolRegistry

from tests.fixtures.chat_model import FakeModel


@pytest.mark.asyncio
async def test_mcp_stdio_lifecycle():
    fixture = Path(__file__).parents[1] / "fixtures" / "mcp_echo_server.py"
    client = MCPClient(sys.executable, ["-u", str(fixture)])
    await client.start()
    try:
        assert (await client.list_tools())[0]["name"] == "echo"
        result = await client.call_tool("echo", {"text": "ok"})
        assert result["content"][0]["text"] == "ok"
        registry = ToolRegistry()
        discovered = await registry.discover_mcp(client, "fixture")
        assert [tool.name for tool in discovered] == ["fixture__echo"]
        wrapped = await discovered[0].execute(ExecutionContext("trace", "task", "step"), {"text": "wrapped"})
        assert "wrapped" in wrapped.raw
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_agent_core_loads_configured_mcp_tools_through_policy(tmp_path):
    from agent.config_manager import ConfigManager
    from agent.core import AgentCore
    from agent.event_bus import EventBus
    from agent.observability import Observability
    from agent.repository import SQLiteRepository

    fixture = Path(__file__).parents[1] / "fixtures" / "mcp_echo_server.py"
    config = ConfigManager()
    config.set("workspace.root", str(tmp_path))
    config.set("permissions.default_mode", "read_only")
    config.set(
        "mcp.servers.fixture",
        {
            "command": sys.executable,
            "args": ["-u", str(fixture)],
            "cwd": "{workspace}",
        },
    )
    core = AgentCore(config, EventBus(), Observability(), model=FakeModel(), repository=SQLiteRepository(":memory:"))
    await core.initialize()
    try:
        assert core.tool_registry.get("fixture__echo") is not None
        assert await core.list_mcp_servers() == [
            {
                "namespace": "fixture",
                "status": "running",
                "transport": "stdio",
                "tool_names": ["fixture__echo"],
            }
        ]
        conversation = await core.create_conversation(title="mcp", permission_mode="read_only")
        blocked = await core.run(
            '/tool fixture__echo {"text":"through-core"}',
            {"conversation_id": conversation["id"]},
        )
        assert blocked["status"] == "waiting_approval"
        approval = blocked["approval"]
        allowed = await core.decide_approval(
            "personal", approval["approval_id"], "local-user", "allow", approval["version"]
        )
        assert "through-core" in allowed["response"]
    finally:
        await core.shutdown()
    assert core.tool_registry.get("fixture__echo") is None
