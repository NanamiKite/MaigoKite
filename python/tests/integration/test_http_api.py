from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from agent.api.app import create_app

API = "/api/v1/workspaces/personal"


@pytest.fixture
async def api_client(core):
    app = create_app(core)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 49001))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        yield client


async def _wait_for_task(client: httpx.AsyncClient, task_id: str, terminal: set[str]) -> dict:
    for _ in range(500):
        response = await client.get(f"{API}/tasks/{task_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if task["status"] in terminal:
            return task
        await asyncio.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach a terminal state")


@pytest.mark.asyncio
async def test_http_conversation_send_and_durable_event_projection(api_client):
    created = await api_client.post(f"{API}/conversations", json={"title": "HTTP API"})
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    sent = await api_client.post(
        f"{API}/conversations/{conversation_id}/messages", json={"content": "ping"},
    )
    assert sent.status_code == 202, sent.text
    task_id = sent.json()["task_id"]
    task = await _wait_for_task(api_client, task_id, {"succeeded", "failed", "cancelled"})
    assert task["status"] == "succeeded", task

    history = await api_client.get(f"{API}/conversations/{conversation_id}/events/history")
    assert history.status_code == 200, history.text
    events = history.json()["events"]
    assert [item["sequence"] for item in events] == sorted(item["sequence"] for item in events)
    assert all(item["event_id"] and item["occurred_at"] for item in events)
    assert any(item["type"] == "message.delta" and "reply:ping" in item["payload"].get("content", "")
               for item in events)
    assert events[-1]["type"] == "task.succeeded"

    messages = await api_client.get(f"{API}/conversations/{conversation_id}/messages")
    assert [item["role"] for item in messages.json()["messages"]] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_http_approval_resumes_file_write_and_projects_completion(api_client, tmp_path):
    created = await api_client.post(
        f"{API}/conversations",
        json={"title": "HTTP approval", "permission_mode": "read_only"},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    command = "/tool file_write " + json.dumps({"path": "http-api.txt", "content": "approved"})
    sent = await api_client.post(
        f"{API}/conversations/{conversation_id}/messages", json={"content": command},
    )
    assert sent.status_code == 202, sent.text
    task_id = sent.json()["task_id"]

    approval = None
    for _ in range(500):
        response = await api_client.get(f"{API}/approvals", params={"conversation_id": conversation_id})
        assert response.status_code == 200, response.text
        approval = next((item for item in response.json()["approvals"] if item["status"] == "pending"), None)
        if approval is not None:
            break
        await asyncio.sleep(0.01)
    assert approval is not None, "the HTTP chat path did not create a pending approval"
    assert not (tmp_path / "http-api.txt").exists()

    decided = await api_client.post(
        f"{API}/approvals/{approval['id']}/decision",
        json={"decision": "allow", "version": approval["version"], "scope": "once"},
    )
    assert decided.status_code == 202, decided.text
    assert decided.json()["task_id"] == task_id
    task = await _wait_for_task(api_client, task_id, {"succeeded", "failed", "cancelled"})
    assert task["status"] == "succeeded", task
    assert (tmp_path / "http-api.txt").read_text(encoding="utf-8") == "approved"

    history = await api_client.get(f"{API}/conversations/{conversation_id}/events/history")
    kinds = [item["type"] for item in history.json()["events"]]
    assert "approval.requested" in kinds
    assert "approval.decided" in kinds
    assert "tool.completed" in kinds
    assert kinds[-1] == "task.succeeded"
