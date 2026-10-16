"""Product records shared by persistence and services, independent of HTTP and LangGraph."""
from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from pydantic import ConfigDict, TypeAdapter, with_config

# A named recursive alias also preserves the JSON shape in serialization
# schemas. Pydantic's JsonValue currently exports an unconstrained output schema.
type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]
type TaskStatus = Literal[
    "created", "queued", "running", "waiting_approval", "retry_wait", "succeeded",
    "failed", "rejected", "expired", "cancelled", "timed_out", "interrupted",
]
type ToolCallStatus = Literal[
    "proposed", "waiting_approval", "running", "succeeded", "failed",
    "denied", "cancelled", "timed_out", "expired",
]
type PermissionMode = Literal["read_only", "workspace", "workspace_auto", "danger_full_access"]
type ApprovalStatus = Literal["pending", "consumed", "rejected", "expired"]
type AuthorizationScope = Literal["task", "session", "session_public_web"]
type ApprovalScope = Literal["once"] | AuthorizationScope
type RiskLabel = Literal["L1", "L2", "L3", "L4", "L5"]


@with_config(ConfigDict(strict=True, allow_inf_nan=False))
class TaskRecord(TypedDict):
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
    request_hash: str
    output_schema_json: str
    output_result_json: str | None
    max_subagent_calls: int
    max_concurrent_subagents: int
    model_call_limit: int
    model_calls_used: int
    subagent_model_ids_json: str


@with_config(ConfigDict(strict=True, allow_inf_nan=False))
class ConversationRecord(TypedDict):
    id: str
    workspace_id: str
    title: str
    model_id: str
    permission_mode: PermissionMode
    response_mode: Literal["fast", "balanced", "thorough"]
    thinking: str
    folder_id: str
    # SQLite flags stay integers here; HTTP projections expose booleans.
    archived: int
    memory_enabled: int
    max_subagent_calls: int
    max_concurrent_subagents: int
    subagent_model_ids_json: str
    forked_from_conversation_id: str
    forked_from_message_id: str
    fork_request_hash: str
    created_at: str
    updated_at: str
    message_count: NotRequired[int]


@with_config(ConfigDict(strict=True, allow_inf_nan=False))
class MessageRecord(TypedDict):
    id: str
    conversation_id: str
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    created_at: str
    attachments_json: NotRequired[str]


@with_config(ConfigDict(strict=True, allow_inf_nan=False))
class ToolCallRecord(TypedDict):
    id: str
    workspace_id: str
    conversation_id: str
    task_id: str
    tool_name: str
    arguments_json: str
    assistant_content: str
    result_json: str | None
    summary: str
    status: ToolCallStatus
    risk_level: RiskLabel | Literal[""]
    created_at: str
    updated_at: str
    subagent_admitted: int


@with_config(ConfigDict(strict=True, allow_inf_nan=False))
class ApprovalRecord(TypedDict):
    id: str
    workspace_id: str
    conversation_id: str
    principal_id: str
    task_id: str
    tool_call_id: str
    arguments_hash: str
    risk_level: RiskLabel
    status: ApprovalStatus
    version: int
    expires_at: str
    created_at: str
    decided_at: str | None
    note: str
    authorization_scope: ApprovalScope
    authorization_expires_at: str
    authorization_revoked_at: str
    tool_name: str
    arguments: JSONObject
    assistant_content: NotRequired[str]
    reuse_scope: JSONObject


@with_config(ConfigDict(strict=True, allow_inf_nan=False))
class AuthorizationRecord(TypedDict):
    approval_id: str
    conversation_id: str
    task_id: str
    scope: AuthorizationScope
    expires_at: str
    resource: JSONObject


@with_config(ConfigDict(strict=True, allow_inf_nan=False))
class TaskEventRecord(TypedDict):
    id: str
    workspace_id: str
    conversation_id: str
    task_id: str
    sequence: int
    event_type: str
    occurred_at: str
    payload: JSONObject


class TaskResultRecord(TypedDict):
    task_id: str
    status: TaskStatus
    ready: bool
    output_mode: Literal["text", "json_schema"]
    schema: JSONObject | None
    value: JSONObject | None
    error: str


class RunMetadata(TypedDict, total=False):
    workspace_id: str
    conversation_id: str
    session_id: str
    principal_id: str
    task_id: str
    trace_id: str
    idempotency_key: str
    request_hash: str
    attachments: list[JSONObject]
    output_schema: JSONObject | None


