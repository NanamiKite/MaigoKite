"""Own HTTP-started coroutines and bounded live subscriptions until application shutdown."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect

from agent.api.contracts import APIError, event
from agent.data_contracts import JSONObject, RunResult

if TYPE_CHECKING:
    from agent.core import AgentCore

logger = logging.getLogger(__name__)
_admission = ContextVar[object | None]("http_admission", default=None)


class Execution:
    def __init__(self, core: AgentCore) -> None:
        self.core = core
        self.bus = core.event_bus
        self.tasks: set[asyncio.Task[object]] = set()
        self.closing = False

    def own[T](self, operation: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        if self.closing:
            operation.close()
            raise APIError(503, "STOPPING", "应用正在退出，请稍后重新连接。")
        task = asyncio.create_task(operation)
        self.tasks.add(task)

        def release(done: asyncio.Task[object]) -> None:
            self.tasks.discard(done)
            if not done.cancelled() and (error := done.exception()) is not None:
                logger.warning("Background operation ended: %s", type(error).__name__)

        task.add_done_callback(release)
        return task

    async def admit(self, operation: Coroutine[Any, Any, RunResult],
                    matches: Callable[[str, JSONObject], bool]) -> dict[str, object]:
        """Return a durable acceptance or replay receipt, leaving execution owned by this service."""
        receipt: asyncio.Future[JSONObject] = asyncio.get_running_loop().create_future()
        identity = object()

        async def receive(kind: str, payload: JSONObject) -> None:
            if (
                _admission.get() is identity
                and not receipt.done()
                and payload.get("event_id")
                and matches(kind, payload)
            ):
                receipt.set_result(payload)

        self.bus.subscribe("*", receive)
        try:
            # asyncio propagates this correlation into Core's tasks and event callbacks.
            # Concurrent decisions on the same approval cannot claim each other's acceptance.
            token = _admission.set(identity)
            try:
                running = self.own(operation)
            finally:
                _admission.reset(token)
            pending: set[asyncio.Task[RunResult] | asyncio.Future[JSONObject]] = {running, receipt}
            await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if running.done() and not running.cancelled() and running.exception() is not None:
                running.result()
            if receipt.done():
                return dict(receipt.result())
            result = running.result()
            if result.get("replayed"):
                return dict(result)
            raise APIError(502, str(result.get("error_code") or "ADMISSION_FAILED"),
                           str(result.get("error") or "任务没有返回接纳回执，请刷新任务状态。"))
        finally:
            self.bus.unsubscribe("*", receive)
            receipt.cancel()

    async def close(self) -> None:
        self.closing = True
        active = tuple(self.tasks)
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    async def websocket(self, socket: WebSocket, workspace: str, conversation: str) -> None:
        await self.core.conversations.get_conversation(workspace, conversation)
        await socket.accept()
        queue: asyncio.Queue[JSONObject] = asyncio.Queue(maxsize=64)
        overflow = asyncio.Event()

        async def receive(kind: str, payload: JSONObject) -> None:
            # Conversation IDs are globally unique and were scoped to the workspace before accepting the socket.
            if payload.get("conversation_id") != conversation:
                return
            if overflow.is_set():
                return
            if not payload.get("event_id") or not payload.get("sequence") or not payload.get("occurred_at"):
                overflow.set()
                return
            try:
                queue.put_nowait(event(kind, payload).model_dump(mode="json", exclude_unset=True))
            except asyncio.QueueFull:
                # Reconnection replays the Repository journal. Never hold an Agent run behind socket I/O.
                overflow.set()
            except Exception as exc:
                # This event is already durable. A failed UI projection must
                # neither fail the Agent run nor leave the socket silently stale.
                logger.warning("Live event projection failed: %s", type(exc).__name__)
                overflow.set()

        async def send() -> None:
            while True:
                value = await queue.get()
                await asyncio.wait_for(socket.send_json(value), timeout=10)

        async def disconnected() -> None:
            while True:
                if (await socket.receive())["type"] == "websocket.disconnect":
                    return

        # No await between registration and the control frame: live events must follow stream.ready.
        queue.put_nowait({"type": "stream.ready", "payload": {}})
        self.bus.subscribe("*", receive)
        jobs = [asyncio.create_task(send()), asyncio.create_task(disconnected()), asyncio.create_task(overflow.wait())]
        try:
            done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
            for job in done:
                job.result()
        except (WebSocketDisconnect, OSError, TimeoutError):
            pass
        finally:
            self.bus.unsubscribe("*", receive)
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            with contextlib.suppress(OSError, RuntimeError, TimeoutError):
                await asyncio.wait_for(socket.close(code=1013 if overflow.is_set() else 1000), 1)
