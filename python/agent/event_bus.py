"""Small asynchronous event bus used inside Agent Core."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping

from agent.data_contracts import JSONObject
from agent.event_contracts import event_payload

EventCallback = Callable[[str, JSONObject], Awaitable[None]]
EventSink = Callable[[str, Mapping[str, object]], Awaitable[JSONObject]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[EventCallback]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._sink: EventSink | None = None

    def set_sink(self, sink: EventSink | None) -> None:
        """Set the durable sink that enriches events before live delivery."""
        self._sink = sink

    def subscribe(self, event_type: str, callback: EventCallback) -> None:
        self._subscribers[event_type].add(callback)

    def unsubscribe(self, event_type: str, callback: EventCallback) -> None:
        callbacks = self._subscribers.get(event_type)
        if callbacks:
            callbacks.discard(callback)
            if not callbacks:
                self._subscribers.pop(event_type, None)

    async def publish(self, event_type: str, payload: Mapping[str, object]) -> None:
        delivered = await self._sink(event_type, payload) if self._sink else event_payload(event_type, payload)
        async with self._lock:
            callbacks = tuple(self._subscribers.get(event_type, ())) + tuple(self._subscribers.get("*", ()))
        if callbacks:
            await asyncio.gather(*(cb(event_type, delivered) for cb in callbacks))

    def clear(self) -> None:
        self._subscribers.clear()
        self._sink = None