@with_config(ConfigDict(strict=True, allow_inf_nan=False))
class QuestionRecord(TypedDict):
    id: str
    workspace_id: str
    conversation_id: str
    task_id: str
    tool_call_id: str
    principal_id: str
    title: str
    options_json: str
    status: Literal["pending", "answered", "cancelled"]
    version: int
    answer: str
    created_at: str


class ConversationSnapshotRecord(TypedDict):
    messages: list[MessageRecord]
    tasks: list[TaskRecord]
    approvals: list[ApprovalRecord]
    questions: list[QuestionRecord]
    last_sequence: int
    truncated: bool


class ApprovalNotice(TypedDict):
    approval_id: str
    task_id: str
    conversation_id: str
    tool_call_id: str
    tool_name: str
    arguments: JSONObject
    resource_scope: str
    risk_level: RiskLabel
    status: ApprovalStatus
    version: int
    expires_at: str
    note: str
    decided_at: str
    reuse_scope: JSONObject
    allowed_scopes: list[ApprovalScope]
    authorization_scope: ApprovalScope
    authorization_expires_at: str
    authorization_revoked_at: str


class RunResult(TypedDict):
    """Core completion/replay result; HTTP admission projects its own receipt."""
    task_id: str
    conversation_id: str
    status: TaskStatus
    replayed: NotRequired[bool]
    response: NotRequired[str]
    questions: NotRequired[list[QuestionRecord]]
    approval: NotRequired[ApprovalNotice]
    error: NotRequired[str]
    error_code: NotRequired[str]
    retryable: NotRequired[bool]


def available_approval_scopes(resource: JSONObject | None) -> list[ApprovalScope]:
    """Offer public-web reuse only for the built-in search and HTTP text reader.

    The grant changes neither browser permissions nor web_fetch's public-address
    checks. Old task/session grants retain their engine/origin boundaries.
    """
    scopes: list[ApprovalScope] = ["once"]
    if not resource:
        return scopes
    scopes.extend(["task", "session"])
    if (
        resource.get("policy_version") == 2
        and resource.get("risk") == "L3"
        and resource.get("tool") in ("web_search", "web_fetch")
        and resource.get("mode") in ("read_only", "workspace", "workspace_auto", "danger_full_access")
        and isinstance(resource.get("workspace_root"), str)
        and resource.get("workspace_root")
    ):
        scopes.append("session_public_web")
    return scopes


def approval_notice(approval: ApprovalRecord) -> ApprovalNotice:
    """Public approval facts; omit principal IDs and the private argument hash."""
    arguments = approval["arguments"]
    return {
        "approval_id": approval["id"], "task_id": approval["task_id"],
        "conversation_id": approval["conversation_id"], "tool_call_id": approval["tool_call_id"],
        "tool_name": approval["tool_name"], "arguments": arguments,
        "resource_scope": str(arguments.get("path") or arguments.get("root_dir") or
                              arguments.get("workdir") or arguments.get("url") or "current workspace"),
        "risk_level": approval["risk_level"], "status": approval["status"],
        "version": approval["version"], "expires_at": approval["expires_at"],
        "note": approval["note"], "decided_at": approval["decided_at"] or "",
        "reuse_scope": approval["reuse_scope"],
        "allowed_scopes": available_approval_scopes(approval["reuse_scope"]),
        "authorization_scope": approval["authorization_scope"],
        "authorization_expires_at": approval["authorization_expires_at"],
        "authorization_revoked_at": approval["authorization_revoked_at"],
    }


# SQLite and json.loads are untyped boundaries. Validate once when records
# leave persistence, so services receive checked records rather than casts.
TASK_RECORD = TypeAdapter(TaskRecord)
CONVERSATION_RECORD = TypeAdapter(ConversationRecord)
MESSAGE_RECORD = TypeAdapter(MessageRecord)
APPROVAL_RECORD = TypeAdapter(ApprovalRecord)
AUTHORIZATION_RECORD = TypeAdapter(AuthorizationRecord)
JSON_OBJECT: TypeAdapter[JSONObject] = TypeAdapter(JSONObject, config=ConfigDict(strict=True, allow_inf_nan=False))
PERMISSION_MODE: TypeAdapter[PermissionMode] = TypeAdapter(PermissionMode)
RUN_METADATA = TypeAdapter(RunMetadata)
QUESTION_RECORD = TypeAdapter(QuestionRecord)
SNAPSHOT_RECORD = TypeAdapter(ConversationSnapshotRecord)
TASK_EVENT_RECORD = TypeAdapter(TaskEventRecord)
TOOL_CALL_RECORD = TypeAdapter(ToolCallRecord)
