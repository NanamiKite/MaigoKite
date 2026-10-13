"""HTTP input limits and projections of Repository facts used by the desktop UI."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from agent.errors import UserFacingError
from agent.structured_output import validate_output_schema

if TYPE_CHECKING:
    from agent.models.runtime import ChatModelSettings

Principal = "local-user"
PermissionMode = Literal["read_only", "workspace", "workspace_auto", "full_access", "danger_full_access"]
ModelID = Annotated[str, Field(max_length=100)]


class APIError(UserFacingError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationCreate(Input):
    memory_enabled: StrictBool = False
    title: str = Field(default="", max_length=200)
    model_id: str = Field(default="", max_length=200)
    folder_id: str = Field(default="", max_length=200)
    permission_mode: PermissionMode | None = None
    response_mode: Literal["fast", "balanced", "thorough"] = "balanced"
    thinking: str = Field(default="", max_length=32)
    max_subagent_calls: int = Field(default=0, ge=0, le=32)
    max_concurrent_subagents: int = Field(default=1, ge=1, le=8)
    subagent_model_ids: list[ModelID] = Field(default_factory=list, max_length=32)


class ConversationUpdate(Input):
    memory_enabled: StrictBool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    model_id: str | None = Field(default=None, max_length=200)
    folder_id: str | None = Field(default=None, max_length=200)
    archived: StrictBool | None = None
    permission_mode: PermissionMode | None = None
    response_mode: Literal["fast", "balanced", "thorough"] | None = None
    thinking: str | None = Field(default=None, max_length=32)
    max_subagent_calls: int | None = Field(default=None, ge=0, le=32)
    max_concurrent_subagents: int | None = Field(default=None, ge=1, le=8)
    subagent_model_ids: list[ModelID] | None = Field(default=None, max_length=32)


class Attachment(Input):
    type: Literal["", "file", "image", "url"] = ""
    path: str = Field(default="", max_length=4096)
    url: str = Field(default="", max_length=8192)


class SendMessage(Input):
    content: str = Field(min_length=1, max_length=10000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=100)
    output_schema: dict[str, Any] | None = None

    @field_validator("output_schema")
    @classmethod
    def check_output_schema(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_output_schema(value)


class TaskInput(Input):
    content: str = Field(min_length=1, max_length=10000)


class Fork(Input):
    message_id: str = Field(default="", max_length=200)


class Version(Input):
    version: int = Field(ge=1)


class Decision(Version):
    decision: Literal["allow", "deny"]
    note: str = Field(default="", max_length=2000)
    scope: Literal["once", "task", "session"] = "once"


class Answer(Version):
    answer: str = Field(min_length=1, max_length=10000)


class JobInput(Input):
    text: str = Field(default="", max_length=4096)
    eof: StrictBool = False


class ModelInput(Input):
    id: str = Field(default="", max_length=86)
    name: str = Field(default="", max_length=80)
    provider: Literal["openai", "anthropic", "openai_compatible", "ollama"]
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(default="", max_length=2048)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=1, le=65536)
    context_window: int | None = Field(default=None, ge=0, le=9007199254740991)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    is_default: StrictBool = False
    api_key: str = Field(default="", max_length=8192, repr=False)
    credential_source_id: str = Field(default="", max_length=86)

    def settings(self) -> ChatModelSettings:
        from agent.models.runtime import ChatModelSettings
        result = ChatModelSettings.from_dict(self.model_dump(exclude={"api_key", "credential_source_id"}))
        result.validate()
        return result


class ModelValidation(Input):
    chat: ModelInput
    api_key: str = Field(default="", max_length=8192, repr=False)


class ModelsSettings(Input):
    chat: ModelInput


class SettingsUpdate(Input):
    models: ModelsSettings


class Discovery(Input):
    id: str = Field(default="", max_length=86)
    provider: Literal["openai", "anthropic", "openai_compatible", "ollama"]
    base_url: str = Field(default="", max_length=2048)
    api_key: str = Field(default="", max_length=8192, repr=False)


class CredentialUpdate(Input):
    api_key: str = Field(min_length=1, max_length=65536, repr=False)


class CredentialSave(CredentialUpdate):
    service: str = Field(min_length=1, max_length=200)


class Preferences(Input):
    assistant_name: str = Field(min_length=1, max_length=32)


class Permissions(Input):
    default_mode: PermissionMode


class ToolSettings(Input):
    enabled: dict[str, StrictBool]


class SafetySettings(Input):
    """Prompt safety review settings; request/response share this shape.

    `credential_configured` is read-only on write. `api_key` is write-only and
    stored in the credential store, never echoed back. `request_format` and
    `jev_layout` stay plain strings so the endpoint can reject them with
    SAFETY_FORMAT_UNSUPPORTED / SAFETY_LAYOUT_UNSUPPORTED instead of a generic
    validation error; the endpoint checks the enums.
    """

    enabled: StrictBool = False
    request_format: str = Field(default="jev_like", max_length=32)
    jev_layout: str = Field(default="message", max_length=32)
    state: str = Field(default="", max_length=4000)
    decision_base_url: str = Field(default="", max_length=2048)
    decision_model: str = Field(default="", max_length=200)
    credential_configured: StrictBool = False
    trusted_model_ids: list[ModelID] = Field(default_factory=list, max_length=32)
    risk_threshold: int = Field(default=75, ge=0, le=100)
    api_key: str = Field(default="", max_length=8192, repr=False)


class MemoryInput(Input):
    folder_id: str | None = Field(default=None, max_length=200)
    category: str = Field(min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(default=1, ge=0, le=1)


class MemorySettings(Input):
    folder_id: str = Field(max_length=200)
    enabled: StrictBool


class ExtensionApply(Input):
    revision: int = Field(ge=0)
    restore_only: StrictBool = False


class FolderCreate(Input):
    root_path: str = Field(min_length=1, max_length=4096)
    name: str = Field(default="", max_length=120)


class EntryCreate(Input):
    name: str = Field(min_length=1, max_length=255)
    type: Literal["file", "directory"]


def fields(item: dict[str, Any], names: str) -> dict[str, Any]:
    return {key: item[key] for key in names.split() if key in item}


def conversation(item: dict[str, Any]) -> dict[str, Any]:
    value = fields(item, "id workspace_id title model_id permission_mode response_mode thinking folder_id "
                   "forked_from_conversation_id forked_from_message_id created_at updated_at message_count "
                   "archived max_subagent_calls max_concurrent_subagents")
    value["subagent_model_ids"] = json.loads(item.get("subagent_model_ids_json") or "[]")
    value["archived"] = bool(item.get("archived", False))
    value["memory_enabled"] = bool(item.get("memory_enabled", False))
    return value


def task(item: dict[str, Any]) -> dict[str, Any]:
    value = fields(item, "id workspace_id conversation_id title status input_text last_error version created_at updated_at")
    value["subagent_model_ids"] = json.loads(item.get("subagent_model_ids_json") or "[]")
    value["output_mode"] = "json_schema" if item.get("output_schema_json") else "text"
    value["result_ready"] = item.get("status") == "succeeded" and bool(item.get("output_result_json"))
    return value


def approval(item: dict[str, Any]) -> dict[str, Any]:
    from agent.core import AgentCore
    value = AgentCore._approval_payload(item)
    value["id"] = value.pop("approval_id")
    value["workspace_id"] = item["workspace_id"]
    return value


def question(item: dict[str, Any]) -> dict[str, Any]:
    value = fields(item, "id workspace_id conversation_id task_id tool_call_id title status version answer created_at")
    value["options"] = json.loads(item.get("options_json") or "[]")
    return value


def event(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "type": kind, "event_id": payload["event_id"],
            "sequence": payload["sequence"], "occurred_at": payload["occurred_at"],
            "conversation_id": payload.get("conversation_id", ""), "task_id": payload.get("task_id", ""),
            "payload": payload}


def history_event(row: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "type": row["event_type"], "event_id": row["id"],
            "sequence": row["sequence"], "occurred_at": row["occurred_at"],
            "conversation_id": row["conversation_id"], "task_id": row["task_id"], "payload": row.get("payload", {})}
