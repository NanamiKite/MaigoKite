"""Reserved inspection timeline; the live UI reads Repository task events."""

from __future__ import annotations

from typing import Any

from agent.inspector.events import InspectorEvent


class TimelineEntry:
    """A single entry in the execution timeline."""

    event: InspectorEvent
    position: int

    def __init__(self, event: InspectorEvent, position: int) -> None:
        self.event = event
        self.position = position


class TimelineBuilder:
    """Planned timeline projection; ordering and event mapping are not implemented."""

    def __init__(self, trace_id: str) -> None:
        raise NotImplementedError("TimelineBuilder is not implemented; use Repository task events")

    def append(self, event: InspectorEvent) -> None:
        raise NotImplementedError("TimelineBuilder.append is not implemented")

    def get_entries(self) -> list[TimelineEntry]:
        raise NotImplementedError("TimelineBuilder.get_entries is not implemented")

    def filter_by_type(self, event_type: str) -> list[TimelineEntry]:
        raise NotImplementedError("TimelineBuilder.filter_by_type is not implemented")

    def filter_by_task(self, task_id: str) -> list[TimelineEntry]:
        raise NotImplementedError("TimelineBuilder.filter_by_task is not implemented")

    def export_json(self) -> dict[str, Any]:
        raise NotImplementedError("TimelineBuilder.export_json is not implemented")
