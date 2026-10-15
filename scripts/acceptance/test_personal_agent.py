"""Product acceptance through a real Core process, TCP HTTP/WS and real tools.

Only the model server is scripted. No provider credentials or developer state are
needed. Reuse the process-recovery transport rather than replacing Core methods.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from aiohttp import web
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from tests.integration.test_langgraph_process_recovery import (
    API,
    _connect_events,
    _events_until,
    _free_port,
    _openai_chunk,
    _send_message,
    _start_core,
    _wait_for_task,
    _wait_pending_approval,
)


async def api(client: httpx.AsyncClient, method: str, path: str, status: int = 200, **kwargs):
    response = await client.request(method, API + path, **kwargs)
    assert response.status_code == status, (method, path, response.status_code, response.text)
    return response.json() if response.content else None


async def finished(client: httpx.AsyncClient, cid: str, content: str):
    sent = await _send_message(client, cid, content)
    task = await _wait_for_task(client, sent["task_id"], {"succeeded", "failed", "cancelled"})
    assert task["status"] == "succeeded", task
    return sent, task


@dataclass
class Product:
    root: Path
    model_url: str
    requests: list[dict[str, Any]]
    waiting: asyncio.Event
    disconnected: asyncio.Event
    release: asyncio.Event
    processes: list[asyncio.subprocess.Process] = field(default_factory=list)
    clients: list[httpx.AsyncClient] = field(default_factory=list)
    client: httpx.AsyncClient | None = None
    url: str = ""

    async def start(self) -> None:
        web_dir = os.environ.get("MAIGOKITE_ACCEPTANCE_WEB_DIR")
        process, self.url = await _start_core(
            self.root, _free_port(), self.model_url, web_dir=Path(web_dir) if web_dir else None,
        )
        self.processes.append(process)
        self.client = httpx.AsyncClient(base_url=self.url, timeout=25, trust_env=False)
        self.clients.append(self.client)

    async def stop(self) -> None:
        for process in self.processes:
            if process.returncode is not None:
                continue
            process.terminate()
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
            except TimeoutError:
                process.kill()
                await process.communicate()
                raise AssertionError("Core did not finish shutdown") from None
            (self.root / f"core-{process.pid}.log").write_bytes(stdout + stderr)
            # Uvicorn re-raises SIGTERM after its lifespan cleanup.
            assert process.returncode in {0, -signal.SIGTERM}, stderr.decode()
            assert b"Application shutdown complete." in stderr, stderr.decode()


@pytest_asyncio.fixture
async def product(tmp_path: Path):
    requests: list[dict[str, Any]] = []
    waiting, disconnected, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def completion(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests.append(body)
        request_id = len(requests)
        messages = body["messages"]
        human = max(index for index, message in enumerate(messages) if message["role"] == "user")
        prompt = str(messages[human]["content"])
        results = [message for message in messages[human + 1:] if message["role"] == "tool"]
        child = body["model"] == "acceptance-child"
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        try:
            if prompt == "WAIT" and not results:
                await response.write(_openai_chunk({"role": "assistant", "content": "BEGIN"}))
                waiting.set()
                while not release.is_set():
                    await response.write(b": waiting\n\n")
                    await asyncio.sleep(0.03)
            name, arguments = "", {}
            if child and not results:
                name, arguments = "file_read", {"path": "evidence.txt"}
            elif prompt == "DENY_THEN_READ" and len(results) < 2:
                name, arguments = (("file_write", {"path": "denied.txt", "content": "must not exist"})
                                   if not results else ("file_read", {"path": "evidence.txt"}))
            elif prompt.startswith("DELEGATE ") and not results:
                name, arguments = "delegate", {
                    "task": "Read evidence.txt and report its evidence.", "label": "reader",
                    "model_id": prompt.split(" ", 1)[1],
                }
            elif prompt == "READ" and not results:
                name, arguments = "file_read", {"path": "evidence.txt"}
            if name:
                await response.write(_openai_chunk({"role": "assistant", "tool_calls": [{
                    "index": 0, "id": f"acceptance-call-{request_id}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }]}))
                await response.write(_openai_chunk({}, "tool_calls"))
            else:
                if child:
                    await response.write(_openai_chunk({"reasoning_content": "CHILD_REASONING"}))
                await response.write(_openai_chunk({"role": "assistant", "content":
                    "CHILD_FINAL: EVIDENCE_CANARY" if child else "ACCEPTANCE_OK"}, "stop"))
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
        except (ConnectionResetError, RuntimeError):
            disconnected.set()
        return response

    async def models(_request: web.Request) -> web.Response:
        return web.json_response({"data": [{"id": "process-recovery", "context_length": 32768}]})

    application = web.Application()
    application.router.add_post("/v1/chat/completions", completion)
    application.router.add_get("/v1/models", models)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    model_url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1"
    owner = Product(tmp_path, model_url, requests, waiting, disconnected, release)
    (tmp_path / "evidence.txt").write_text("EVIDENCE_CANARY", encoding="utf-8")
    try:
        await owner.start()
        yield owner
    finally:
        try:
            await owner.stop()
        finally:
            release.set()
            await asyncio.gather(*(client.aclose() for client in owner.clients))
            await runner.cleanup()


@pytest.mark.asyncio
async def test_chat_tool_loop_ws_reconnect_and_product_projection(product: Product) -> None:
    client = product.client
    conversation = await api(client, "POST", "/conversations", 201, json={"permission_mode": "read_only"})
    cid = conversation["id"]
    async with await _connect_events(product.url, cid) as socket:
        sent = await _send_message(client, cid, "READ")
        events = await _events_until(socket, lambda item: item["type"] == "task.succeeded")
    kinds = [item["type"] for item in events]
    assert kinds.index("tool.proposed") < kinds.index("tool.started") < kinds.index("tool.completed")
    assert kinds.index("tool.completed") < kinds.index("task.succeeded")
    assert "message.delta" in kinds and not any(kind in {"task.failed", "task.cancelled"} for kind in kinds)
    task = await _wait_for_task(client, sent["task_id"], {"succeeded"})
    history = await api(client, "GET", f"/conversations/{cid}/events/history", params={"after": 0})
    assert [item["event_id"] for item in history["events"]] == [item["event_id"] for item in events]
    messages = (await api(client, "GET", f"/conversations/{cid}/messages"))["messages"]
    assert messages[-1]["role"] == "assistant" and messages[-1]["content"] == "ACCEPTANCE_OK"
    snapshot = await api(client, "GET", f"/conversations/{cid}/snapshot")
    assert snapshot["tasks"][-1]["id"] == task["id"] and snapshot["tasks"][-1]["status"] == "succeeded"
    assert len(product.requests) == 2
    assert "EVIDENCE_CANARY" in json.dumps(product.requests[-1]["messages"])
    async with await _connect_events(product.url, cid):
        assert len(product.requests) == 2, "reconnecting a viewer must not rerun the task"


@pytest.mark.asyncio
async def test_denied_write_continues_with_an_allowed_read(product: Product) -> None:
    client = product.client
    conversation = await api(client, "POST", "/conversations", 201, json={"permission_mode": "read_only"})
    cid = conversation["id"]
    sent = await _send_message(client, cid, "DENY_THEN_READ")
    approval = await _wait_pending_approval(client, cid)
    assert approval["version"] == 1 and not (product.root / "denied.txt").exists()
    await api(client, "POST", f"/approvals/{approval['id']}/decision", 202, json={
        "decision": "deny", "version": approval["version"], "note": "Read the existing evidence instead.",
    })
    await _wait_for_task(client, sent["task_id"], {"succeeded"})
    assert not (product.root / "denied.txt").exists()
    assert len(product.requests) == 3
    assert "Read the existing evidence instead." in json.dumps(product.requests[1]["messages"])
    assert "EVIDENCE_CANARY" in json.dumps(product.requests[-1]["messages"])
    events = (await api(client, "GET", f"/conversations/{cid}/events/history", params={"after": 0}))["events"]
    assert any(event["type"] == "tool.failed" for event in events)
    assert any(event["type"] == "tool.completed" for event in events)
    assert [event["type"] for event in events].count("task.succeeded") == 1
    assert not any(event["type"] in {"task.cancelled", "task.failed"} for event in events)


@pytest.mark.asyncio
async def test_stop_stream_waits_for_disconnect_and_leaves_model_usable(product: Product) -> None:
    client = product.client
    conversation = await api(client, "POST", "/conversations", 201, json={"permission_mode": "read_only"})
    cid = conversation["id"]
    sent = await _send_message(client, cid, "WAIT")
    await asyncio.wait_for(product.waiting.wait(), 15)
    await api(client, "POST", f"/tasks/{sent['task_id']}/cancel")
    await _wait_for_task(client, sent["task_id"], {"cancelled"})
    await asyncio.wait_for(product.disconnected.wait(), 10)
    before = await api(client, "GET", f"/conversations/{cid}/events/history", params={"after": 0})
    await asyncio.sleep(0.15)
    after = await api(client, "GET", f"/conversations/{cid}/events/history", params={"after": 0})
    assert before == after, "no event may be emitted after cancellation finishes"
    assert [event["type"] for event in after["events"]].count("task.cancelled") == 1
    await finished(client, cid, "AFTER_CANCEL")
    assert len(product.requests) == 2


@pytest.mark.asyncio
async def test_search_runs_real_ripgrep_through_http_without_credentials(product: Product) -> None:
    client = product.client
    conversation = await api(client, "POST", "/conversations", 201, json={"permission_mode": "read_only"})
    cid = conversation["id"]
    (product.root / "credentials.json").write_text("SECRET_CANARY", encoding="utf-8")
    for tool, arguments in (("glob", {"pattern": "*.txt"}), ("grep", {"pattern": "CANARY"})):
        await finished(client, cid, f"/tool {tool} " + json.dumps(arguments))
        messages = (await api(client, "GET", f"/conversations/{cid}/messages"))["messages"]
        assert "evidence.txt" in messages[-1]["content"]
        assert "SECRET_CANARY" not in messages[-1]["content"]
    assert not product.requests, "explicit native search must not need a paid model request"
    assert not (await api(client, "GET", "/approvals", params={"conversation_id": cid}))["approvals"]


@pytest.mark.asyncio
async def test_selected_child_model_read_transcript_and_parent_reply_are_separate(product: Product) -> None:
    client = product.client
    model = await api(client, "POST", "/models", 201, json={
        "name": "Acceptance child", "provider": "openai_compatible", "model": "acceptance-child",
        "base_url": product.model_url,
    })
    conversation = await api(client, "POST", "/conversations", 201, json={
        "permission_mode": "read_only", "max_subagent_calls": 1, "subagent_model_ids": [model["id"]],
    })
    cid = conversation["id"]
    await finished(client, cid, "DELEGATE " + model["id"])
    events = (await api(client, "GET", f"/conversations/{cid}/events/history", params={"after": 0}))["events"]
    transcript = [event for event in events if event["type"] == "subagent.message"]
    assert transcript and any("CHILD_FINAL" in event["payload"]["content"] for event in transcript)
    assert any(event["type"] == "subagent.tool.completed" and "EVIDENCE_CANARY" in event["payload"]["output"]
               for event in events)
    messages = (await api(client, "GET", f"/conversations/{cid}/messages"))["messages"]
    assert messages[-1]["content"] == "ACCEPTANCE_OK" and "CHILD_FINAL" not in messages[-1]["content"]
    child_requests = [request for request in product.requests if request["model"] == "acceptance-child"]
    assert len(child_requests) == 2 and "EVIDENCE_CANARY" in json.dumps(child_requests[-1]["messages"])
    assert "file_write" not in {tool["function"]["name"] for tool in child_requests[0]["tools"]}


@pytest.mark.asyncio
async def test_mcp_skill_apply_failure_rollback_and_real_core_restart(product: Product) -> None:
    client = product.client
    initial = await api(client, "GET", "/extension-settings")
    fixture = Path(__file__).resolve().parents[2] / "python/tests/fixtures/mcp_echo_server.py"
    configuration = {**initial["config"], "mcp_servers": [{
        "namespace": "acceptance", "command": sys.executable, "args": ["-u", str(fixture)],
    }], "skills": [{"name": "acceptance", "content":
        "---\nname: acceptance\ndescription: Acceptance evidence\n---\nSKILL_CANARY"}]}
    saved = await api(client, "PUT", "/extension-settings", json=configuration)
    assert saved["reload_required"] and not any(row["namespace"] == "acceptance" for row in saved["servers"])
    active = await api(client, "POST", "/extension-settings/apply", json={"revision": saved["config"]["revision"]})
    assert not active["reload_required"]
    assert any(row["namespace"] == "acceptance" and row["status"] == "running" for row in active["servers"])
    conversation = await api(client, "POST", "/conversations", 201, json={"permission_mode": "danger_full_access"})
    await finished(client, conversation["id"], '/tool acceptance__echo {"text":"MCP_CANARY"}')
    messages = (await api(client, "GET", f"/conversations/{conversation['id']}/messages"))["messages"]
    assert "MCP_CANARY" in messages[-1]["content"]
    assert any(skill["name"] == "acceptance" for skill in (await api(client, "GET", "/skills"))["skills"])
    await finished(client, conversation["id"], '/tool skill {"action":"read","name":"acceptance"}')
    messages = (await api(client, "GET", f"/conversations/{conversation['id']}/messages"))["messages"]
    assert "SKILL_CANARY" in messages[-1]["content"]
    bad = await api(client, "PUT", "/extension-settings", json={**saved["config"], "mcp_servers": [{
        "namespace": "broken", "command": str(product.root / "missing-mcp-executable"),
    }]})
    await api(client, "POST", "/extension-settings/apply", 400, json={"revision": bad["config"]["revision"]})
    after = await api(client, "GET", "/extension-settings")
    assert after["applied_revision"] == active["applied_revision"] and after["reload_required"]
    await finished(client, conversation["id"], '/tool acceptance__echo {"text":"STILL_HEALTHY"}')
    await product.stop()
    await product.start()
    restarted = await api(product.client, "GET", "/extension-settings")
    assert restarted["applied_revision"] == active["applied_revision"] and restarted["reload_required"]
    await finished(product.client, conversation["id"], '/tool acceptance__echo {"text":"AFTER_RESTART"}')
    messages = (await api(product.client, "GET", f"/conversations/{conversation['id']}/messages"))["messages"]
    assert "AFTER_RESTART" in messages[-1]["content"]


@pytest.mark.asyncio
async def test_memory_switches_forget_checkpoint_and_conversation_fork(product: Product) -> None:
    client = product.client
    conv = await api(client, "POST", "/conversations", 201, json={"permission_mode": "read_only"})
    cid, canary = conv["id"], "MEMORY_PRIVATE_CANARY"
    memory = await api(client, "POST", "/memories", 201, json={
        "folder_id": "", "category": "preference", "content": canary,
    })
    await finished(client, cid, "MEMORY_OFF")
    assert canary not in json.dumps(product.requests[-1])
    await api(client, "PATCH", "/memory-settings", json={"folder_id": "", "enabled": True})
    await finished(client, cid, "CONVERSATION_OFF")
    assert canary not in json.dumps(product.requests[-1])
    await api(client, "PATCH", f"/conversations/{cid}", json={"memory_enabled": True})
    sent, _ = await finished(client, cid, "MEMORY_ON")
    assert canary in json.dumps(product.requests[-1])
    async with AsyncSqliteSaver.from_conn_string(str(product.root / "product.langgraph.sqlite")) as saver:
        checkpoint = await saver.aget_tuple({"configurable": {"thread_id": sent["task_id"]}})
        assert checkpoint and canary not in str(checkpoint.checkpoint["channel_values"].get("messages"))
    messages = (await api(client, "GET", f"/conversations/{cid}/messages"))["messages"]
    fork = await api(client, "POST", f"/conversations/{cid}/fork", 201,
                     json={"message_id": messages[-1]["id"]}, headers={"Idempotency-Key": "acceptance-fork"})
    assert not fork["memory_enabled"]
    copied = (await api(client, "GET", f"/conversations/{fork['id']}/messages"))["messages"]
    assert [message["content"] for message in copied] == [message["content"] for message in messages]
    assert not ({message["id"] for message in copied} & {message["id"] for message in messages})
    assert not (await api(client, "GET", "/tasks", params={"conversation_id": fork["id"]}))["tasks"]
    await finished(client, fork["id"], "FORK_OFF")
    assert canary not in json.dumps(product.requests[-1])
    await api(client, "DELETE", f"/memories/{memory['id']}", 204)
    await finished(client, cid, "AFTER_FORGET")
    assert canary not in json.dumps(product.requests[-1])


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="this direct argv fixture requires a native Windows acceptance run")
async def test_real_process_chinese_input_eof_stop_and_late_input(product: Product) -> None:
    client = product.client
    conv = await api(client, "POST", "/conversations", 201, json={"permission_mode": "danger_full_access"})
    cid = conv["id"]

    async def job_for(task_id: str, live: bool):
        for _ in range(500):
            jobs = (await api(client, "GET", "/jobs", params={"conversation_id": cid}))["jobs"]
            item = next((job for job in jobs if job["task_id"] == task_id and job["live"] == live), None)
            if item is not None:
                return item
            await asyncio.sleep(0.02)
        raise AssertionError(f"process for {task_id} did not reach live={live}")

    command = shlex.join([sys.executable, "-u", "-c",
        "import sys; print('READY',flush=True); print(sys.stdin.readline().strip(),flush=True); "
        "sys.stdin.read(); print('EOF',flush=True)"])
    sent = await _send_message(client, cid, "/tool exec_command " + json.dumps({
        "command": command, "shell": "direct", "interactive": True, "timeout": 30,
    }))
    job = await job_for(sent["task_id"], True)
    assert job["stdin_available"]
    await api(client, "POST", f"/jobs/{job['id']}/input", 204, json={"text": "中文_INPUT_CANARY\n", "eof": False})
    await api(client, "POST", f"/jobs/{job['id']}/input", 204, json={"text": "", "eof": True})
    exited = await job_for(sent["task_id"], False)
    assert "中文_INPUT_CANARY" in exited["log_tail"] and "EOF" in exited["log_tail"]
    assert not exited["stdin_available"]
    await _wait_for_task(client, sent["task_id"], {"succeeded"})
    await api(client, "POST", f"/jobs/{job['id']}/input", 409, json={"text": "late\n", "eof": False})
    slow = shlex.join([sys.executable, "-u", "-c", "import time; print('READY',flush=True); time.sleep(60)"])
    sent = await _send_message(client, cid, "/tool exec_command " + json.dumps({
        "command": slow, "shell": "direct", "interactive": True, "timeout": 30,
    }))
    job = await job_for(sent["task_id"], True)
    await api(client, "POST", f"/jobs/{job['id']}/stop", 204)
    assert not (await job_for(sent["task_id"], False))["live"]
    await api(client, "POST", f"/jobs/{job['id']}/input", 409, json={"text": "late\n", "eof": False})
    assert not product.requests
