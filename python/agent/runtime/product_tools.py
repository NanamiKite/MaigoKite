"""Product hooks around LangChain model and tool calls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

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
from langgraph.runtime import Runtime
from langgraph.types import Command

from agent.errors import UserFacingError
from agent.repository import SQLiteRepository
from agent.runtime.run_context import RunContext
from agent.structured_output import OUTPUT_TOOL_NAME, StructuredResultError, validated_result_json

ToolCallHandler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]]


def tool_call_id(call: NativeToolCall) -> str:
    """ToolNode needs a nonempty ID to bind execution and approval to the same call."""
    call_id = call["id"]
    if not call_id:
        raise ValueError("tool call is missing its ID")
    return call_id


class ProductToolMiddleware(AgentMiddleware[AgentState[object], RunContext]):
    """Map official tool execution callbacks to MaigoKite product concerns."""

    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        refresh_authority: Callable[[RunContext], Awaitable[None]],
        execute_tool: Callable[[ToolCallRequest, ToolCallHandler], Awaitable[ToolMessage | Command[Any]]],
    ) -> None:
        self._repository = repository
        self._refresh_authority = refresh_authority
        self._execute_tool = execute_tool

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
        await self._refresh_authority(runtime.context)

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
            isinstance(message, HumanMessage)
            and isinstance(message.content, list)
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
