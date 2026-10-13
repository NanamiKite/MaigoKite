from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from agent.models.native_model import build_chat_model, close_chat_models
from agent.runtime import LangGraphRuntime
from aiohttp import web
from langchain_core.language_models.chat_models import BaseChatModel


def _chunk(delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
    value = {
        "id": "chatcmpl-runtime-spike",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "local-spike",
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(value)}\n\n".encode()


async def _start_server(
    completion: Any,
) -> tuple[web.AppRunner, str]:
    application = web.Application()
    application.router.add_post("/v1/chat/completions", completion)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/v1"


def _model(base_url: str, provider: str = "openai_compatible") -> BaseChatModel:
    return build_chat_model(
        provider=provider,
        model="local-spike",
        base_url=base_url,
        temperature=0,
        max_tokens=128,
        timeout=5,
        streaming=True,
        api_key="test-key" if provider == "openai" else "",
    )


async def _close_model(model: BaseChatModel) -> None:
    await close_chat_models([model])


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_compatible"])
async def test_real_openai_compatible_http_tool_loop(provider: str, tmp_path: Path) -> None:
    requests: list[dict[str, Any]] = []

    async def completion(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests.append(body)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        has_tool_result = any(message.get("role") == "tool" for message in body["messages"])
        if not has_tool_result:
            arguments = json.dumps({"path": "provider-proof.txt"})
            await response.write(
                _chunk(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-http",
                                "type": "function",
                                "function": {
                                    "name": "file_read",
                                    "arguments": arguments,
                                },
                            }
                        ],
                    }
                )
            )
            await response.write(_chunk({}, "tool_calls"))
        else:
            await response.write(
                _chunk(
                    {
                        "role": "assistant",
                        "content": "MAIGOKITE_HTTP_TOOL_OK",
                    },
                    "stop",
                )
            )
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    runner, base_url = await _start_server(completion)
    model = _model(base_url, provider)
    (tmp_path / "provider-proof.txt").write_text("provider proof", encoding="utf-8")
    try:
        async with LangGraphRuntime(
            model=model,
            workspace_root=tmp_path,
            checkpoint_path=tmp_path / "checkpoints.sqlite",
            product_db_path=tmp_path / "product.sqlite",
        ) as adapter:
            handle = await adapter.start("read provider-proof.txt")
            events = await handle.collect()
            await handle.wait()
    finally:
        await _close_model(model)
        await runner.cleanup()

    event_types = [event.type for event in events]
    assert event_types == [
        "task.started",
        "context.history",
        "context.updated",
        "context.updated",
        "model.call.started",
        "model.call.completed",
        "tool.proposed",
        "tool.started",
        "tool.completed",
        "context.updated",
        "context.updated",
        "model.call.started",
        "message.delta",
        "model.call.completed",
        "message.completed",
        "task.succeeded",
    ]
    assert len(requests) == 2
    read_tool = next(tool["function"] for tool in requests[0]["tools"] if tool["function"]["name"] == "file_read")
    assert read_tool["parameters"]["required"] == ["path"]
    assert requests[0]["tools"] == requests[1]["tools"]
    assert any(message.get("role") == "tool" for message in requests[1]["messages"])


