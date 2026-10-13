from __future__ import annotations

import json

import pytest
from agent.models.native_model import build_chat_model, close_chat_models
from aiohttp import web
from langchain_core.messages import HumanMessage


@pytest.mark.asyncio
async def test_openai_compatible_local_port_chat_and_stream():
    requests: list[dict] = []

    async def completion(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests.append({"body": body, "authorization": request.headers.get("Authorization", "")})
        if body.get("stream"):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            for content, reason in (("hel", None), ("lo", "stop")):
                event = {
                    "id": "chatcmpl-local", "object": "chat.completion.chunk", "model": "local-test",
                    "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": reason}],
                }
                await response.write(f"data: {json.dumps(event)}\n\n".encode())
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response
        return web.json_response({
            "id": "chatcmpl-local", "object": "chat.completion", "model": "local-test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        })

    application = web.Application()
    application.router.add_post("/v1/chat/completions", completion)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    adapter = build_chat_model(
        provider="openai_compatible", model="local-test", base_url=f"http://127.0.0.1:{port}/v1",
        temperature=0, max_tokens=32,
    )
    try:
        response = await adapter.ainvoke([HumanMessage(content="ping")])
        assert response.content == "OK"
        chunks = [chunk async for chunk in adapter.astream([HumanMessage(content="ping")])]
        assert "".join(chunk.content for chunk in chunks) == "hello"
        assert all(item["authorization"] == "Bearer local-no-key" for item in requests)
    finally:
        await close_chat_models([adapter])
        await runner.cleanup()
