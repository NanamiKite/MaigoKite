"""Trusted tool execution inputs; never serialized into messages or UI events."""
from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from agent.data_contracts import JSONObject, PermissionMode

if TYPE_CHECKING:
    from agent.process_registry import ProcessRegistry


class ExecutionMetadata(TypedDict, total=False):
    """Optional entries support standalone tools and restricted child contexts.

    Authority is refreshed by the runtime before admission. This type describes
    that snapshot; it does not cache, grant or persist permissions.
    """

    workspace_id: str
    conversation_id: str
    principal_id: str
    workspace_root: str
    permission_mode: PermissionMode
    sandbox_mode: PermissionMode
    allow_network: bool
    approved: bool
    enabled_tools: list[str]
    api_keys: dict[str, str]
    process_registry: ProcessRegistry
    command_limits: JSONObject
    windows_sandbox: JSONObject
    model_id: str
    model_calls: int
    safety_reviewed_length: int
    safety_model_id: str
