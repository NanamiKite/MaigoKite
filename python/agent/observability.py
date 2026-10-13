"""Phase-one observability interfaces."""

from __future__ import annotations

import json
import logging
from typing import Any


class Observer:
    async def on_event(self, event_type: str, data: dict[str, Any]) -> None:
        raise NotImplementedError

    async def on_metric(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        raise NotImplementedError

    async def on_trace(self, trace_id: str, span_data: dict[str, Any]) -> None:
        raise NotImplementedError


class ConsoleObserver(Observer):
    async def on_event(self, event_type: str, data: dict[str, Any]) -> None:
        level = getattr(logging, str(data.get("level", "INFO")).upper(), logging.INFO)
        logging.getLogger("maigokite.agent").log(
            level, "%s %s", event_type, json.dumps(data, ensure_ascii=False, default=str)
        )

    async def on_metric(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        await self.on_event("metric", {"name": name, "value": value, "labels": labels or {}})

    async def on_trace(self, trace_id: str, span_data: dict[str, Any]) -> None:
        await self.on_event("trace", {"trace_id": trace_id, **span_data})


class Observability:
    def __init__(self) -> None:
        self._observers: list[Observer] = []

    def add_observer(self, observer: Observer) -> None:
        self._observers.append(observer)

    async def log(self, level: str, message: str, extra: dict[str, Any] | None = None) -> None:
        await self._notify("on_event", "log", {"level": level, "message": message, **(extra or {})})

    async def trace(self, trace_id: str, operation: str, data: dict[str, Any] | None = None) -> None:
        await self._notify("on_trace", trace_id, {"operation": operation, **(data or {})})

    async def metric(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        await self._notify("on_metric", name, value, labels)

    async def _notify(self, method: str, *args: Any) -> None:
        for observer in tuple(self._observers):
            await getattr(observer, method)(*args)