@pytest.mark.asyncio
async def test_real_ollama_wire_protocol_runs_tool_loop(tmp_path: Path) -> None:
    requests: list[dict[str, Any]] = []

    async def chat(request: web.Request) -> web.Response:
        body = await request.json()
        requests.append(body)
        has_tool_result = any(
            message.get("role") == "tool" for message in body["messages"]
        )
        response = {
            "model": "local-spike",
            "created_at": "2026-09-27T00:00:00Z",
            "message": (
                {"role": "assistant", "content": "MAIGOKITE_OLLAMA_TOOL_OK"}
                if has_tool_result
                else {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "file_read",
                                "arguments": {"path": "provider-proof.txt"},
                            }
                        }
                    ],
                }
            ),
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 1,
            "eval_count": 1,
        }
        return web.Response(
            text=json.dumps(response) + "\n",
            content_type="application/x-ndjson",
        )

    application = web.Application()
    application.router.add_post("/api/chat", chat)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    model = build_chat_model(
        provider="ollama",
        model="local-spike",
        base_url=f"http://127.0.0.1:{port}",
        temperature=0,
        max_tokens=64,
        timeout=5,
    )
    (tmp_path / "provider-proof.txt").write_text("proof", encoding="utf-8")
    try:
        async with LangGraphRuntime(
            model=model,
            workspace_root=tmp_path,
            checkpoint_path=tmp_path / "ollama-checkpoints.sqlite",
            product_db_path=tmp_path / "ollama-product.sqlite",
        ) as adapter:
            handle = await adapter.start("reply with the requested marker")
            events = await handle.collect()
            await handle.wait()
    finally:
        await _close_model(model)
        await runner.cleanup()

    assert [event.type for event in events] == [
        "task.started",
        "context.history",
        "context.updated",
        "context.updated",
        "model.call.started",
        "model.call.completed",
        "model.usage",
        "tool.proposed",
        "tool.started",
        "tool.completed",
        "context.updated",
        "context.updated",
        "model.call.started",
        "message.delta",
        "model.call.completed",
        "model.usage",
        "message.completed",
        "task.succeeded",
    ], events[-1].payload
    assert [event.payload["usage"] for event in events if event.type == "model.usage"] == [
        {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    ]
    assert len(requests) == 2
    assert requests[0]["stream"] is True
    assert requests[0]["tools"]


@pytest.mark.asyncio
async def test_real_anthropic_wire_protocol_runs_tool_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    requests: list[dict[str, Any]] = []

    async def send_event(
        response: web.StreamResponse, event: str, data: dict[str, Any]
    ) -> None:
        await response.write(
            f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
        )

    async def messages(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests.append(body)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        has_tool_result = any(
            isinstance(message.get("content"), list)
            and any(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in message["content"]
            )
            for message in body["messages"]
        )
        message_id = f"msg_{len(requests)}"
        await send_event(
            response,
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-local-spike",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 1, "output_tokens": 0,
                        "cache_read_input_tokens": 20 if has_tool_result else 0,
                        "cache_creation_input_tokens": 0 if has_tool_result else 20,
                    },
                },
            },
        )
        if has_tool_result:
            await send_event(
                response,
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            await send_event(
                response,
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "text_delta",
                        "text": "ANTHROPIC_TOOL_OK",
                    },
                },
            )
            stop_reason = "end_turn"
        else:
            await send_event(
                response,
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu-http",
                        "name": "file_read",
                        "input": {},
                    },
                },
            )
            await send_event(
                response,
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"path":"provider-proof.txt"}',
                    },
                },
            )
            stop_reason = "tool_use"
        await send_event(
            response,
            "content_block_stop",
            {"type": "content_block_stop", "index": 0},
        )
        await send_event(
            response,
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {
                    "input_tokens": 1, "output_tokens": 1,
                    "cache_read_input_tokens": 20 if has_tool_result else 0,
                    "cache_creation_input_tokens": 0 if has_tool_result else 20,
                },
            },
        )
        await send_event(response, "message_stop", {"type": "message_stop"})
        await response.write_eof()
        return response

    application = web.Application()
    application.router.add_post("/v1/messages", messages)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    model = build_chat_model(
        provider="anthropic",
        model="claude-local-spike",
        base_url=f"http://127.0.0.1:{port}",
        temperature=0,
        max_tokens=64,
        timeout=5,
        streaming=True,
        api_key="test-key",
    )
    (tmp_path / "provider-proof.txt").write_text("proof", encoding="utf-8")
    try:
        async with LangGraphRuntime(
            model=model,
            workspace_root=tmp_path,
            checkpoint_path=tmp_path / "anthropic-checkpoints.sqlite",
            product_db_path=tmp_path / "anthropic-product.sqlite",
        ) as adapter:
            handle = await adapter.start("read provider-proof.txt")
            events = await handle.collect()
            await handle.wait()
    finally:
        await _close_model(model)
        await runner.cleanup()

    assert [event.type for event in events] == [
        "task.started",
        "context.history",
        "context.updated",
        "context.updated",
        "model.call.started",
        "model.call.completed",
        "model.usage",
        "tool.proposed",
        "tool.started",
        "tool.completed",
        "context.updated",
        "context.updated",
        "model.call.started",
        "message.delta",
        "model.call.completed",
        "model.usage",
        "message.completed",
        "task.succeeded",
    ], events[-1].payload
    assert len(requests) == 2
    read_tool = next(tool for tool in requests[0]["tools"] if tool["name"] == "file_read")
    assert read_tool["input_schema"]["required"] == ["path"]
    assert requests[0]["tools"] == requests[1]["tools"]
    assert requests[0]["tools"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    assert requests[0]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    usage = [event.payload["usage"] for event in events if event.type == "model.usage"]
    assert len(usage) == 2
    assert usage[0].get("cache_creation_tokens") == 20, usage
    assert usage[1]["cache_read_tokens"] == 20
    assert any(
        isinstance(message.get("content"), list)
        and any(block.get("type") == "tool_result" for block in message["content"])
        for message in requests[1]["messages"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_compatible"])
async def test_real_http_stream_is_disconnected_by_root_task_cancel(
    provider: str, tmp_path: Path,
) -> None:
    streaming = asyncio.Event()
    disconnected = asyncio.Event()

    async def completion(request: web.Request) -> web.StreamResponse:
        await request.json()
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        try:
            await response.write(_chunk({"role": "assistant", "content": "first"}))
            streaming.set()
            while True:
                await asyncio.sleep(0.05)
                await response.write(_chunk({"content": "later"}))
        except (ConnectionResetError, asyncio.CancelledError):
            disconnected.set()
            raise

    runner, base_url = await _start_server(completion)
    model = _model(base_url, provider)
    try:
        async with LangGraphRuntime(
            model=model,
            workspace_root=tmp_path,
            checkpoint_path=tmp_path / "checkpoints.sqlite",
            product_db_path=tmp_path / "product.sqlite",
        ) as adapter:
            handle = await adapter.start("stream until cancelled")
            iterator = handle.events().__aiter__()
            events = []
            while True:
                event = await asyncio.wait_for(anext(iterator), timeout=2)
                events.append(event)
                if event.type == "message.delta":
                    break
            await asyncio.wait_for(streaming.wait(), timeout=2)
            await handle.cancel()
            async for event in iterator:
                events.append(event)
            task = await adapter.repository.get_task("personal", handle.request.task_id)
            await asyncio.wait_for(disconnected.wait(), timeout=2)
    finally:
        await _close_model(model)
        await runner.cleanup()

    assert task["status"] == "cancelled"
    assert events[-1].type == "task.cancelled"
    cancelled_index = next(index for index, event in enumerate(events) if event.type == "task.cancelled")
    assert not any(event.type == "message.delta" for event in events[cancelled_index + 1 :])
