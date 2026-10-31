"""Per-run request and transient resources shared by the runtime and its middleware."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import ToolCall as NativeToolCall

from agent.data_contracts import AuthorizationRecord, JSONObject, PermissionMode
from agent.media import AttachmentReference
from agent.models.context_capacity import ContextCapacity
from agent.repository import SQLiteRepository
from agent.runtime.projection import RunProjection
from agent.tools.execution_context import ExecutionMetadata


@dataclass(frozen=True, slots=True)
class RuntimeRunRequest:
    workspace_id: str = "personal"
    conversation_id: str = ""
    task_id: str = ""
    principal_id: str = "local-user"
    permission_mode: PermissionMode = "read_only"
    trace_id: str = ""
    request_hash: str = ""
    output_schema: JSONObject | None = None
    attachments: tuple[AttachmentReference, ...] = ()


@dataclass(slots=True)
class RunContext:
    repository: SQLiteRepository
    request: RuntimeRunRequest
    projection: RunProjection
    execution_metadata: ExecutionMetadata = field(default_factory=ExecutionMetadata)
    latest_tool_calls: list[NativeToolCall] = field(default_factory=list)
    tool_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    started: asyncio.Event = field(default_factory=asyncio.Event)
    direct_tool: NativeToolCall | None = None
    review_call_ids: set[str] = field(default_factory=set)
    boundary_review_ids: set[str] = field(default_factory=set)
    authorizations: list[AuthorizationRecord] = field(default_factory=list)
    max_subagent_calls: int = 0
    subagent_model_ids: list[str] = field(default_factory=list)
    child_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))
    child_scope: asyncio.TaskGroup | None = None
    context_capacity: ContextCapacity | None = None
    # Current model request's input budget and fixed prompt/tool overhead.
    # Child results use these estimates without copying the parent's history.
    result_budget: tuple[int, int] | None = None
    # Pending UI metrics only; the summary itself belongs to LangGraph state.
    compaction_receipt: tuple[str, dict[str, Any]] | None = None

    @property
    def terminal_emitted(self) -> bool:
        return self.projection.terminal_delivered


ExecutionMetadataProvider = Callable[[RuntimeRunRequest], Awaitable[ExecutionMetadata]]


@dataclass(slots=True)
class ChildContext:
    parent: RunContext
    subagent_id: str
    request: RuntimeRunRequest
    execution_metadata: ExecutionMetadata
    model_info: dict[str, Any]
    scope: asyncio.TaskGroup | None = None
    context_capacity: ContextCapacity | None = None
    result_token_limit: int = 0

    @property
    def repository(self) -> SQLiteRepository:
        return self.parent.repository
