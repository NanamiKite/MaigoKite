"""Inspector event models for agent monitoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any


class EventType(StrEnum):
    # Keep the existing diagnostic string form while serialized values stay unchanged.
    __str__ = Enum.__str__

    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    MODEL_REQUEST = "model_request"
    MODEL_RESPONSE = "model_response"
    ERROR = "error"
    RECOVERY = "recovery"
    SUB_AGENT_SPAWN = "sub_agent_spawn"
    SUB_AGENT_COMPLETE = "sub_agent_complete"


@dataclass
class InspectorEvent:
    """A single monitored event from the agent lifecycle."""

    event_type: EventType
    timestamp: datetime = field(default_factory=datetime.now)
    trace_id: str = ""
    task_id: str = ""
    step_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: Exception | None = None
