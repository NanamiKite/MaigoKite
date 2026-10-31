"""Product hooks around LangChain model and tool calls."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages import ToolCall as NativeToolCall
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphInterrupt
from langgraph.runtime import Runtime
from langgraph.types import Command
from langgraph.typing import ContextT

from agent.data_contracts import ApprovalRecord
from agent.errors import UserFacingCancellation, UserFacingError, public_error
from agent.repository import SQLiteRepository
from agent.runtime.run_context import ChildContext, ExecutionMetadataProvider, RunContext
from agent.structured_output import OUTPUT_TOOL_NAME, StructuredResultError, validated_result_json
from agent.tools.base import BaseTool
from agent.tools.permission_mgr import PermissionManager, PolicyDecision, RiskLevel
from agent.tools.validator import ToolValidator, ValidationError


def tool_call_id(call: NativeToolCall) -> str:
    """ToolNode needs a nonempty ID to bind execution and approval to the same call."""
    call_id = call["id"]
    if not call_id:
        raise ValueError("tool call is missing its ID")
    return call_id


@dataclass(frozen=True, slots=True)
class ProductToolSpec:
    """Pair permission metadata with the tool callable passed to LangChain."""

    tool: BaseTool
    runtime_tool: StructuredTool


class ProductToolMiddleware(AgentMiddleware[AgentState[object], RunContext]):
    """Map official tool execution callbacks to MaigoKite product concerns."""

    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        policy: PermissionManager,
        workspace_root: Path,
        tools: Mapping[str, BaseTool],
        execution_metadata: ExecutionMetadataProvider | None,
        interrupt_tool: Callable[[ToolCallRequest, PolicyDecision], bool],
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._workspace_root = workspace_root
        self._tools = tools
        self._execution_metadata = execution_metadata
        self._interrupt_tool = interrupt_tool
        self._validator = ToolValidator(
            lambda name: self._tools[name].parameters_schema if name in self._tools else None
        )

    @property
    def name(self) -> str:
        # LangChain includes this name in graph nodes referenced by existing checkpoints.
        return "_ProductToolMiddleware"

    async def abefore_model(self, state: AgentState[object], runtime: Runtime[RunContext]) -> dict[str, Any] | None:
        context = runtime.context
        pending = await self._repository.task_inputs(
            context.request.workspace_id,
            context.request.task_id,
            pending=True,
        )
        known = {message.id for message in state["messages"]}
        messages = [HumanMessage(content=item["content"], id=item["id"]) for item in pending if item["id"] not in known]
        return {"messages": messages} if messages else None

    async def aafter_model(self, state: AgentState[object], runtime: Runtime[RunContext]) -> None:
        # after_model hooks run in reverse order: refresh before native HITL,
        # including permission changes made while the provider was streaming.
        await self.refresh_authority(runtime.context)

    async def awrap_model_call(
        self,
        request: ModelRequest[RunContext],
        handler: Callable[[ModelRequest[RunContext]], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        context = request.runtime.context
        if context.direct_tool is not None:
            # Convert an explicit /tool command to an AIMessage so it follows
            # the same approval and tool execution nodes as model-generated calls.
            last = request.messages[-1]
            if isinstance(last, HumanMessage):
                return ModelResponse(result=[AIMessage(content="", tool_calls=[context.direct_tool])])
            if isinstance(last, ToolMessage):
                artifact = last.artifact if isinstance(last.artifact, dict) else {}
                content = artifact.get("raw") or artifact.get("summary") or last.text
                return ModelResponse(result=[AIMessage(content=str(content))])
            raise RuntimeError("direct tool command has no matching tool result")
        enabled = request.runtime.context.execution_metadata.get("enabled_tools")
        if enabled is not None:
            request = request.override(
                tools=[tool for tool in request.tools if getattr(tool, "name", "") in {*enabled, "write_todos"}]
            )
        if context.max_subagent_calls == 0:
            request = request.override(
                tools=[
                    tool
                    for tool in request.tools
                    if getattr(tool, "name", "") not in {"delegate", "list_subagent_models"}
                ]
            )
        profile = request.model.profile or {}
        if profile.get("image_inputs") is False and any(
            isinstance(message.content, list)
            and any(
                isinstance(block, dict) and block.get("type") in {"image", "image_url"} for block in message.content
            )
            for message in request.messages
        ):
            raise UserFacingError("当前模型声明不支持图片输入，请切换到支持看图的模型。")
        response = await handler(request)
        if context.request.output_schema is not None:
            result = response.model_response if isinstance(response, ExtendedModelResponse) else response
            if result.structured_response is not None:
                if any(
                    call["name"] != OUTPUT_TOOL_NAME
                    for message in result.result
                    if isinstance(message, AIMessage)
                    for call in message.tool_calls
                ):
                    raise StructuredResultError("最终结果不能与尚未执行的工具一起提交；未保存工作流结果。")
                validated_result_json(context.request.output_schema, result.structured_response)
        # A budget rejection or failed/cancelled request is not a confirmed
        # consumption receipt. A successful model response confirms this input;
        # remote consumption after a lost response remains deliberately unknown.
        consumed = await self._repository.consume_task_inputs(
            context.request.workspace_id,
            context.request.task_id,
            {str(message.id) for message in request.messages if isinstance(message, HumanMessage)},
        )
        for item in consumed:
            await context.projection.emit(
                "task.input.consumed",
                {
                    "input_id": item["id"],
                    "content": item["content"],
                    "status": "consumed",
                },
            )
        return response

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        context = request.runtime.context
        if not isinstance(context, RunContext):
            raise RuntimeError("LangGraph runtime context is missing")
        if request.tool_call["name"] == "delegate":
            # ToolNode fans out independent investigations. Their own semaphore
            # limits concurrency; holding the file-mutation lock here would
            # silently turn every user's parallel setting into one.
            if context.child_scope is None or context.terminal_emitted:
                raise RuntimeError("delegation requires an active root task scope")
            work = context.child_scope.create_task(
                self._execute_tool(request, handler),
                name=f"delegate:{request.tool_call['id']}",
            )
            return await work
        # Serialize ordinary tool bodies to prevent competing writes in one
        # model batch; ToolNode retains orchestration and result handling.
        async with context.tool_lock:
            if request.tool_call["name"] == "write_todos":
                # Framework-owned planning state, not a filesystem/workspace
                # TODO mutation. ToolNode owns the returned Command update.
                await self._repository.update_tool_call(tool_call_id(request.tool_call), "running")
                await context.projection.emit(
                    "tool.started",
                    {
                        "tool_call_id": tool_call_id(request.tool_call),
                        "tool_name": "write_todos",
                        "arguments": request.tool_call.get("args", {}),
                    },
                )
                return await handler(request)
            return await self._execute_tool(request, handler)

    def evaluate(
        self,
        name: str,
        arguments: dict[str, Any],
        context: RunContext | ChildContext,
        *,
        approved: bool = False,
    ) -> PolicyDecision:
        spec = self._tools.get(name)
        enabled = context.execution_metadata.get("enabled_tools", self._tools)
        if spec is None or name not in enabled:
            return PolicyDecision("deny", RiskLevel.L5, "tool is not enabled for this run", arguments)
        try:
            clean = self._validator.sanitize(name, arguments)
        except ValidationError as exc:
            return PolicyDecision("deny", RiskLevel.L5, str(exc), arguments)
        if name == "web_search" and not clean.get("engine"):
            keys = context.execution_metadata.get("api_keys", {})
            clean["engine"] = "tavily" if keys.get("tavily") else "google" if keys.get("google") else "duckduckgo"
        if name == "image_generate":
            # Approval hashes bind the endpoint/model/options without exposing credentials.
            clean["configuration"] = context.execution_metadata.get("image_configuration", "")
        policy = self._policy.for_workspace(context.execution_metadata.get("workspace_root", self._workspace_root))
        decision = policy.evaluate(
            name,
            clean,
            approved=approved,
            risk_override=getattr(spec, "risk_level", None),
            attachment_sources=context.execution_metadata.get("attachment_sources", ""),
            mode=context.execution_metadata.get("permission_mode", context.request.permission_mode),
        )
        # Only the parent can use its own explicitly scoped receipts. Child
        # tools never inherit authorizations, even inside the same conversation.
        if isinstance(context, RunContext) and decision.outcome == "approval_required" and decision.reuse_scope:
            receipt = next(
                (item for item in context.authorizations if policy.authorizes(item, decision)),
                None,
            )
            if receipt is not None:
                return replace(
                    decision,
                    outcome="allow",
                    reason="allowed by scoped approval",
                    authorization_id=receipt["approval_id"],
                )
        return decision

    async def refresh_authority(self, context: RunContext) -> None:
        fresh = (
            await self._execution_metadata(context.request)
            if self._execution_metadata
            else context.execution_metadata.copy()
        )
        original = context.execution_metadata.get("enabled_tools", self._tools)
        # Tool discovery stays stable for this run; revocations still apply.
        fresh["enabled_tools"] = sorted(set(original) & set(fresh.get("enabled_tools", original)))
        fresh["attachment_sources"] = await self._repository.attachment_sources_hash(
            context.request.workspace_id, context.request.conversation_id,
        )
        # Fetch authority last, after configuration/credential lookups. Even a
        # fixed-model runtime must read live permissions, not its initial request.
        mode, grants = await self._repository.execution_authority(
            context.request.workspace_id,
            context.request.conversation_id,
            context.request.principal_id,
            context.request.task_id,
        )
        fresh["permission_mode"] = self._policy.normalize_mode(mode)
        # Replace rather than mutate: an already-started tool keeps its snapshot.
        context.authorizations = grants
        context.execution_metadata = fresh

    def should_interrupt(self, tool_request: ToolCallRequest) -> bool:
        context = cast(RunContext, tool_request.runtime.context)
        if context.review_call_ids and any(call["id"] in context.review_call_ids for call in context.latest_tool_calls):
            # Replay must consume the original native HITL batch. Newly gated
            # calls are checked separately at their actual execution boundary.
            return tool_call_id(tool_request.tool_call) in context.review_call_ids
        decision = self.evaluate(
            str(tool_request.tool_call["name"]),
            dict(tool_request.tool_call.get("args") or {}),
            context,
        )
        return decision.outcome == "approval_required"

    def approval_description(
        self,
        tool_call: NativeToolCall,
        state: AgentState[object],
        runtime: Runtime[ContextT],
    ) -> str:
        decision = self.evaluate(
            str(tool_call["name"]),
            dict(tool_call.get("args") or {}),
            cast(RunContext, runtime.context),
        )
        return f"{decision.reason}\nRisk: {decision.risk.name}\nScope: {decision.resource_scope}"

    async def review_records(self, context: RunContext) -> dict[str, ApprovalRecord]:
        records: dict[str, ApprovalRecord] = {}
        for status in ("pending", "consumed", "rejected", "expired"):
            for record in await self._repository.list_approvals(
                context.request.workspace_id, context.request.conversation_id, status
            ):
                if record["task_id"] == context.request.task_id:
                    records[str(record["tool_call_id"])] = record
        return records

    def matches_approval(self, record: ApprovalRecord, name: str, decision: PolicyDecision) -> bool:
        return (
            record["tool_name"] == name
            and record["risk_level"] == decision.risk.name
            and self._repository.approval_matches_arguments(record, decision.arguments)
            and (record["reuse_scope"] or {}).get("attachment_sources", "")
            == (decision.reuse_scope or {}).get("attachment_sources", "")
        )

    @staticmethod
    def rejection_message(record: ApprovalRecord | None) -> str:
        message = (
            "The user rejected this operation. It was not executed. "
            "Do not retry it or bypass the refusal using another tool unless the user explicitly requests it. "
            "Continue the permitted parts of the task; if the goal requires this operation, explain the limitation."
        )
        if record is not None and record["note"]:
            message += f"\nUser's reason: {record['note']}"
        return message

    async def _execute_tool(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        context = request.runtime.context
        if not isinstance(context, RunContext):
            raise RuntimeError("LangGraph runtime context is missing")
        tool_name = str(request.tool_call["name"])
        task = await self._repository.get_task(context.request.workspace_id, context.request.task_id)
        if task["status"] == "cancelled":
            return ToolMessage(
                content="Task cancelled; this tool was not executed.",
                tool_call_id=tool_call_id(request.tool_call),
                name=tool_name,
                status="error",
            )
        arguments = dict(request.tool_call.get("args") or {})
        if await self._repository.task_inputs(context.request.workspace_id, context.request.task_id, pending=True):
            return ToolMessage(
                content=(
                    "New user input arrived. This proposed tool was not executed; "
                    "reconsider it after reading the user's message."
                ),
                tool_call_id=tool_call_id(request.tool_call),
                name=tool_name,
                status="error",
            )
        record = (await self.review_records(context)).get(tool_call_id(request.tool_call))
        await self.refresh_authority(context)
        authority = replace(context, execution_metadata=context.execution_metadata.copy())
        # Capture authority only after waiting for this tool's admission and
        # reading pending input/approvals. Earlier tools retain their own copy.
        request = replace(request, runtime=replace(request.runtime, context=authority))
        decision = self.evaluate(tool_name, arguments, authority)
        approved = record if record is not None and record["status"] == "consumed" else None
        rejected = record if record is not None and record["status"] == "rejected" else None
        # Recheck after earlier tools finish; a live permission change may
        # require a native interrupt even after model-level HITL has completed.
        if (
            tool_call_id(request.tool_call) in context.boundary_review_ids
            or (decision.outcome == "approval_required" and approved is None and rejected is None)
        ) and self._interrupt_tool(request, decision):
            return ToolMessage(
                content=self.rejection_message(rejected),
                tool_call_id=tool_call_id(request.tool_call),
                name=tool_name,
                status="error",
            )
        if rejected is not None:
            # Replaying a ToolNode after its interrupt was checkpointed away
            # must still respect the audit, even if the permission mode changed.
            return ToolMessage(
                content=self.rejection_message(rejected),
                tool_call_id=tool_call_id(request.tool_call),
                name=tool_name,
                status="error",
            )
        if approved is not None or decision.outcome == "approval_required":
            # HITL owns suspension/resume. The product audit still owns the
            # authorization, including replay directly into a ToolNode after
            # the approval interrupt has already been checkpointed away.
            decision = self.evaluate(tool_name, arguments, authority, approved=approved is not None)
            if approved is None or not self.matches_approval(approved, tool_name, decision):
                return ToolMessage(
                    content="No matching approval for these tool parameters; this tool was not executed.",
                    tool_call_id=tool_call_id(request.tool_call),
                    name=tool_name,
                    status="error",
                )
        if decision.outcome != "allow":
            # ToolNode still projects the returned failure to the model and UI;
            # persist denial so it cannot be mistaken for a failed execution
            # or for the user's separate HITL rejection.
            await self._repository.update_tool_call(
                tool_call_id(request.tool_call),
                "denied",
                risk_level=decision.risk.name,
            )
            return ToolMessage(
                content=decision.reason,
                tool_call_id=tool_call_id(request.tool_call),
                name=tool_name,
                status="error",
                artifact={
                    "summary": decision.reason,
                    "error_code": "TOOL_DENIED",
                    "user_hint": "当前权限或工具设置不允许此操作，未执行。",
                },
            )
        if decision.authorization_id:
            await self._repository.record_audit(
                context.request.workspace_id,
                context.request.principal_id,
                "authorization.used",
                decision.authorization_id,
                "allowed",
                {
                    "task_id": context.request.task_id,
                    "tool_call_id": tool_call_id(request.tool_call),
                    "tool_name": tool_name,
                },
            )
        # Only a successful product policy decision selects the execution
        # boundary. Tool arguments can request escalation, never grant it.
        authority.execution_metadata["sandbox_mode"] = decision.sandbox_mode
        authority.execution_metadata["allow_network"] = decision.allow_network
        call_id = str(request.tool_call.get("id") or "")
        request = request.override(tool_call={**request.tool_call, "args": decision.arguments})
        await self._repository.update_tool_call(
            call_id,
            "running",
            arguments=decision.arguments,
            risk_level=decision.risk.name,
        )
        await context.projection.emit(
            "tool.started",
            {
                "tool_call_id": call_id,
                "tool_name": tool_name,
                "arguments": decision.arguments,
                "resource_scope": decision.resource_scope,
                "risk_level": decision.risk.name,
                "sandbox_mode": decision.sandbox_mode,
                "allow_network": decision.allow_network,
            },
        )
        try:
            result = await handler(request)
        except GraphInterrupt:
            # LangGraph owns the suspension; asking a question is not a failed tool.
            raise
        except UserFacingCancellation as exc:
            # A stopped local waiter is not proof that a remote action stopped.
            # Project the authored cleanup warning, but keep root cancellation
            # even if persisting/delivering that warning itself fails.
            try:
                hint = public_error(exc)
                await context.projection.tool_message(
                    ToolMessage(
                        content=hint,
                        tool_call_id=call_id,
                        name=tool_name,
                        status="error",
                        artifact={"summary": hint, "user_hint": hint, "error_code": "TOOL_CLEANUP_PENDING"},
                    )
                )
            finally:
                raise exc from None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await context.projection.tool_message(
                ToolMessage(
                    content=public_error(exc),
                    tool_call_id=call_id,
                    name=tool_name,
                    status="error",
                )
            )
            raise
        if isinstance(result, ToolMessage):
            await context.projection.tool_message(result)
        return result
