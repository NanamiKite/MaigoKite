"""Wire event schemas shared by live delivery and history projection."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from agent.data_contracts import JSON_OBJECT, JSONObject, JSONValue, RiskLabel


class Payload(BaseModel):
    # Keep additive fields for older clients, but never stringify arbitrary
    # Python objects: the live and persisted representations must be identical.
    model_config = ConfigDict(strict=True, extra="allow", allow_inf_nan=False)
    __pydantic_extra__: dict[str, JSONValue] = Field(init=False)


class TextPayload(Payload):
    content: str


class MessageCompletedPayload(TextPayload):
    message_id: str


class ReasoningCompletedPayload(Payload):
    summary: str


class TaskStartedPayload(Payload):
    input_text: str
    trace_id: str
    output_mode: Literal["text", "json_schema"] = "text"
    attachments_json: str = "[]"


class TaskSucceededPayload(Payload):
    result_ready: bool = False
    output_mode: Literal["text", "json_schema"] = "text"


class TaskStoppedPayload(Payload):
    message: str
    error_code: str | None = None
    error_type: str | None = None


class ToolPayload(Payload):
    tool_call_id: str
    tool_name: str


class ToolStartedPayload(ToolPayload):
    arguments: JSONObject


class ApprovalPayload(Payload):
    approval_id: str
    tool_call_id: str
    tool_name: str
    version: int = Field(ge=1)
    risk_level: RiskLabel
    arguments: JSONObject


class ApprovalDecidedPayload(ApprovalPayload):
    decision: Literal["allow", "deny"]


_PAYLOADS: dict[str, type[Payload]] = {
    "message.delta": TextPayload, "message.completed": MessageCompletedPayload,
    "reasoning.delta": TextPayload,
    "reasoning.started": Payload, "reasoning.completed": ReasoningCompletedPayload,
    "task.started": TaskStartedPayload, "task.succeeded": TaskSucceededPayload,
    "task.failed": TaskStoppedPayload, "task.cancelled": TaskStoppedPayload,
    "task.interrupted": TaskStoppedPayload,
    "tool.proposed": ToolStartedPayload, "tool.requested": ToolStartedPayload,
    "tool.started": ToolStartedPayload, "tool.completed": ToolPayload, "tool.failed": ToolPayload,
    "approval.requested": ApprovalPayload, "approval.decided": ApprovalDecidedPayload,
}


def event_payload(kind: str, value: Mapping[str, object]) -> JSONObject:
    """Validate before publishing or committing an event, preserving omitted fields."""
    payload = _PAYLOADS.get(kind, Payload).model_validate(value)
    checked = JSON_OBJECT.validate_python(payload.model_dump(exclude_unset=True), strict=True)
    # JSON does not support NaN/Infinity, even in nested additional fields.
    json.dumps(checked, allow_nan=False)
    return checked


class _Envelope[PayloadT](BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    type: str
    event_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    occurred_at: str
    conversation_id: str
    task_id: str
    payload: PayloadT


class EventEnvelope(_Envelope[JSONObject]):
    type: str = Field(json_schema_extra={"not": {"enum": list(_PAYLOADS)}})

    @field_validator("type")
    @classmethod
    def unknown_events_only(cls, value: str) -> str:
        if cls is EventEnvelope and value in _PAYLOADS:
            raise ValueError("known event types must use their declared payload")
        return value


class TextEvent(_Envelope[TextPayload]):
    type: Literal["message.delta", "reasoning.delta"]
    payload: TextPayload


class MessageCompletedEvent(_Envelope[MessageCompletedPayload]):
    type: Literal["message.completed"]
    payload: MessageCompletedPayload


class ReasoningStartedEvent(_Envelope[Payload]):
    type: Literal["reasoning.started"]
    payload: Payload


class ReasoningCompletedEvent(_Envelope[ReasoningCompletedPayload]):
    type: Literal["reasoning.completed"]
    payload: ReasoningCompletedPayload


class TaskStartedEvent(_Envelope[TaskStartedPayload]):
    type: Literal["task.started"]
    payload: TaskStartedPayload


class TaskSucceededEvent(_Envelope[TaskSucceededPayload]):
    type: Literal["task.succeeded"]
    payload: TaskSucceededPayload


class TaskStoppedEvent(_Envelope[TaskStoppedPayload]):
    type: Literal["task.failed", "task.cancelled", "task.interrupted"]
    payload: TaskStoppedPayload


class ToolStartedEvent(_Envelope[ToolStartedPayload]):
    type: Literal["tool.proposed", "tool.requested", "tool.started"]
    payload: ToolStartedPayload


class ToolEndedEvent(_Envelope[ToolPayload]):
    type: Literal["tool.completed", "tool.failed"]
    payload: ToolPayload


class ApprovalRequestedEvent(_Envelope[ApprovalPayload]):
    type: Literal["approval.requested"]
    payload: ApprovalPayload


class ApprovalDecidedEvent(_Envelope[ApprovalDecidedPayload]):
    type: Literal["approval.decided"]
    payload: ApprovalDecidedPayload


type KnownEvent = Annotated[
    TextEvent | MessageCompletedEvent | ReasoningStartedEvent | ReasoningCompletedEvent | TaskStartedEvent |
    TaskSucceededEvent | TaskStoppedEvent | ToolStartedEvent | ToolEndedEvent |
    ApprovalRequestedEvent | ApprovalDecidedEvent,
    Field(discriminator="type"),
]
type WireEvent = KnownEvent | EventEnvelope
_KNOWN_EVENT: TypeAdapter[KnownEvent] = TypeAdapter(KnownEvent)


def wire_event(value: Mapping[str, object]) -> WireEvent:
    """Validate the durable envelope without coercing missing identities."""
    kind = value.get("type")
    if not isinstance(kind, str):
        raise ValueError("event type must be a string")
    if kind in _PAYLOADS:
        return _KNOWN_EVENT.validate_python(value)
    return EventEnvelope.model_validate(value)
