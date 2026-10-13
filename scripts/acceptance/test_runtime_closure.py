"""Explicit batch 1–4 acceptance; all state belongs in pytest's temporary path."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent.browser_runtime import BrowserRuntime
from agent.config_manager import ConfigManager
from agent.core import AgentCore
from agent.event_bus import EventBus
from agent.mcp.client import MCPClient, MCPError
from agent.models.native_model import build_chat_model, close_chat_models
from agent.observability import Observability
from agent.repository import SQLiteRepository
from agent.runtime import LangGraphRuntime
from agent.runtime.context import ContextBudgetExceeded, ContextBudgetMiddleware, effective_input_budget
from aiohttp import web
from langchain.agents.middleware.types import ModelRequest
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from tests.fixtures.chat_model import FakeModel


@pytest.mark.parametrize(
    "profile,expected",
    [
        ({"max_input_tokens": 2048}, 1664),
        ({"max_input_tokens": 100000}, 98720),
        ({}, 32000),
        ({"max_input_tokens": True}, 32000),
    ],
)
def test_budget_uses_sdk_capacity_without_model_name_table(profile, expected):
    assert effective_input_budget(SimpleNamespace(profile=profile, max_tokens=256), 32000) == expected


@pytest.mark.asyncio
async def test_small_model_rejected_before_network_without_slicing():
    messages = [HumanMessage("large request " * 600)]
    request = ModelRequest(
        model=FakeModel(profile={"max_input_tokens": 512}),
        messages=messages,
        tools=[],
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    async def forbidden(_):
        raise AssertionError("model should not be called")

    with pytest.raises(ContextBudgetExceeded):
        await ContextBudgetMiddleware(32000).awrap_model_call(request, forbidden)
    assert request.messages == messages


async def make_core(tmp_path):
    config = ConfigManager()
    config.set("workspace.root", str(tmp_path))
    config.set("browser.enabled", False)
    core = AgentCore(config, EventBus(), Observability(), FakeModel(), SQLiteRepository(tmp_path / "product.db"))
    await core.initialize()
    assert core._langgraph_runtime.checkpoint_path == tmp_path / "product.langgraph.db"
    return core


@pytest.mark.asyncio
async def test_cleanup_failure_still_closes_remaining_owners(tmp_path, monkeypatch):
    core = await make_core(tmp_path)
    runtime = core._langgraph_runtime
    order = []
    real_models, real_repository = core.models.close, core.repository.close

    async def fail_after_drained():
        order.append("drained")
        raise RuntimeError("injected cleanup failure after all roots ended")

    async def models():
        order.append("models")
        await real_models()

    async def repository():
        order.append("repository")
        await real_repository()

    monkeypatch.setattr(runtime, "cancel_active_runs", fail_after_drained)
    monkeypatch.setattr(core.models, "close", models)
    monkeypatch.setattr(core.repository, "close", repository)
    with pytest.raises(ExceptionGroup):
        await core.shutdown()
    assert order == ["drained", "models", "repository"]
    assert runtime._checkpointer is None and not runtime._opened
    assert core._langgraph_runtime is None and not core._initialized


@pytest.mark.asyncio
async def test_cancelled_shutdown_waiter_does_not_abandon_cleanup(tmp_path, monkeypatch):
    core = await make_core(tmp_path)
    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = core.models.close

    async def slow_close():
        entered.set()
        await release.wait()
        await original()
        closed.set()

    monkeypatch.setattr(core.models, "close", slow_close)
    caller = asyncio.create_task(core.shutdown())
    await asyncio.wait_for(entered.wait(), 5)
    second = asyncio.create_task(core.shutdown())
    caller.cancel()
    await asyncio.sleep(0)
    assert not caller.done() and not closed.is_set()
    release.set()
    results = await asyncio.wait_for(asyncio.gather(caller, second, return_exceptions=True), 5)
    assert isinstance(results[0], asyncio.CancelledError) and results[1] is None
    assert closed.is_set() and core._shutdown_task.done()


@pytest.mark.asyncio
async def test_browser_driver_closes_even_if_chromium_close_fails(tmp_path):
    runtime = BrowserRuntime(tmp_path)
    stopped = []

    async def fail():
        raise RuntimeError("browser disconnected during close")

    async def stop():
        stopped.append(True)

    runtime._browser = SimpleNamespace(close=fail)
    runtime._playwright = SimpleNamespace(stop=stop)
    with pytest.raises(ExceptionGroup):
        await runtime.close()
    assert stopped == [True] and runtime._playwright is None and runtime._browser is None


@pytest.mark.asyncio
async def test_provider_cleanup_continues_after_one_client_failure():
    called = []

    async def broken():
        called.append("async")
        raise RuntimeError("close failed")

    def close():
        called.append("sync")

    model = ChatOpenAI(model="test-only", api_key="test-only")
    object.__setattr__(model, "root_async_client", SimpleNamespace(close=broken))
    object.__setattr__(model, "root_client", SimpleNamespace(close=close))
    with pytest.raises(ExceptionGroup):
        await close_chat_models([model])
    assert called == ["async", "sync"]


@pytest.mark.asyncio
async def test_mcp_cancel_and_timeout_keep_shared_session_usable():
    client = MCPClient(sys.executable, ["-B", str(Path(__file__).with_name("test_mcp_server.py"))], request_timeout=5)
    await client.start()
    try:
        slow = asyncio.create_task(client.call_tool("delayed_echo", {"text": "slow", "delay": 1}))
        await asyncio.sleep(0.1)
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow
        assert (await client.call_tool("delayed_echo", {"text": "still open"}))["content"][0]["text"] == "still open"
        client.request_timeout = 0.05
        with pytest.raises(MCPError, match="outcome may be unknown"):
            await client.call_tool("delayed_echo", {"text": "timeout", "delay": 0.5})
        client.request_timeout = 5
        assert (await client.call_tool("delayed_echo", {"text": "after timeout"}))["content"][0][
            "text"
        ] == "after timeout"
    finally:
        await asyncio.wait_for(client.stop(), 10)
    assert client._owner is None and client._session is None


@pytest.mark.asyncio
async def test_failed_mcp_initialization_keeps_native_core_available(tmp_path):
    config = ConfigManager()
    config.set("workspace.root", str(tmp_path))
    config.set("browser.enabled", False)
    config.set("mcp.servers.invalid", {"command": str(tmp_path / "missing-executable")})
    core = AgentCore(config, EventBus(), Observability(), FakeModel(), SQLiteRepository(tmp_path / "failed.db"))
    await core.initialize()
    try:
        assert core._initialized and core._mcp_clients == {}
        assert await core.list_mcp_servers() == [
            {
                "namespace": "invalid",
                "status": "error",
                "transport": "stdio",
                "tool_names": [],
            }
        ]
        assert core.tool_registry.get("file_read") is not None
        assert core.tool_registry.get("invalid__echo") is None
        result = await core.run("hello")
        assert result["status"] == "succeeded"
        assert result["response"] == "reply:hello"
    finally:
        await core.shutdown()
    assert not core._initialized and core._mcp_clients == {}
    with pytest.raises(RuntimeError):
        core.repository._db()


@pytest.mark.asyncio
async def test_core_startup_failure_releases_connected_mcp_and_repository(tmp_path, monkeypatch):
    config = ConfigManager()
    config.set("workspace.root", str(tmp_path))
    config.set("browser.enabled", False)
    config.set(
        "mcp.servers.fixture",
        {
            "command": sys.executable,
            "args": ["-B", str(Path(__file__).with_name("test_mcp_server.py"))],
        },
    )
    core = AgentCore(config, EventBus(), Observability(), FakeModel(), SQLiteRepository(tmp_path / "startup.db"))
    clients = []

    async def fail_runtime(_registry, _skills):
        clients.extend(core._mcp_clients.values())
        assert len(clients) == 1 and clients[0].is_running
        raise RuntimeError("injected runtime startup failure")

    monkeypatch.setattr(core, "_create_langgraph_runtime", fail_runtime)
    with pytest.raises(RuntimeError, match="injected runtime startup failure"):
        await core.initialize()
    assert not core._initialized and core._mcp_clients == {}
    assert all(client._owner is None and client._session is None for client in clients)
    with pytest.raises(RuntimeError):
        core.repository._db()


@pytest.mark.asyncio
async def test_event_pages_preserve_snapshot_watermark_and_identity(tmp_path):
    repo = SQLiteRepository(tmp_path / "events.db")
    await repo.open()
    try:
        conversation = await repo.create_conversation("personal", "event replay")
        cid = conversation["id"]
        await repo.start_chat_turn("task_pages", "personal", cid, "hello")
        payload = {"task_id": "task_pages", "conversation_id": cid, "content": "x"}
        receipts = [await repo.append_task_event("message.delta", payload) for _ in range(1005)]
        snapshot = await repo.conversation_snapshot("personal", cid)
        assert snapshot["last_sequence"] == 1005
        first = await repo.list_task_events("personal", cid, limit=1000)
        assert len(first) == 1000
        late = await repo.append_task_event("message.completed", payload)
        second = await repo.list_task_events("personal", cid, first[-1]["sequence"], limit=1000)
        assert [event["sequence"] for event in first + second] == list(range(1, 1007))
        assert [event["id"] for event in first + second] == [item["event_id"] for item in receipts + [late]]
        assert await repo.list_task_events("personal", cid, late["sequence"]) == []
        assert [item["id"] for item in await repo.list_task_events("personal", cid, 1005)] == [late["event_id"]]
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "ollama"])
async def test_provider_cancel_disconnects_and_keeps_model_reusable(tmp_path, monkeypatch, provider):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    streaming, disconnected, paused = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    class PauseFirstToken(AsyncCallbackHandler):
        async def on_llm_new_token(self, token, **kwargs):
            if token and not paused.is_set():
                paused.set()
                # Deterministic cancellation while the SDK stream is suspended
                # at yield, not just while HTTP is awaiting its next bytes.
                await asyncio.Event().wait()

    async def response(request):
        nonlocal calls
        await request.json()
        calls += 1
        stream_forever = calls == 1
        result = web.StreamResponse(
            headers={"Content-Type": "text/event-stream" if provider == "anthropic" else "application/x-ndjson"}
        )
        await result.prepare(request)

        async def event(kind, value):
            await result.write(f"event: {kind}\ndata: {json.dumps({'type': kind, **value})}\n\n".encode())

        try:
            if provider == "anthropic":
                await event(
                    "message_start",
                    {
                        "message": {
                            "id": "msg_cancel",
                            "type": "message",
                            "role": "assistant",
                            "model": "local-model",
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 1, "output_tokens": 0},
                        }
                    },
                )
                await event("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}})
            while True:
                if provider == "anthropic":
                    await event("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "OK"}})
                else:
                    await result.write(
                        (
                            json.dumps(
                                {
                                    "model": "local-model",
                                    "created_at": "2026-10-10T00:00:00Z",
                                    "message": {"role": "assistant", "content": "OK"},
                                    "done": not stream_forever,
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                streaming.set()
                if not stream_forever:
                    break
                await asyncio.sleep(0.02)
            if provider == "anthropic":
                await event("content_block_stop", {"index": 0})
                await event(
                    "message_delta",
                    {"delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}},
                )
                await event("message_stop", {})
            await result.write_eof()
        except ConnectionResetError, asyncio.CancelledError:
            disconnected.set()
        return result

    app = web.Application()
    app.router.add_post("/v1/messages" if provider == "anthropic" else "/api/chat", response)
    runner = web.AppRunner(app, shutdown_timeout=1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    model = build_chat_model(
        provider,
        "local-model",
        base_url=f"http://127.0.0.1:{port}",
        api_key="local-test",
        max_tokens=64,
        timeout=5,
        callbacks=[PauseFirstToken()],
    )
    try:
        async with LangGraphRuntime(
            model=model,
            workspace_root=tmp_path,
            product_db_path=tmp_path / "provider.db",
            checkpoint_path=tmp_path / "provider-checkpoint.db",
        ) as runtime:
            handle = await runtime.start("stream")
            await asyncio.wait_for(streaming.wait(), 5)
            await asyncio.wait_for(paused.wait(), 5)
            sibling = await runtime.start("concurrent client")
            assert (await asyncio.wait_for(sibling.collect(), 5))[-1].type == "task.succeeded"
            await sibling.wait()
            assert not handle.root_task.done()
            await asyncio.wait_for(handle.cancel(), 5)
            events = await handle.collect()
            assert events[-1].type == "task.cancelled" and handle.root_task.done()
            await asyncio.wait_for(disconnected.wait(), 5)
            again = await runtime.start("reuse client")
            completed = await asyncio.wait_for(again.collect(), 5)
            await again.wait()
            assert completed[-1].type == "task.succeeded"
            assert any(event.type == "message.completed" and event.payload["content"] == "OK" for event in completed)
    finally:
        await close_chat_models([model])
        await runner.cleanup()
