from __future__ import annotations

import asyncio

import pytest
from agent.api.execution import Execution


@pytest.mark.asyncio
async def test_websocket_stream_sends_ready_then_only_matching_conversation_events(core) -> None:
    conversation = await core.create_conversation(title="websocket stream")

    class Socket:
        def __init__(self) -> None:
            self.accepted = False
            self.sent = []
            self.closed_with = None
            self.disconnect = asyncio.Event()

        async def accept(self) -> None:
            self.accepted = True

        async def send_json(self, value) -> None:
            self.sent.append(value)
            if value["type"] == "stream.ready":
                await core.event_bus.publish("message.delta", {
                    "event_id": "event-other", "sequence": 1, "occurred_at": "2026-01-01T00:00:00Z",
                    "conversation_id": "another-conversation", "task_id": "other-task", "content": "ignore",
                })
                await core.event_bus.publish("message.delta", {
                    "event_id": "event-current", "sequence": 2, "occurred_at": "2026-01-01T00:00:01Z",
                    "conversation_id": conversation["id"], "task_id": "current-task", "content": "forward",
                })
            else:
                self.disconnect.set()

        async def receive(self):
            await self.disconnect.wait()
            return {"type": "websocket.disconnect"}

        async def close(self, code: int = 1000) -> None:
            self.closed_with = code

    socket = Socket()
    await Execution(core).websocket(socket, "personal", conversation["id"])
    assert socket.accepted
    assert [value["type"] for value in socket.sent] == ["stream.ready", "message.delta"]
    assert socket.sent[1]["event_id"] == "event-current"
    assert socket.sent[1]["payload"]["content"] == "forward"
    assert socket.closed_with == 1000


@pytest.mark.asyncio
async def test_missing_historical_checkpoint_is_not_recoverable(core) -> None:
    conversation = await core.create_conversation(title="historical interrupted task")
    await core.repository.create_task_state(
        "old-task", "personal", conversation["id"], "historical", "interrupted", "old input",
    )
    task = await core.get_task("personal", "old-task")
    with pytest.raises(ValueError, match="no execution checkpoint"):
        await core.resume_task("personal", "old-task", "local-user", task["version"])
