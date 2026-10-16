"""Per-run UI projection; execution and resume still read LangGraph checkpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from agent.data_contracts import JSONObject
from agent.event_contracts import event_payload
from agent.models.reasoning import ThinkTagStream, message_text, raw_message_text, reasoning_text, split_thinking
from agent.repository import SQLiteRepository
from agent.structured_output import OUTPUT_TOOL_NAME

if TYPE_CHECKING:
    from agent.runtime.langgraph_runtime import RuntimeRunRequest


@dataclass(frozen=True, slots=True)
class MaigoKiteEvent:
    """Stable UI data, independent of LangGraph stream event types."""

    type: str
    payload: JSONObject


EventPublisher = Callable[[str, Mapping[str, object]], Awaitable[None]]
END_OF_EVENTS = object()


async def publish_event(
    repository: SQLiteRepository,
    publisher: EventPublisher | None,
    request: RuntimeRunRequest,
    event_type: str,
    payload: Mapping[str, object],
) -> JSONObject:
    base = event_payload(event_type, {
        "task_id": request.task_id,
        "conversation_id": request.conversation_id,
        **payload,
    })
    if publisher is not None:
        await publisher(event_type, base)
        return base
    return await repository.append_task_event(event_type, base)


class RunProjection:
    """Own delivery deduplication and text parsing for one active event stream.

    Terminal delivery is not task status: Repository owns the business outcome.
    These transient receipts prevent repeated graph updates from duplicating UI
    output; they neither authorize tool execution nor decide checkpoint resume.
    """

    def __init__(
        self,
        repository: SQLiteRepository,
        publisher: EventPublisher | None,
        request: RuntimeRunRequest,
        queue: asyncio.Queue[MaigoKiteEvent | object],
    ) -> None:
        self.repository = repository
        self.publisher = publisher
        self.request = request
        self.queue = queue
        self._event_lock = asyncio.Lock()
        self.terminal_delivered = False
        self.projected_tool_calls: set[str] = set()
        self._final_saved = False
        self._reasoning_parts: list[str] = []
        self._reasoning_open = False
        self._provider_reasoning_streamed = False
        self._text_stream = ThinkTagStream()

    async def emit(self, event_type: str, payload: Mapping[str, object]) -> None:
        async with self._event_lock:
            if self.terminal_delivered:
                return
            enriched = await publish_event(self.repository, self.publisher, self.request, event_type, payload)
            if event_type in {"task.cancelled", "task.failed", "task.succeeded", "task.interrupted"}:
                self.terminal_delivered = True
            await self.queue.put(MaigoKiteEvent(event_type, enriched))

    async def stream_message(self, message: AIMessageChunk) -> None:
        if reasoning := reasoning_text(message):
            self._provider_reasoning_streamed = True
            await self._append_reasoning(reasoning)
        await self._text_delta(raw_message_text(message))

    async def model_message(self, message: AIMessage) -> None:
        # The final update repeats streamed text. Only non-streamed reasoning
        # and the persisted final answer need projection here.
        if not self._provider_reasoning_streamed and (reasoning := reasoning_text(message)):
            await self._append_reasoning(reasoning)
        await self._text_delta("", final=True)
        if not self._text_stream.reasoning_emitted:
            _, reasoning = split_thinking(raw_message_text(message))
            if reasoning:
                await self._append_reasoning(reasoning, separate=True)
        self._text_stream = ThinkTagStream()
        self._provider_reasoning_streamed = False
        await self.complete_reasoning()
        content = message_text(message)
        if not message.tool_calls and content and not self._final_saved and self.request.output_schema is None:
            saved = await self.repository.append_message(self.request.conversation_id, "assistant", content)
            await self.emit("message.completed", {"content": content, "message_id": saved["id"]})
            self._final_saved = True

    async def tool_message(self, message: ToolMessage) -> None:
        if self.request.output_schema is not None and message.name == OUTPUT_TOOL_NAME:
            return
        call_id = str(message.tool_call_id or "")
        if not call_id or call_id in self.projected_tool_calls:
            return
        failed = message.status == "error"
        content = message_text(message)
        product = message.artifact if isinstance(message.artifact, dict) else {}
        summary = str(product.get("summary") or content)[:500]
        stored = await self.repository.get_tool_call(call_id)
        tool_name = str(message.name or stored["tool_name"])
        status = "failed" if failed else "succeeded"
        if failed and stored["status"] == "denied":
            status = "denied"
        error_code = product.get("error_code")
        if status == "denied" and not error_code:
            error_code = "APPROVAL_REJECTED"
        policy_denied = status == "denied" and error_code == "TOOL_DENIED"
        await self.repository.update_tool_call(
            call_id, status, result=product.get("data", {"content": content}), summary=summary,
        )
        await self.repository.record_audit(
            self.request.workspace_id,
            self.request.principal_id,
            "tool.denied" if policy_denied else "tool.failed" if failed else "tool.completed",
            tool_name,
            "denied" if status == "denied" else "failed" if failed else "success",
            {"tool_call_id": call_id, "risk": stored["risk_level"], "summary": summary},
        )
        await self.emit("tool.failed" if failed else "tool.completed", {
            "tool_call_id": call_id,
            "tool_name": tool_name,
            **({"message": content} if not product else {}),
            "summary": summary,
            "data": product.get("data", {"content": content}),
            "result": product.get("data", {"content": content}),
            "error_code": error_code,
            **({"denied": True} if status == "denied" else {}),
            "user_hint": (
                "该操作已驳回，未执行；继续处理任务的其他部分。"
                if error_code == "APPROVAL_REJECTED" else product.get("user_hint")
            ),
            "action": product.get("action"),
        })
        self.projected_tool_calls.add(call_id)
        change = product.get("file_change")
        if not failed and isinstance(change, dict):
            await self.emit("file.changed", {**change, "tool_call_id": call_id})

    async def _text_delta(self, text: str, *, final: bool = False) -> None:
        separate = not self._text_stream.reasoning_emitted
        answer, reasoning = self._text_stream.feed(text, final=final)
        if reasoning:
            await self._append_reasoning(reasoning, separate=separate)
        if answer:
            await self.emit("message.delta", {"content": answer})

    async def _append_reasoning(self, text: str, *, separate: bool = False) -> None:
        if not self._reasoning_open:
            await self.emit("reasoning.started", {})
            if self._reasoning_parts:
                text = "\n\n" + text
            self._reasoning_open = True
        elif separate:
            text = "\n\n" + text
        self._reasoning_parts.append(text)
        await self.emit("reasoning.delta", {"content": text})

    async def complete_reasoning(self) -> None:
        if self._reasoning_open:
            await self.emit("reasoning.completed", {"summary": "".join(self._reasoning_parts)})
            self._reasoning_open = False
