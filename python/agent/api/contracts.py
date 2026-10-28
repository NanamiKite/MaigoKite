"""HTTP input limits and projections of Repository facts used by the desktop UI."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from agent.data_contracts import (
    ApprovalRecord,
    ApprovalScope,
    ApprovalStatus,
    AuthorizationScope,
    ConversationRecord,
    JSONObject,
    QuestionRecord,
    RiskLabel,
    TaskRecord,
    TaskStatus,
    approval_notice,
)
from agent.data_contracts import PermissionMode as ActivePermissionMode
from agent.errors import ErrorParameter, UserFacingError
from agent.event_contracts import WireEvent, wire_event
from agent.media import ImageReference
from agent.structured_output import validate_output_schema

if TYPE_CHECKING:
    from agent.models.runtime import ChatModelSettings

Principal = "local-user"
PermissionMode = Literal["read_only", "workspace", "workspace_auto", "full_access", "danger_full_access"]
ModelID = Annotated[str, Field(max_length=100)]


class APIError(UserFacingError):
    def __init__(
        self, status: int, code: str, message: str, *, params: dict[str, ErrorParameter] | None = None,
    ) -> None:
        super().__init__(message, code=code, params=params, status_code=status)
        self.status, self.code = status, code


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(BaseModel):
    code: str
    message: str
    params: dict[str, ErrorParameter] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail


class WorkspaceFileResponse(BaseModel):
    path: str
    content: str
    binary: bool
    size: int = Field(ge=0)
    modified_at: str
    truncated: bool
    mime_type: str
    data_url: str = Field(
        pattern=r"^(|data:image/(png|jpeg|gif|webp|bmp);base64,[A-Za-z0-9+/]+=*)$",
    )


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


class ImageUpload(Input):
    data: str = Field(min_length=1, max_length=22369624)


class SendMessage(Input):
    content: str = Field(min_length=1, max_length=10000)
    attachments: list[ImageReference] = Field(default_factory=list, max_length=4)
    output_schema: JSONObject | None = None

    @field_validator("output_schema")
    @classmethod
    def check_output_schema(cls, value: JSONObject | None) -> JSONObject | None:
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
    scope: ApprovalScope = "once"


class Answer(Version):
    answer: str = Field(min_length=1, max_length=10000)


class JobInput(Input):
    text: str = Field(default="", max_length=4096)
    eof: StrictBool = False


class ModelFields(Input):
    id: str = Field(default="", max_length=86)
    name: str = Field(default="", max_length=80)
    provider: Literal["openai", "anthropic", "openai_compatible", "ollama"]
    model: str = Field(max_length=200)
    base_url: str = Field(default="", max_length=2048)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=1, le=65536)
    context_window: int | None = Field(default=None, ge=0, le=9007199254740991)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    is_default: StrictBool = False


class ModelInput(ModelFields):
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default="", max_length=8192, repr=False)
    credential_source_id: str = Field(default="", max_length=86)

    def settings(self) -> ChatModelSettings:
        from agent.models.runtime import ChatModelSettings
        result = ChatModelSettings.from_dict(self.model_dump(exclude={"api_key", "credential_source_id"}))
        result.validate()
        return result


class ModelProfileResponse(ModelFields):
    credential_configured: bool


class ModelCatalogResponse(BaseModel):
    models: list[ModelProfileResponse]
    default_model_id: str
    revision: str


class ModelBatchInput(Input):
    operation_id: UUID
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    profiles: list[ModelInput] = Field(default_factory=list, max_length=32)
    removed_ids: list[Annotated[str, Field(pattern=r"^model_[A-Za-z0-9_-]{1,80}$")]] = Field(
        default_factory=list, max_length=32,
    )
    api_key: str = Field(default="", max_length=8192, repr=False)
    reuse_credentials: StrictBool = True


class ModelBatchResponse(ModelCatalogResponse):
    saved_ids: list[str]
    removed_ids: list[str]
    error: ErrorDetail | None = None


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


class Output(BaseModel):
    # Public fields are explicitly declared. Internal row columns are ignored,
    # not serialized; wrong types and missing required fields are server errors.
    model_config = ConfigDict(strict=True, extra="ignore", allow_inf_nan=False)


class ConversationResponse(Output):
    id: str
    workspace_id: str
    title: str
    title_source: Literal["pending", "automatic", "manual"] = "manual"
    model_id: str
    permission_mode: ActivePermissionMode
    response_mode: Literal["fast", "balanced", "thorough"]
    thinking: str
    folder_id: str
    forked_from_conversation_id: str
    forked_from_message_id: str
    created_at: str
    updated_at: str
    message_count: int
    archived: bool
    memory_enabled: bool
    max_subagent_calls: int
    max_concurrent_subagents: int
    subagent_model_ids: list[str]


class TaskResponse(Output):
    id: str
    workspace_id: str
    conversation_id: str
    title: str
    status: TaskStatus
    input_text: str
    last_error: str
    version: int
    created_at: str
    updated_at: str
    subagent_model_ids: list[str]
    output_mode: Literal["text", "json_schema"]
    result_ready: bool


class TaskResultResponse(Output):
    task_id: str
    status: TaskStatus
    ready: bool
    output_mode: Literal["text", "json_schema"]
    output_schema: JSONObject | None = Field(alias="schema")
    value: JSONObject | None
    error: str

    @model_validator(mode="after")
    def result_readiness(self) -> Self:
        if self.ready:
            if (self.status != "succeeded" or self.output_mode != "json_schema"
                    or self.output_schema is None or self.value is None):
                raise ValueError("ready requires a successful structured result")
        elif self.value is not None:
            raise ValueError("unready results cannot expose a value")
        if (self.output_mode == "json_schema") != (self.output_schema is not None):
            raise ValueError("output mode must match its contract")
        return self


class ApprovalFields(Output):
    id: str
    workspace_id: str
    task_id: str
    conversation_id: str
    tool_call_id: str
    tool_name: str
    resource_scope: str
    risk_level: RiskLabel
    status: ApprovalStatus
    version: int
    expires_at: str
    note: str
    decided_at: str
    allowed_scopes: list[ApprovalScope]
    authorization_scope: ApprovalScope
    authorization_expires_at: str
    authorization_revoked_at: str


class ApprovalResponse(ApprovalFields):
    arguments: JSONObject
    reuse_scope: JSONObject


class SnapshotApproval(ApprovalFields):
    arguments_json: str
    reuse_scope_json: str


class QuestionFields(Output):
    id: str
    workspace_id: str
    conversation_id: str
    task_id: str
    tool_call_id: str
    title: str
    status: Literal["pending", "answered", "cancelled"]
    version: int
    answer: str
    created_at: str


class QuestionOption(Output):
    label: str
    recommended: bool = False
    value: str | None = None


class QuestionResponse(QuestionFields):
    options: list[QuestionOption]


class SnapshotQuestion(QuestionFields):
    options_json: str


class MessageResponse(Output):
    id: str
    conversation_id: str
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    created_at: str
    attachments_json: str = "[]"


class SnapshotResponse(Output):
    messages: list[MessageResponse]
    tasks: list[TaskResponse]
    approvals: list[SnapshotApproval]
    questions: list[SnapshotQuestion]
    last_sequence: int
    truncated: bool


class QuestionsResponse(Output):
    questions: list[QuestionResponse]


class AuthorizationResponse(Output):
    approval_id: str
    conversation_id: str
    task_id: str
    scope: AuthorizationScope
    expires_at: str
    resource: JSONObject


class ConversationsResponse(Output):
    conversations: list[ConversationResponse]


class TasksResponse(Output):
    tasks: list[TaskResponse]


class ApprovalsResponse(Output):
    approvals: list[ApprovalResponse]


class AuthorizationsResponse(Output):
    authorizations: list[AuthorizationResponse]


class EventsResponse(Output):
    events: list[WireEvent]


class MessagesResponse(Output):
    messages: list[MessageResponse]


def fields(item: dict[str, Any], names: str) -> dict[str, Any]:
    return {key: item[key] for key in names.split() if key in item}


def conversation(item: ConversationRecord) -> ConversationResponse:
    return ConversationResponse.model_validate({
        **item, "subagent_model_ids": json.loads(item["subagent_model_ids_json"]),
        "archived": bool(item["archived"]), "memory_enabled": bool(item["memory_enabled"]),
    })


def task(item: TaskRecord) -> TaskResponse:
    return TaskResponse.model_validate({
        **item, "subagent_model_ids": json.loads(item["subagent_model_ids_json"]),
        "output_mode": "json_schema" if item["output_schema_json"] else "text",
        "result_ready": item["status"] == "succeeded" and item["output_result_json"] is not None,
    })


def approval(item: ApprovalRecord) -> ApprovalResponse:
    value = approval_notice(item)
    return ApprovalResponse.model_validate({**value, "id": item["id"], "workspace_id": item["workspace_id"]})


def question(item: QuestionRecord) -> QuestionResponse:
    return QuestionResponse.model_validate({**item, "options": json.loads(item["options_json"])})


def event(kind: str, payload: JSONObject) -> WireEvent:
    return wire_event({
        "schema_version": 1, "type": kind, "event_id": payload["event_id"],
        "sequence": payload["sequence"], "occurred_at": payload["occurred_at"],
        "conversation_id": payload.get("conversation_id", ""), "task_id": payload.get("task_id", ""),
        "payload": payload,
    })


def history_event(row: Mapping[str, object]) -> WireEvent:
    return wire_event({
        "schema_version": 1, "type": row["event_type"], "event_id": row["id"],
        "sequence": row["sequence"], "occurred_at": row["occurred_at"],
        "conversation_id": row["conversation_id"], "task_id": row["task_id"],
        "payload": row.get("payload", {}),
    })
