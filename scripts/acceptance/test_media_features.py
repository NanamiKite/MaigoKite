"""Exercise media through the public API and official SDK, with a loopback service only."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web
from tests.integration.test_langgraph_process_recovery import (
    _connect_events,
    _events_until,
    _openai_chunk,
    _send_message,
    _wait_for_task,
    _wait_pending_approval,
)

from scripts.acceptance.test_personal_agent import Product, api, finished

# A real, decodable 1x1 PNG, not just a signature accepted by the API.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6"
    "V8sAAAAASUVORK5CYII="
)
B64 = base64.b64encode(PNG).decode()


@pytest_asyncio.fixture
async def media(tmp_path: Path):
    calls = []
    waiting, disconnected, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def model(request):
        body = await request.json()
        calls.append({"endpoint": request.path, "body": body})
        messages = body["messages"]
        index = max(i for i, message in enumerate(messages) if message["role"] == "user")
        content = messages[index]["content"]
        text = content if isinstance(content, str) else " ".join(item.get("text", "") for item in content)
        results = [message for message in messages[index + 1:] if message["role"] == "tool"]
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        if text.startswith("GENERATE ") and not results:
            delta = {"role": "assistant", "tool_calls": [{
                "index": 0, "id": f"image-call-{len(calls)}", "type": "function",
                "function": {"name": "image_generate", "arguments": json.dumps({"prompt": text[9:]})},
            }]}
            await response.write(_openai_chunk(delta))
            await response.write(_openai_chunk({}, "tool_calls"))
        elif text == "PLAN" and not results:
            todos = [{"content": "Inspect input", "status": "completed"},
                     {"content": "Produce result", "status": "in_progress"}]
            await response.write(_openai_chunk({"role": "assistant", "tool_calls": [{
                "index": 0, "id": f"plan-{len(calls)}", "type": "function",
                "function": {"name": "write_todos", "arguments": json.dumps({"todos": todos})},
            }, {
                "index": 1, "id": f"read-{len(calls)}", "type": "function",
                "function": {"name": "file_read", "arguments": '{"path":"evidence.txt"}'},
            }]}))
            await response.write(_openai_chunk({}, "tool_calls"))
        else:
            has_image = isinstance(content, list) and any(item.get("type") == "image_url" for item in content)
            await response.write(_openai_chunk({
                "role": "assistant", "content": "IMAGE_SEEN" if has_image else "MEDIA_DONE",
            }, "stop"))
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    async def generation(request):
        body = await request.json()
        calls.append({"endpoint": request.path, "body": body})
        prompt = body.get("prompt", body.get("input", ""))
        if prompt == "FAIL":
            return web.json_response({"error": {"message": "SECRET_ERROR_CANARY", "type": "server_error"}}, status=500)
        if prompt == "SLOW":
            response = web.StreamResponse(headers={"Content-Type": "application/json"})
            await response.prepare(request)
            waiting.set()
            try:
                while not release.is_set():
                    await response.write(b" ")
                    await asyncio.sleep(0.03)
            except (ConnectionResetError, RuntimeError):
                disconnected.set()
            return response
        if request.path.endswith("responses"):
            return web.json_response({
                "id": "resp-acceptance", "created_at": 1, "object": "response", "status": "completed",
                "model": body["model"], "output": [{
                    "id": "ig-acceptance", "type": "image_generation_call", "status": "completed", "result": B64,
                }],
            })
        item = {"b64_json": "not base64!" if prompt == "INVALID" else B64}
        if body.get("response_format") == "url":
            item = {"url": f"http://{request.host}/artifact.png"}
        return web.json_response({"created": 1, "data": [item]})

    async def artifact(request):
        calls.append({"endpoint": request.path, "headers": dict(request.headers)})
        return web.Response(body=PNG, content_type="image/png")

    async def models(_request):
        return web.json_response({"data": [{"id": "process-recovery", "context_length": 32768}]})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", model)
    app.router.add_post("/v1/images/generations", generation)
    app.router.add_post("/v1/responses", generation)
    app.router.add_get("/v1/models", models)
    app.router.add_get("/artifact.png", artifact)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1"
    product = Product(tmp_path, url, calls, waiting, disconnected, release)
    (tmp_path / "evidence.txt").write_text("PLAN_EVIDENCE", encoding="utf-8")
    try:
        await product.start()
        yield product
    finally:
        try:
            await product.stop()
        finally:
            release.set()
            await asyncio.gather(*(client.aclose() for client in product.clients))
            await runner.cleanup()


async def settings(media: Product, **overrides):
    body = {"enabled": True, "model": "fixture-image", "protocol": "images", "base_url": media.model_url}
    return await api(media.client, "PUT", "/settings/image-generation", json={"settings": {**body, **overrides}})


def generations(media: Product):
    return [call for call in media.requests if call["endpoint"] in {"/v1/images/generations", "/v1/responses"}]


async def upload(media: Product):
    return await api(media.client, "POST", "/files/images", 201, json={"data": B64})


@pytest.mark.asyncio
async def test_upload_chat_snapshot_fork_and_restart(media):
    image = await upload(media)
    assert (media.root / image["path"]).read_bytes() == PNG
    assert image["sha256"] == hashlib.sha256(PNG).hexdigest()
    preview = await api(media.client, "GET", "/files/content", params={"path": image["path"]})
    assert preview["data_url"] == f"data:image/png;base64,{B64}" and not preview["truncated"]
    conv = await api(media.client, "POST", "/conversations", 201, json={"permission_mode": "read_only"})
    cid = conv["id"]
    sent = await api(media.client, "POST", f"/conversations/{cid}/messages", 202,
                     json={"content": "Inspect image", "attachments": [image]}, headers={"Idempotency-Key": "image-1"})
    assert (await _wait_for_task(media.client, sent["task_id"], {"succeeded", "failed"}))["status"] == "succeeded"
    requests = [call["body"] for call in media.requests if call["endpoint"].endswith("chat/completions")]
    assert f"data:image/png;base64,{B64}" in json.dumps(requests[-1])
    replay = await api(media.client, "POST", f"/conversations/{cid}/messages", json={
        "content": "Inspect image", "attachments": [image],
    }, headers={"Idempotency-Key": "image-1"})
    assert replay["task_id"] == sent["task_id"] and replay["replayed"]
    messages = (await api(media.client, "GET", f"/conversations/{cid}/messages"))["messages"]
    assert json.loads(messages[0]["attachments_json"]) == [image]
    assert messages[-1]["content"] == "IMAGE_SEEN"
    events = await api(media.client, "GET", f"/conversations/{cid}/events/history")
    assert B64 not in json.dumps(events) and B64 not in json.dumps(messages)
    fork = await api(media.client, "POST", f"/conversations/{cid}/fork", 201,
                     json={"message_id": messages[-1]["id"]}, headers={"Idempotency-Key": "image-fork"})
    copied = (await api(media.client, "GET", f"/conversations/{fork['id']}/messages"))["messages"]
    assert copied[0]["attachments_json"] == messages[0]["attachments_json"]
    await media.stop()
    await media.start()
    restarted = (await api(media.client, "GET", f"/conversations/{cid}/messages"))["messages"]
    assert restarted == messages
    await finished(media.client, cid, "Follow up after restart")
    assert f"data:image/png;base64,{B64}" in json.dumps(media.requests[-1])


@pytest.mark.asyncio
async def test_attachment_tamper_cross_folder_and_invalid_upload_rejected(media):
    for data in ("invalid base64!", base64.b64encode(b"not an image").decode(), base64.b64encode(b"BMbitmap").decode()):
        await api(media.client, "POST", "/files/images", 400, json={"data": data})
    image = await upload(media)
    conv = await api(media.client, "POST", "/conversations", 201, json={})
    endpoint = f"/conversations/{conv['id']}/messages"
    for attachment in ({**image, "folder_id": "another-folder"}, {**image, "sha256": "0" * 64}):
        response = await media.client.post("/api/v1/workspaces/personal" + endpoint, json={
            "content": "inspect", "attachments": [attachment],
        })
        assert response.status_code == 400, response.text
    (media.root / image["path"]).write_bytes(b"changed")
    response = await media.client.post("/api/v1/workspaces/personal" + endpoint,
                                       json={"content": "inspect", "attachments": [image]})
    assert response.status_code == 400, response.text
    assert not media.requests
    assert not (await api(media.client, "GET", "/tasks", params={"conversation_id": conv["id"]}))["tasks"]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,response_format", [("images", "b64_json"), ("images", "url"), ("responses", "")])
async def test_real_sdk_generation_approval_file_and_events(media, protocol, response_format):
    await settings(media, protocol=protocol, response_format=response_format, size="1024x1024")
    conv = await api(media.client, "POST", "/conversations", 201, json={"permission_mode": "read_only"})
    cid = conv["id"]
    async with await _connect_events(media.url, cid) as socket:
        sent = await _send_message(media.client, cid, "GENERATE a blue cat")
        approval = await _wait_pending_approval(media.client, cid)
        assert not generations(media) and not list((media.root / "generated-images").glob("*"))
        await api(media.client, "POST", f"/approvals/{approval['id']}/decision", 202,
                  json={"decision": "allow", "version": approval["version"], "scope": "once"})
        events = await _events_until(socket, lambda event: event["type"] == "task.succeeded")
    assert (await _wait_for_task(media.client, sent["task_id"], {"succeeded"}))["status"] == "succeeded"
    calls = generations(media)
    assert len(calls) == 1
    body = calls[0]["body"]
    if protocol == "responses":
        assert body["store"] is False and body["max_tool_calls"] == 1
        assert body["tools"] == [{"type": "image_generation", "size": "1024x1024"}]
    else:
        assert body["n"] == 1 and body["size"] == "1024x1024" and body["response_format"] == response_format
    complete = next(event for event in events if event["type"] == "tool.completed")
    assert "generated-images/" in json.dumps(complete)
    assert any(event["type"] == "file.changed" for event in events)
    paths = list((media.root / "generated-images").glob("*.png"))
    assert len(paths) == 1 and paths[0].read_bytes() == PNG
    assert B64 not in json.dumps(events)
    if response_format == "url":
        headers = next(call["headers"] for call in media.requests if call["endpoint"] == "/artifact.png")
        assert not any(name.lower() in {"authorization", "cookie", "proxy-authorization"} for name in headers)


@pytest.mark.asyncio
async def test_denied_generation_never_dispatches(media):
    await settings(media)
    conv = await api(media.client, "POST", "/conversations", 201, json={"permission_mode": "read_only"})
    sent = await _send_message(media.client, conv["id"], "GENERATE rejected cat")
    approval = await _wait_pending_approval(media.client, conv["id"])
    await api(media.client, "POST", f"/approvals/{approval['id']}/decision", 202, json={
        "decision": "deny", "version": approval["version"], "note": "Do not generate.",
    })
    assert (await _wait_for_task(media.client, sent["task_id"], {"succeeded", "failed"}))["status"] == "succeeded"
    assert not generations(media) and not list((media.root / "generated-images").glob("*"))


@pytest.mark.asyncio
async def test_image_configuration_change_invalidates_pending_approval(media):
    await settings(media)
    conv = await api(media.client, "POST", "/conversations", 201, json={"permission_mode": "read_only"})
    await _send_message(media.client, conv["id"], "GENERATE old configuration")
    approval = await _wait_pending_approval(media.client, conv["id"])
    await settings(media, model="different-model")
    await api(media.client, "POST", f"/approvals/{approval['id']}/decision", 403, json={
        "decision": "allow", "version": approval["version"], "scope": "once",
    })
    assert not generations(media), "old approval must never authorize a different model or endpoint"
    assert not list((media.root / "generated-images").glob("*"))
    pending = await _wait_pending_approval(media.client, conv["id"])
    assert pending["id"] == approval["id"] and pending["version"] == approval["version"]


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", ["FAIL", "INVALID"])
async def test_generation_failure_does_not_retry_or_leak_provider_error(media, prompt):
    await settings(media)
    conv = await api(media.client, "POST", "/conversations", 201, json={"permission_mode": "danger_full_access"})
    await finished(media.client, conv["id"], "GENERATE " + prompt)
    assert len(generations(media)) == 1
    events = (await api(media.client, "GET", f"/conversations/{conv['id']}/events/history"))["events"]
    assert any(event["type"] == "tool.failed" for event in events)
    assert "SECRET_ERROR_CANARY" not in json.dumps(events)
    assert not list((media.root / "generated-images").glob("*"))


@pytest.mark.asyncio
async def test_cancel_generation_disconnects_and_stops_late_events(media):
    await settings(media)
    conv = await api(media.client, "POST", "/conversations", 201, json={"permission_mode": "danger_full_access"})
    sent = await _send_message(media.client, conv["id"], "GENERATE SLOW")
    await asyncio.wait_for(media.waiting.wait(), 15)
    await api(media.client, "POST", f"/tasks/{sent['task_id']}/cancel")
    await _wait_for_task(media.client, sent["task_id"], {"cancelled"})
    await asyncio.wait_for(media.disconnected.wait(), 10)
    before = await api(media.client, "GET", f"/conversations/{conv['id']}/events/history")
    await asyncio.sleep(0.1)
    assert before == await api(media.client, "GET", f"/conversations/{conv['id']}/events/history")
    assert len(generations(media)) == 1 and not list((media.root / "generated-images").glob("*"))


@pytest.mark.asyncio
async def test_image_settings_validation_visibility_and_real_restart(media):
    initial = await api(media.client, "GET", "/settings/image-generation")
    assert not initial["settings"]["enabled"] and not initial["credential_configured"]
    for overrides in ({"model": ""}, {"protocol": "responses", "response_format": "url"}):
        body = {"enabled": True, "model": "fixture", "base_url": media.model_url, **overrides}
        await api(media.client, "PUT", "/settings/image-generation", 400, json={"settings": body})
    configured = await settings(media)
    assert not configured["credential_configured"]
    await media.stop()
    await media.start()
    assert await api(media.client, "GET", "/settings/image-generation") == configured
    catalog = await api(media.client, "GET", "/tool-settings")
    assert next(tool for tool in catalog["tools"] if tool["name"] == "image_generate")["enabled"]
    assert not media.requests


@pytest.mark.asyncio
async def test_inflight_generation_core_crash_does_not_automatically_redispatch(media):
    await settings(media)
    conv = await api(media.client, "POST", "/conversations", 201, json={"permission_mode": "danger_full_access"})
    sent = await _send_message(media.client, conv["id"], "GENERATE SLOW")
    await asyncio.wait_for(media.waiting.wait(), 15)
    process = media.processes[-1]
    process.kill()
    await asyncio.wait_for(process.communicate(), 10)
    await asyncio.wait_for(media.disconnected.wait(), 10)
    await media.start()
    task = await api(media.client, "GET", f"/tasks/{sent['task_id']}")
    assert task["status"] == "interrupted" and len(generations(media)) == 1
    await finished(media.client, conv["id"], "GENERATE explicitly new request")
    assert len(generations(media)) == 2
    assert len(list((media.root / "generated-images").glob("*.png"))) == 1


@pytest.mark.asyncio
async def test_plan_and_file_tool_same_model_turn_projects_actual_steps(media):
    conv = await api(media.client, "POST", "/conversations", 201, json={"permission_mode": "read_only"})
    await finished(media.client, conv["id"], "PLAN")
    events = (await api(media.client, "GET", f"/conversations/{conv['id']}/events/history"))["events"]
    plans = [event for event in events if event["type"] == "plan.updated"]
    assert plans, [event["type"] for event in events]
    assert "in_progress" in json.dumps(plans) and "completed" in json.dumps(plans)
    assert any(event["type"] == "tool.completed" and "PLAN_EVIDENCE" in json.dumps(event) for event in events)
