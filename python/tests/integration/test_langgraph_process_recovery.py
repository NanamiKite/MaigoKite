from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest
import websockets
from aiohttp import web

API = "/api/v1/workspaces/personal"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _openai_chunk(delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
    body = {
        "id": "chatcmpl-process-recovery",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "process-recovery",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(body)}\n\n".encode()


async def _start_model_server(*, two_writes: bool = False) -> tuple[web.AppRunner, str, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []

    async def completion(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests.append(body)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        if any(message.get("role") == "tool" for message in body["messages"]):
            await response.write(_openai_chunk({"role": "assistant", "content": "PROCESS_RESUME_OK"}, "stop"))
        else:
            tool_calls = [
                {
                    "index": index,
                    "id": f"call-process-write-{index}",
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "arguments": json.dumps({
                            "path": "process-recovery.txt" if index == 0 else "second.txt",
                            "content": "written once",
                        }),
                    },
                }
                for index in range(2 if two_writes else 1)
            ]
            await response.write(_openai_chunk({"role": "assistant", "tool_calls": tool_calls}))
            await response.write(_openai_chunk({}, "tool_calls"))
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    application = web.Application()
    application.router.add_post("/v1/chat/completions", completion)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = int(site._server.sockets[0].getsockname()[1])
    return runner, f"http://127.0.0.1:{port}/v1", requests


async def _start_core(
    tmp_path: Path, port: int, model_base_url: str, *, barrier: str = "", observe_writes: bool = False,
    web_dir: Path | None = None,
) -> tuple[asyncio.subprocess.Process, str]:
    python_root = Path(__file__).resolve().parents[2]
    repository_root = python_root.parent
    environment = os.environ.copy()
    for name in list(environment):
        if name.startswith("MAIGOKITE_"):
            environment.pop(name)
    # A developer's shell may contain paid provider or tracing credentials.
    # Child Core processes must use only this test's loopback model service.
    for name in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_COMPATIBLE_API_KEY", "AZURE_OPENAI_API_KEY",
        "DEEPSEEK_API_KEY", "GOOGLE_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY",
    ):
        environment.pop(name, None)
    environment.update({
        "PYTHONPATH": str(python_root),
        "MAIGOKITE_DB_PATH": str(tmp_path / "product.sqlite"),
        "MAIGOKITE_WORKSPACE_ROOT": str(tmp_path),
        "MAIGOKITE_PERMISSION_MODE": "read_only",
        "MAIGOKITE_MODEL_PROVIDER": "openai_compatible",
        "MAIGOKITE_MODEL_NAME": "process-recovery",
        "MAIGOKITE_MODEL_BASE_URL": model_base_url,
        "NO_PROXY": "127.0.0.1,localhost",
        "MAIGOKITE_TEST_WRITE_BARRIER": barrier,
        "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
    })
    entry = ([str(Path(__file__).with_name("runtime_recovery_worker.py"))]
             if barrier or observe_writes else ["-m", "agent.main"])
    arguments = [sys.executable, *entry, "run", "--host", "127.0.0.1", "--port", str(port)]
    if web_dir is not None:
        arguments.extend(["--web-dir", str(web_dir)])
    process = await asyncio.create_subprocess_exec(
        *arguments,
        cwd=repository_root, env=environment,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(base_url=base_url, timeout=1) as client:
        for _ in range(300):
            if process.returncode is not None:
                stdout, stderr = await process.communicate()
                raise AssertionError(f"Core exited before HTTP became ready:\n{stdout.decode()}\n{stderr.decode()}")
            try:
                response = await client.get("/health")
                if response.status_code == 200 and response.json().get("agent_core") == "serving":
                    return process, base_url
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
    process.kill()
    stdout, stderr = await process.communicate()
    raise AssertionError(f"Core failed to serve HTTP:\n{stdout.decode()}\n{stderr.decode()}")


async def _crash_core(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    await asyncio.wait_for(process.wait(), timeout=5)


async def _connect_events(base_url: str, conversation_id: str):
    url = base_url.replace("http://", "ws://", 1) + f"{API}/events?conversation_id={quote(conversation_id)}"
    socket = await websockets.connect(url, open_timeout=15, close_timeout=2)
    ready = json.loads(await asyncio.wait_for(socket.recv(), timeout=5))
    assert ready["type"] == "stream.ready"
    return socket


async def _events_until(socket, predicate, timeout: float = 20) -> list[dict[str, Any]]:
    async def receive() -> list[dict[str, Any]]:
        events = []
        while True:
            event = json.loads(await socket.recv())
            if event["type"] == "stream.ready":
                continue
            events.append(event)
            if predicate(event):
                return events

    return await asyncio.wait_for(receive(), timeout=timeout)


async def _create_conversation(client: httpx.AsyncClient, title: str) -> dict[str, Any]:
    response = await client.post(f"{API}/conversations", json={
        "title": title, "permission_mode": "read_only",
    })
    assert response.status_code == 201, response.text
    return response.json()


async def _pending_approvals(client: httpx.AsyncClient, conversation_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"{API}/approvals", params={"conversation_id": conversation_id})
    assert response.status_code == 200, response.text
    return [item for item in response.json()["approvals"] if item["status"] == "pending"]


async def _wait_pending_approval(client: httpx.AsyncClient, conversation_id: str) -> dict[str, Any]:
    for _ in range(500):
        approvals = await _pending_approvals(client, conversation_id)
        if approvals:
            return approvals[0]
        await asyncio.sleep(0.02)
    raise AssertionError("Core did not persist the expected pending approval")


async def _wait_for_task(client: httpx.AsyncClient, task_id: str, statuses: set[str]) -> dict[str, Any]:
    for _ in range(500):
        response = await client.get(f"{API}/tasks/{task_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if task["status"] in statuses:
            return task
        await asyncio.sleep(0.02)
    raise AssertionError(f"Task {task_id} did not reach one of {sorted(statuses)}")


async def _send_message(client: httpx.AsyncClient, conversation_id: str, content: str) -> dict[str, Any]:
    response = await client.post(f"{API}/conversations/{conversation_id}/messages", json={"content": content})
    assert response.status_code == 202, response.text
    return response.json()


async def _approve(client: httpx.AsyncClient, approval: dict[str, Any]) -> None:
    response = await client.post(f"{API}/approvals/{approval['id']}/decision", json={
        "decision": "allow", "version": approval["version"], "scope": "once",
    })
    assert response.status_code == 202, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("partial_batch", [False, True])
async def test_approval_survives_real_core_process_restart(tmp_path: Path, partial_batch: bool) -> None:
    model_runner, model_base_url, model_requests = await _start_model_server(two_writes=partial_batch)
    port = _free_port()
    first_process = second_process = None
    first_socket = second_socket = None
    try:
        first_process, first_url = await _start_core(tmp_path, port, model_base_url)
        async with httpx.AsyncClient(base_url=first_url, timeout=20) as first:
            conversation = await _create_conversation(first, "process recovery")
            conversation_id = conversation["id"]
            first_socket = await _connect_events(first_url, conversation_id)
            sent = await _send_message(first, conversation_id, "write the recovery marker")
            task_id = sent["task_id"]
            before_restart = await _events_until(first_socket, lambda event: event["type"] == "approval.requested")
            assert "task.waiting_approval" in [event["type"] for event in before_restart]
            assert not (tmp_path / "process-recovery.txt").exists()
            approval = await _wait_pending_approval(first, conversation_id)
            assert approval["version"] == 1
            first_approval = approval
            if partial_batch:
                await _approve(first, approval)
                await _events_until(first_socket, lambda event:
                                    event["type"] == "approval.requested"
                                    and event["payload"].get("approval_id") != first_approval["id"])
                approval = await _wait_pending_approval(first, conversation_id)
                assert approval["id"] != first_approval["id"] and approval["version"] == 1
                assert not (tmp_path / "process-recovery.txt").exists()
                assert not (tmp_path / "second.txt").exists()
            await first_socket.close()
            first_socket = None
            await _crash_core(first_process)
            first_process = None

        second_process, second_url = await _start_core(tmp_path, port, model_base_url)
        async with httpx.AsyncClient(base_url=second_url, timeout=20) as second:
            second_socket = await _connect_events(second_url, conversation_id)
            await _approve(second, approval)
            after_restart = await _events_until(second_socket, lambda event: event["type"] == "task.succeeded")
            kinds = [event["type"] for event in after_restart]
            assert kinds[0] == "approval.decided"
            assert kinds.count("tool.started") == (2 if partial_batch else 1)
            assert kinds.count("tool.completed") == (2 if partial_batch else 1)
            assert (tmp_path / "process-recovery.txt").read_text(encoding="utf-8") == "written once"
            if partial_batch:
                assert (tmp_path / "second.txt").read_text(encoding="utf-8") == "written once"

            task = (await second.get(f"{API}/tasks/{task_id}")).json()
            assert task["status"] == "succeeded"
            history_response = await second.get(
                f"{API}/conversations/{conversation_id}/events/history", params={"after": 0},
            )
            assert history_response.status_code == 200, history_response.text
            history = history_response.json()["events"]
            assert [item["sequence"] for item in history] == sorted(item["sequence"] for item in history)
            assert [item["type"] for item in history].count("tool.started") == (2 if partial_batch else 1)
            assert len(model_requests) == 2

            with sqlite3.connect(tmp_path / "product.sqlite") as database:
                continuation = database.execute(
                    "SELECT continuation_json FROM tool_calls WHERE id=?", (approval["tool_call_id"],),
                ).fetchone()
                stored_approval = database.execute(
                    "SELECT arguments_hash,status,version FROM approvals WHERE id=?", (approval["id"],),
                ).fetchone()
                if partial_batch:
                    assert database.execute(
                        "SELECT status,version FROM approvals WHERE id=?", (first_approval["id"],),
                    ).fetchone() == ("consumed", 2)
            assert continuation == ("{}",)
            assert stored_approval is not None and stored_approval[0]
            assert stored_approval[1:] == ("consumed", 2)
            await second_socket.close()
            second_socket = None
    finally:
        if first_socket is not None:
            await first_socket.close()
        if second_socket is not None:
            await second_socket.close()
        if first_process is not None:
            await _crash_core(first_process)
        if second_process is not None:
            await _crash_core(second_process)
        await model_runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_point", ["before", "after"])
async def test_file_write_survives_core_process_crash(tmp_path: Path, crash_point: str) -> None:
    model_runner, model_url, model_requests = await _start_model_server()
    port = _free_port()
    process = restarted = None
    event_socket = None
    try:
        process, base_url = await _start_core(tmp_path, port, model_url, barrier=crash_point)
        async with httpx.AsyncClient(base_url=base_url, timeout=20) as client:
            conversation = await _create_conversation(client, "file crash recovery")
            conversation_id = conversation["id"]
            event_socket = await _connect_events(base_url, conversation_id)
            sent = await _send_message(client, conversation_id, "write a file")
            task_id = sent["task_id"]
            before = await _events_until(event_socket, lambda event: event["type"] == "approval.requested")
            assert before[-1]["type"] == "approval.requested"
            approval = await _wait_pending_approval(client, conversation_id)
            target = tmp_path / "process-recovery.txt"
            assert not target.exists()
            expected_hash = hashlib.sha256(json.dumps(
                approval["arguments"], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            await _approve(client, approval)

            async def wait_for_barrier() -> None:
                while not (tmp_path / "write-barrier").exists():
                    if process.returncode is not None:
                        raise AssertionError("Core exited before reaching the file-write barrier")
                    await asyncio.sleep(0.02)

            await asyncio.wait_for(wait_for_barrier(), timeout=10)
            if crash_point == "before":
                assert not target.exists()
                mtime = None
            else:
                assert target.read_bytes() == b"written once"
                mtime = target.stat().st_mtime_ns
            with sqlite3.connect(tmp_path / "product.sqlite") as database:
                assert database.execute(
                    "SELECT status,version,arguments_hash FROM approvals WHERE id=?", (approval["id"],),
                ).fetchone() == ("consumed", 2, expected_hash)
                assert database.execute(
                    "SELECT status FROM tool_calls WHERE id=?", (approval["tool_call_id"],),
                ).fetchone() == ("running",)
            await event_socket.close()
            event_socket = None
            await _crash_core(process)
            assert process.returncode != 0
            process = None

        restarted, restarted_url = await _start_core(tmp_path, port, model_url, observe_writes=True)
        async with httpx.AsyncClient(base_url=restarted_url, timeout=20) as client:
            event_socket = await _connect_events(restarted_url, conversation_id)
            task_response = await client.get(f"{API}/tasks/{task_id}")
            assert task_response.status_code == 200, task_response.text
            task = task_response.json()
            assert task["status"] == "interrupted"
            stale = await client.post(f"{API}/tasks/{task_id}/resume", json={"version": task["version"] - 1})
            assert stale.status_code == 409
            assert stale.json()["error"]["code"] == "TASK_NOT_RESUMABLE"

            resumed = await client.post(f"{API}/tasks/{task_id}/resume", json={"version": task["version"]})
            assert resumed.status_code == 202, resumed.text
            resumed_events = await _events_until(event_socket, lambda event: event["type"] == "task.succeeded")
            assert resumed_events[0]["type"] == "task.resumed"
            assert all(event["event_id"] and event["sequence"] > 0 for event in resumed_events)
            repeated = await client.post(f"{API}/tasks/{task_id}/resume", json={"version": task["version"]})
            assert repeated.status_code == 409

            completed = (await client.get(f"{API}/tasks/{task_id}")).json()
            assert completed["status"] == "succeeded", completed
            assert target.read_bytes() == b"written once"
            receipt_sizes = [int(value) for value in (tmp_path / "write-receipts").read_text().splitlines()]
            assert receipt_sizes == ([12] if crash_point == "before" else [12, 0])
            if mtime is not None:
                assert target.stat().st_mtime_ns == mtime

            history_response = await client.get(
                f"{API}/conversations/{conversation_id}/events/history", params={"after": 0},
            )
            assert history_response.status_code == 200, history_response.text
            history = history_response.json()["events"]
            kinds = [event["type"] for event in history]
            assert kinds.count("task.resumed") == 1
            persisted = {event["event_id"]: event for event in history}
            for event in resumed_events:
                record = persisted[event["event_id"]]
                assert record["sequence"] == event["sequence"]
                assert record["occurred_at"] == event["occurred_at"]
                live_payload = dict(event["payload"])
                durable_payload = dict(record["payload"])
                for identity in ("event_id", "sequence", "occurred_at"):
                    live_payload.pop(identity, None)
                    durable_payload.pop(identity, None)
                assert durable_payload == live_payload
            assert kinds.count("tool.started") == 2  # At-least-once invocation is visible.
            assert kinds.count("tool.completed") == 1
            assert kinds.count("task.succeeded") == 1 and kinds[-1] == "task.succeeded"
            assert "task.failed" not in kinds and "task.cancelled" not in kinds
            assert len({event["event_id"] for event in history}) == len(history)
            assert [event["sequence"] for event in history] == sorted(event["sequence"] for event in history)
            completed_tool = next(event for event in history if event["type"] == "tool.completed")
            result = completed_tool["payload"]["result"]
            assert bool(result.get("reconciled")) == (crash_point == "after")
            assert len(model_requests) == 2
            assert model_requests[-1]["messages"][-1]["role"] == "tool"
            with sqlite3.connect(tmp_path / "product.sqlite") as database:
                assert database.execute(
                    "SELECT status,version,arguments_hash FROM approvals WHERE id=?", (approval["id"],),
                ).fetchone() == ("consumed", 2, expected_hash)
                assert database.execute(
                    "SELECT status,continuation_json FROM tool_calls WHERE id=?", (approval["tool_call_id"],),
                ).fetchone() == ("succeeded", "{}")
                assert database.execute(
                    "SELECT content FROM messages WHERE conversation_id=? AND role='assistant'", (conversation_id,),
                ).fetchall() == [("PROCESS_RESUME_OK",)]
            await event_socket.close()
            event_socket = None
    finally:
        if event_socket is not None:
            await event_socket.close()
        if process is not None:
            await _crash_core(process)
        if restarted is not None:
            await _crash_core(restarted)
        await model_runner.cleanup()
