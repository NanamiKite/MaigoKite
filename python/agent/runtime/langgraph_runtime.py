"""Invoke LangGraph and project its messages and interrupts into product records.

Repository stores user decisions and UI history; graph checkpoints carry the
execution position used on resume. Each active run owns its streaming resources.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    TodoListMiddleware,
)
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain.agents.middleware.types import (
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain.agents.structured_output import StructuredOutputError, ToolStrategy
from langchain_anthropic import ChatAnthropic
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages import ToolCall as NativeToolCall
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from langgraph.typing import ContextT

from agent.data_contracts import (
    ApprovalNotice,
    ApprovalRecord,
    AuthorizationRecord,
    JSONObject,
    PermissionMode,
    TaskRecord,
    approval_notice,
)
from agent.errors import UserFacingCancellation, UserFacingError, public_error
from agent.event_contracts import event_payload
from agent.models.context_capacity import ContextCapacity
from agent.models.reasoning import ThinkTagStream, split_thinking, visible_reasoning
from agent.repository import ConversationBusyError, SQLiteRepository
from agent.runtime.context import ContextBudgetMiddleware, SafeSummarizationMiddleware
from agent.runtime.subagents import CHILD_TAG, ChildContext, DelegateTool, ListSubagentModelsTool, create_readonly_agent
from agent.safety import SafetyReviewer, SafetyReviewError, SafetyReviewInput, SafetyReviewResult, skipped_review
from agent.structured_output import (
    OUTPUT_TOOL_NAME,
    StructuredResultError,
    response_schema,
    validate_output_schema,
    validated_result_json,
)
from agent.tools.base import BaseTool
from agent.tools.execution_context import ExecutionMetadata
from agent.tools.native.file_read import FileReadTool
from agent.tools.native.file_write import FileWriteTool
from agent.tools.permission_mgr import PermissionManager, PolicyDecision, RiskLevel
from agent.tools.validator import ToolValidator, ValidationError

logger = logging.getLogger("maigokite.agent.runtime")


def _tool_call_id(call: NativeToolCall) -> str:
    """ToolNode needs a nonempty ID to bind execution and approval to the same call."""
    call_id = call["id"]
    if not call_id:
        raise ValueError("tool call is missing its ID")
    return call_id


@dataclass(frozen=True, slots=True)
class MaigoKiteEvent:
    """A stable product event that does not expose LangGraph event classes."""

    type: str
    payload: JSONObject


EventPublisher = Callable[[str, Mapping[str, object]], Awaitable[None]]
ModelResolver = Callable[[str, str, str], Awaitable[tuple[str, BaseChatModel]]]


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


@dataclass(frozen=True, slots=True)
class ProductToolSpec:
    """Pair permission metadata with the tool callable passed to LangChain."""

    tool: BaseTool
    runtime_tool: StructuredTool


_END = object()


class RunHandle:
    """One run owns one root asyncio task and an event queue."""

    def __init__(
        self,
        request: RuntimeRunRequest,
        root_task: asyncio.Task[None],
        queue: asyncio.Queue[MaigoKiteEvent | object],
        started: asyncio.Event,
    ) -> None:
        self.request = request
        self.root_task = root_task
        self._queue = queue
        self._started = started

    async def events(self) -> AsyncIterator[MaigoKiteEvent]:
        while True:
            item = await self._queue.get()
            if item is _END:
                return
            assert isinstance(item, MaigoKiteEvent)
            yield item

    async def collect(self) -> list[MaigoKiteEvent]:
        return [event async for event in self.events()]

    async def wait(self) -> None:
        await asyncio.shield(self.root_task)

    async def cancel(self) -> None:
        # Let the root enter its try/finally before cancellation. Otherwise an
        # immediate stop can prevent all cleanup/status/queue-finalization code
        # from running. The root yields at this barrier before doing any work.
        if not self.root_task.done():
            await self._started.wait()
        if not self.root_task.done() and not self.root_task.cancelling():
            self.root_task.cancel()
        try:
            # Repeated stop/shutdown callers must not interrupt root cleanup.
            await asyncio.shield(self.root_task)
        except asyncio.CancelledError:
            if not self.root_task.done():
                raise


@dataclass(slots=True)
class _RunContext:
    adapter: LangGraphRuntime
    request: RuntimeRunRequest
    queue: asyncio.Queue[MaigoKiteEvent | object]
    execution_metadata: ExecutionMetadata = field(default_factory=ExecutionMetadata)
    latest_tool_calls: list[NativeToolCall] = field(default_factory=list)
    finished_tool_calls: set[str] = field(default_factory=set)
    terminal_emitted: bool = False
    reasoning_parts: list[str] = field(default_factory=list)
    reasoning_started: bool = False
    reasoning_completed: bool = False
    reasoning_streamed: bool = False
    text_stream: ThinkTagStream = field(default_factory=ThinkTagStream)
    final_saved: bool = False
    tool_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    event_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
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


class _ProductToolMiddleware(AgentMiddleware[AgentState[object], _RunContext]):
    """Map official tool execution callbacks to MaigoKite product concerns."""

    async def abefore_model(self, state: AgentState[object], runtime: Runtime[_RunContext]) -> dict[str, Any] | None:
        context = runtime.context
        pending = await context.adapter.repository.task_inputs(
            context.request.workspace_id, context.request.task_id, pending=True,
        )
        known = {message.id for message in state["messages"]}
        messages = [HumanMessage(content=item["content"], id=item["id"])
                    for item in pending if item["id"] not in known]
        return {"messages": messages} if messages else None

    async def aafter_model(self, state: AgentState[object], runtime: Runtime[_RunContext]) -> None:
        # after_model hooks run in reverse order: refresh before native HITL,
        # including permission changes made while the provider was streaming.
        await runtime.context.adapter._refresh_execution_metadata(runtime.context)

    async def awrap_model_call(
        self, request: ModelRequest[_RunContext],
        handler: Callable[[ModelRequest[_RunContext]], Awaitable[ModelResponse]],
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
            request = request.override(tools=[tool for tool in request.tools
                                             if getattr(tool, "name", "") in {*enabled, "write_todos"}])
        if context.max_subagent_calls == 0:
            request = request.override(
                tools=[
                    tool
                    for tool in request.tools
                    if getattr(tool, "name", "") not in {"delegate", "list_subagent_models"}
                ]
            )
        response = await handler(request)
        if context.request.output_schema is not None:
            result = response.model_response if isinstance(response, ExtendedModelResponse) else response
            if result.structured_response is not None:
                if any(call["name"] != OUTPUT_TOOL_NAME
                       for message in result.result if isinstance(message, AIMessage)
                       for call in message.tool_calls):
                    raise StructuredResultError("最终结果不能与尚未执行的工具一起提交；未保存工作流结果。")
                validated_result_json(context.request.output_schema, result.structured_response)
        # A budget rejection or failed/cancelled request is not a confirmed
        # consumption receipt. A successful model response confirms this input;
        # remote consumption after a lost response remains deliberately unknown.
        consumed = await context.adapter.repository.consume_task_inputs(
            context.request.workspace_id, context.request.task_id,
            {str(message.id) for message in request.messages if isinstance(message, HumanMessage)},
        )
        for item in consumed:
            await context.adapter._emit(context, "task.input.consumed", {
                "input_id": item["id"], "content": item["content"], "status": "consumed",
            })
        return response

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        context = request.runtime.context
        if not isinstance(context, _RunContext):
            raise RuntimeError("LangGraph runtime context is missing")
        if request.tool_call["name"] == "delegate":
            # ToolNode fans out independent investigations. Their own semaphore
            # limits concurrency; holding the file-mutation lock here would
            # silently turn every user's parallel setting into one.
            if context.child_scope is None or context.terminal_emitted:
                raise RuntimeError("delegation requires an active root task scope")
            work = context.child_scope.create_task(
                context.adapter._execute_product_tool_call(request, handler),
                name=f"delegate:{request.tool_call['id']}",
            )
            return await work
        # ToolNode retains orchestration and results. Serialize tool bodies
        # during migration, including writes to the same file in one batch.
        async with context.tool_lock:
            if request.tool_call["name"] == "write_todos":
                # Framework-owned planning state, not a filesystem/workspace
                # TODO mutation. ToolNode owns the returned Command update.
                await context.adapter.repository.update_tool_call(_tool_call_id(request.tool_call), "running")
                await context.adapter._emit(context, "tool.started", {
                    "tool_call_id": _tool_call_id(request.tool_call), "tool_name": "write_todos",
                    "arguments": request.tool_call.get("args", {}),
                })
                return await handler(request)
            return await context.adapter._execute_product_tool_call(request, handler)


class _PromptSafetyMiddleware(AgentMiddleware[AgentState[object], _RunContext]):
    """Review newly visible human prompt text immediately before a model call."""

    async def awrap_model_call(
            self, request: ModelRequest[_RunContext],
            handler: Callable[[ModelRequest[_RunContext]], Awaitable[ModelResponse]],
    ) -> ModelResponse:
            context = request.runtime.context
            adapter = context.adapter
            if adapter._safety_settings is None:
                return await handler(request)
            settings = await adapter._safety_settings(context.request.workspace_id)
            if not settings.get("enabled"):
                return await handler(request)
            model_id = str(context.execution_metadata.get("model_id") or
                           getattr(request.model, "model_name", None) or getattr(request.model, "model", ""))
            if model_id in settings.get("trusted_model_ids", []):
                result = skipped_review(model_id, str(settings["request_format"]), int(settings["risk_threshold"]))
                await adapter._emit_safety_result(context, result)
                return await handler(request)
            previous_questions = await adapter.repository.task_questions(
                context.request.workspace_id, context.request.task_id,
            )
            safety_question = next(
                (item for item in reversed(previous_questions)
                 if item["tool_call_id"].startswith("safety_")),
                None,
            )
            if safety_question is not None:
                if safety_question["status"] == "answered":
                    if safety_question["answer"] != "继续执行":
                        raise PermissionError("prompt safety review denied execution")
                    return await handler(request)
                decision = interrupt({
                    "kind": "user_question",
                    "tool_call_id": safety_question["tool_call_id"],
                    "title": safety_question["title"],
                    "options": json.loads(safety_question["options_json"]),
                    "safety_review": True,
                    "review_id": safety_question["tool_call_id"],
                })
                if str(decision) != "继续执行":
                    raise PermissionError("prompt safety review denied execution")
                return await handler(request)
            human_text = "\n\n".join(
                str(message.text) for message in request.messages if isinstance(message, HumanMessage)
            )
            previous_length = int(context.execution_metadata.get("safety_reviewed_length", 0))
            delta = human_text[previous_length:]
            if not delta.strip():
                return await handler(request)
            context.execution_metadata["safety_reviewed_length"] = len(human_text)
            key = await adapter._safety_credential(context.request.workspace_id) if adapter._safety_credential else ""
            try:
                result = await adapter._safety_reviewer.review(
                    base_url=str(settings["decision_base_url"]),
                    api_key=key,
                    request_format=str(settings["request_format"]),
                    state=str(settings["state"]),
                    decision_model=str(settings["decision_model"]),
                    review=SafetyReviewInput(
                        model_id=model_id, task_id=context.request.task_id,
                        step=int(context.execution_metadata.get("model_calls", 0)) + 1,
                        delta=delta, previous_model_id=str(context.execution_metadata.get("safety_model_id", "")),
                    ),
                    threshold=int(settings["risk_threshold"]),
                    layout=str(settings["jev_layout"]),
                )
            except SafetyReviewError as exc:
                logger.warning(
                    "Prompt safety review failed closed: model_id=%s format=%s error=%s",
                    model_id,
                    settings["request_format"],
                    exc,
                )
                result = SafetyReviewResult(
                    review_id="safety_" + uuid.uuid4().hex, model_id=model_id,
                    request_format=str(settings["request_format"]), risk_score=None,
                    threshold=int(settings["risk_threshold"]), skipped=False,
                    skip_reason=None, blocked=True, reason="review_error",
                )
            context.execution_metadata["safety_model_id"] = model_id
            await adapter._emit_safety_result(context, result)
            if result.blocked:
                await adapter.repository.create_tool_call(
                    result.review_id,
                    context.request.workspace_id,
                    context.request.conversation_id,
                    context.request.task_id,
                    "safety_review",
                    {"review_id": result.review_id, "risk_score": result.risk_score},
                    delta,
                )
                decision = interrupt({
                    "kind": "user_question", "tool_call_id": result.review_id,
                    "title": "提示词安全审查需要确认",
                    "options": [
                        {"label": "继续执行", "recommended": False},
                        {"label": "终止任务", "recommended": True},
                    ],
                    "safety_review": True,
                    "review_id": result.review_id,
                })
                if str(decision) != "继续执行":
                    raise PermissionError("prompt safety review denied execution")
            return await handler(request)


class LangGraphRuntime:
    """Own active runs and translate checkpoint interrupts to product approvals."""

    def __init__(
        self,
        *,
        model: BaseChatModel | None = None,
        model_resolver: ModelResolver | None = None,
        model_catalog: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None,
        workspace_root: str | Path,
        checkpoint_path: str | Path,
        product_db_path: str | Path | None = None,
        repository: SQLiteRepository | None = None,
        policy: PermissionManager | None = None,
        event_publisher: EventPublisher | None = None,
        tools: Sequence[ProductToolSpec] | None = None,
        system_prompt: str = "",
        context_max_tokens: int = 32000,
        max_model_calls: int = 8,
        execution_metadata: Callable[[RuntimeRunRequest], Awaitable[ExecutionMetadata]] | None = None,
        safety_settings: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        safety_credential: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        if model is None and model_resolver is None:
            raise ValueError("model or model_resolver is required")
        if repository is None and product_db_path is None:
            raise ValueError("product_db_path is required when repository is not provided")
        self.model = model
        if context_max_tokens <= 0 or max_model_calls <= 0:
            raise ValueError("context_max_tokens and max_model_calls must be positive")
        self.system_prompt = system_prompt
        self.context_max_tokens = context_max_tokens
        self.max_model_calls = max_model_calls
        self.model_resolver = model_resolver
        self.model_catalog = model_catalog
        self._execution_metadata = execution_metadata
        self._safety_settings = safety_settings
        self._safety_credential = safety_credential
        self._safety_reviewer = SafetyReviewer()
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.checkpoint_path = (
            Path(checkpoint_path).expanduser().resolve()
            if str(checkpoint_path) != ":memory:"
            else Path(":memory:")
        )
        if repository is None:
            assert product_db_path is not None
            self.repository = SQLiteRepository(product_db_path)
        else:
            self.repository = repository
        self._owns_repository = repository is None
        self.policy = policy or PermissionManager("read_only", self.workspace_root)
        self._event_publisher = event_publisher
        if tools is None:
            read_tool = FileReadTool()
            write_tool = FileWriteTool()
            self.tool_specs = [
                ProductToolSpec(read_tool, read_tool.to_langchain_tool()),
                ProductToolSpec(write_tool, write_tool.to_langchain_tool()),
            ]
        else:
            self.tool_specs = list(tools)
        # Tool order is part of the provider's reusable prompt prefix.
        self.tool_specs.sort(key=lambda spec: spec.tool.name)
        self._specs_by_name = {spec.tool.name: spec for spec in self.tool_specs}
        self._validator = ToolValidator(
            lambda name: self._specs_by_name[name].tool.parameters_schema
            if name in self._specs_by_name else None
        )
        self._checkpointer_context: Any | None = None
        self._checkpointer: AsyncSqliteSaver | None = None
        self._graphs: dict[str, Any] = {}
        self._readonly_graphs: dict[str, Any] = {}
        self._active_runs: dict[str, RunHandle] = {}
        self._conversation_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._opened = False

    async def open(self) -> None:
        if self._opened:
            return
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        if str(self.checkpoint_path) != ":memory:":
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        if self._owns_repository:
            await self.repository.open()
        self._checkpointer_context = AsyncSqliteSaver.from_conn_string(
            str(self.checkpoint_path)
        )
        self._checkpointer = await self._checkpointer_context.__aenter__()
        self._opened = True
        if self.model is not None:
            self._graphs["fixed"] = self._create_graph(self.model)

    def readonly_graph(self, key: str, model: BaseChatModel) -> Any:
        """Cache compiled native child graphs, not execution state or HTTP clients."""
        if key not in self._readonly_graphs:
            self._readonly_graphs[key] = create_readonly_agent(
                model, self.tool_specs, self.context_max_tokens, self.max_model_calls
            )
        return self._readonly_graphs[key]

    def _create_graph(
        self, model: BaseChatModel, model_info: dict[str, Any] | None = None,
        *, output_schema: JSONObject | None = None,
    ) -> Any:
        if output_schema is not None and OUTPUT_TOOL_NAME in self._specs_by_name:
            raise UserFacingError("工具名称与结构化结果保留名称冲突，请停用同名扩展。")
        child = create_readonly_agent(model, self.tool_specs, self.context_max_tokens, self.max_model_calls)
        info = {**(model_info or {}), "model": getattr(model, "model_name", None) or getattr(model, "model", "")}
        runtime_tools = [
            DelegateTool(child, info).to_langchain_tool()
            if spec.tool.name == "delegate"
            else ListSubagentModelsTool(info).to_langchain_tool()
            if spec.tool.name == "list_subagent_models"
            else spec.runtime_tool
            for spec in self.tool_specs
        ]
        interrupt_on: dict[str, bool | InterruptOnConfig] = {
            spec.tool.name: InterruptOnConfig(
                allowed_decisions=["approve", "reject"],
                when=self._should_interrupt,
                description=self._approval_description,
            )
            for spec in self.tool_specs
        }
        # Official middleware still owns the plan and write_todos schema. Its
        # long default tutorial need not consume every small-window request.
        planning = TodoListMiddleware(
            system_prompt=(
                "Use write_todos for complex work or an explicitly requested plan, not simple chat. "
                "Each call replaces the entire plan: include unchanged and unfinished steps too. "
                "Keep step descriptions concise and stable when only their status changes. "
                "Mark work in_progress before starting it, then update that step to completed only "
                "after its result confirms success; leave other steps at their actual status. "
                "A successful tool call does not complete the whole plan. Do not batch completion "
                "updates until the end or call write_todos more than once in a model turn. "
                "Read files in bounded pages and prefer targeted searches over large raw outputs."
            ),
            tool_description=(
                "Replace the full task plan, not just changed steps. Include pending (not started), "
                "in_progress (unfinished) and completed (confirmed done) steps. Preserve unchanged "
                "steps; omit a step only when deliberately removing it from the plan."
            ),
        )
        summarizer = SafeSummarizationMiddleware(
            model=model,
            input_budget=self.context_max_tokens,
            system_prompt="\n\n".join(part for part in (self.system_prompt, planning.system_prompt) if part),
            tools=[*runtime_tools, *planning.tools],
        )
        middleware: list[AgentMiddleware[Any, Any, Any]] = [
            planning,
            ModelCallLimitMiddleware(thread_limit=self.max_model_calls, exit_behavior="error"),
            summarizer,
            # Append queued user input after compaction so it cannot be reduced
            # to a summary before its first consumption receipt.
            HumanInTheLoopMiddleware(interrupt_on),
            _ProductToolMiddleware(),
            _PromptSafetyMiddleware(),
            ContextBudgetMiddleware[_RunContext](self.context_max_tokens, summarizer),
        ]
        if isinstance(model, ChatAnthropic):
            middleware.append(AnthropicPromptCachingMiddleware())
        return create_agent(
            model,
            runtime_tools,
            system_prompt=self.system_prompt or None,
            middleware=middleware,
            checkpointer=self._checkpointer,
            context_schema=_RunContext,
            response_format=ToolStrategy(
                response_schema(output_schema), handle_errors=False,
                tool_message_content="Final structured task result captured.",
            ) if output_schema is not None else None,
            name="maigokite-langgraph-runtime",
        )

    async def cancel_active_runs(self) -> None:
        """Stop the current roots using the same cancellation path as one run."""
        # A finished root may still be waiting for its release callback. Its
        # failure belongs to wait(), not a fresh cancellation request; replaying
        # that exception here would prevent closing already-idle resources.
        handles = [handle for handle in self._active_runs.values() if not handle.root_task.done()]
        if handles:
            results = await asyncio.gather(*(handle.cancel() for handle in handles), return_exceptions=True)
            # Finish draining every root even if one cleanup fails. Do not
            # silently report successful shutdown after a cleanup error.
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    async def close(self) -> None:
        self._opened = False  # No new admissions while owned runs drain.
        errors: list[Exception] = []
        try:
            await self.cancel_active_runs()
        except Exception as exc:
            # cancel_active_runs waits for every root before reporting errors.
            errors.append(exc)
        self._active_runs.clear()
        self._conversation_locks.clear()
        self._graphs.clear()
        self._readonly_graphs.clear()
        # Roots close graph streams before terminating. Saver writes commit in
        # aput/aput_writes; a checkpoint read is not a persistence barrier.
        self._checkpointer = None
        if self._checkpointer_context is not None:
            try:
                await self._checkpointer_context.__aexit__(None, None, None)
            except Exception as exc:
                errors.append(exc)
            else:
                self._checkpointer_context = None
        if self._owns_repository:
            try:
                await self.repository.close()
            except Exception as exc:
                errors.append(exc)
        self._opened = False
        if errors:
            raise ExceptionGroup("Runtime cleanup failed after draining runs", errors)

    async def __aenter__(self) -> LangGraphRuntime:
        await self.open()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def _require_open(self) -> Any:
        if not self._opened or self._checkpointer is None:
            raise RuntimeError("LangGraph runtime is not open")
        return self._checkpointer

    async def _graph_for(self, request: RuntimeRunRequest) -> Any:
        self._require_open()
        schema = request.output_schema
        if schema is None and request.task_id:
            try:
                task = await self.repository.get_task(request.workspace_id, request.task_id)
            except KeyError:
                pass  # A new turn has not committed its task yet.
            else:
                schema = json.loads(task["output_schema_json"]) if task.get("output_schema_json") else None
        if self.model is not None:
            return self._create_graph(self.model, output_schema=schema) if schema is not None else self._graphs["fixed"]
        assert self.model_resolver is not None
        conversation = await self.repository.get_conversation(
            request.workspace_id, request.conversation_id
        )
        key, model = await self.model_resolver(
            request.workspace_id, str(conversation.get("model_id") or ""),
            str(conversation.get("thinking") or ""),
        )
        self._require_open()
        graph = self._graphs.get(key)
        if graph is None or schema is not None:
            catalog = await self.model_catalog(request.workspace_id) if self.model_catalog else []
            info = next((item for item in catalog if item["model_id"] == conversation.get("model_id")), {})
            if not conversation.get("model_id"):
                info = next((item for item in catalog if item.get("is_default")), {})
            graph = self._create_graph(model, info, output_schema=schema)
            if schema is not None:
                # Schemas are per-task inputs. Do not retain one compiled graph
                # for every user schema in the shared model cache.
                return graph
            self._graphs[key] = graph
        return graph

    def _policy_decision(
        self, name: str, arguments: dict[str, Any], context: _RunContext | ChildContext, *, approved: bool = False,
    ) -> PolicyDecision:
        spec = self._specs_by_name.get(name)
        enabled = context.execution_metadata.get("enabled_tools", self._specs_by_name)
        if spec is None or name not in enabled:
            return PolicyDecision("deny", RiskLevel.L5, "tool is not enabled for this run", arguments)
        try:
            clean = self._validator.sanitize(name, arguments)
        except ValidationError as exc:
            return PolicyDecision("deny", RiskLevel.L5, str(exc), arguments)
        if name == "web_search" and not clean.get("engine"):
            keys = context.execution_metadata.get("api_keys", {})
            clean["engine"] = "tavily" if keys.get("tavily") else "google" if keys.get("google") else "duckduckgo"
        policy = self.policy.for_workspace(context.execution_metadata.get("workspace_root", self.workspace_root))
        decision = policy.evaluate(
            name,
            clean,
            approved=approved,
            risk_override=getattr(spec.tool, "risk_level", None),
            mode=context.execution_metadata.get("permission_mode", context.request.permission_mode),
        )
        # Only the parent can use its own explicitly scoped receipts. Child
        # tools never inherit authorizations, even inside the same conversation.
        if isinstance(context, _RunContext) and decision.outcome == "approval_required" and decision.reuse_scope:
            receipt = next((item for item in context.authorizations if item["resource"] == decision.reuse_scope), None)
            if receipt is not None:
                return replace(decision, outcome="allow", reason="allowed by scoped approval",
                               authorization_id=receipt["approval_id"])
        return decision

    async def _refresh_execution_metadata(self, context: _RunContext) -> None:
        fresh = (await self._execution_metadata(context.request) if self._execution_metadata
                 else context.execution_metadata.copy())
        original = context.execution_metadata.get("enabled_tools", self._specs_by_name)
        # Tool discovery stays stable for this run; revocations still apply.
        fresh["enabled_tools"] = sorted(set(original) & set(fresh.get("enabled_tools", original)))
        # Fetch authority last, after configuration/credential lookups. Even a
        # fixed-model runtime must read live permissions, not its initial request.
        mode, grants = await self.repository.execution_authority(
            context.request.workspace_id, context.request.conversation_id,
            context.request.principal_id, context.request.task_id,
        )
        fresh["permission_mode"] = self.policy.normalize_mode(mode)
        # Replace rather than mutate: an already-started tool keeps its snapshot.
        context.authorizations = grants
        context.execution_metadata = fresh

    async def _emit_safety_result(self, context: _RunContext, result: Any) -> None:
        await self._emit(context, "safety.reviewed", {
            "review_id": result.review_id,
            "model_id": result.model_id,
            "request_format": result.request_format,
            "risk_score": result.risk_score,
            "threshold": result.threshold,
            "skipped": result.skipped,
            "skip_reason": result.skip_reason,
            "blocked": result.blocked,
            "reason": result.reason,
        })

    def _should_interrupt(self, tool_request: ToolCallRequest) -> bool:
        context = cast(_RunContext, tool_request.runtime.context)
        if context.review_call_ids and any(
            call["id"] in context.review_call_ids for call in context.latest_tool_calls
        ):
            # Replay must consume the original native HITL batch. Newly gated
            # calls are checked separately at their actual execution boundary.
            return _tool_call_id(tool_request.tool_call) in context.review_call_ids
        decision = self._policy_decision(
            str(tool_request.tool_call["name"]),
            dict(tool_request.tool_call.get("args") or {}),
            context,
        )
        return decision.outcome == "approval_required"

    def _approval_description(
        self, tool_call: NativeToolCall, state: AgentState[object], runtime: Runtime[ContextT],
    ) -> str:
        decision = self._policy_decision(
            str(tool_call["name"]),
            dict(tool_call.get("args") or {}),
            cast(_RunContext, runtime.context),
        )
        return (
            f"{decision.reason}\n"
            f"Risk: {decision.risk.name}\n"
            f"Scope: {decision.resource_scope}"
        )

    async def start(
        self,
        prompt: str,
        request: RuntimeRunRequest | None = None,
    ) -> RunHandle:
        self._require_open()
        requested = request or RuntimeRunRequest()
        requested = replace(requested, output_schema=validate_output_schema(requested.output_schema))
        if requested.output_schema is not None and prompt.startswith("/tool "):
            raise UserFacingError("结构化输出需要模型完成最终结果，不能与直接 /tool 命令合用。")
        await self.repository.ensure_workspace(
            requested.workspace_id,
            requested.workspace_id,
            "personal" if requested.workspace_id == "personal" else "team",
        )
        if requested.conversation_id:
            conversation = await self.repository.get_conversation(
                requested.workspace_id, requested.conversation_id
            )
        else:
            conversation = await self.repository.create_conversation(
                requested.workspace_id,
                title=prompt[:80],
                permission_mode=requested.permission_mode,
            )
        task_id = requested.task_id or f"task_{uuid.uuid4().hex}"
        actual = replace(
            requested,
            conversation_id=str(conversation["id"]),
            task_id=task_id,
        )
        async with self._conversation_locks[actual.conversation_id]:
            return await self._start_turn(prompt, actual)

    def has_active_conversation(self, conversation_id: str) -> bool:
        return any(
            handle.request.conversation_id == conversation_id and not handle.root_task.done()
            for handle in self._active_runs.values()
        )

    async def delete_threads(self, task_ids: Sequence[str]) -> None:
        """Use the checkpointer's public deletion API, not custom checkpoint SQL."""
        if self._checkpointer is not None:
            for task_id in task_ids:
                await self._checkpointer.adelete_thread(task_id)

    async def _start_turn(self, prompt: str, actual: RuntimeRunRequest) -> RunHandle:
        await self.repository.check_message_replay(actual.workspace_id, actual.task_id, actual.request_hash)
        previous = await self.repository.latest_conversation_task(actual.workspace_id, actual.conversation_id)
        if previous and previous["status"] in {"created", "queued", "running", "waiting_approval", "retry_wait"}:
            raise ConversationBusyError(
                "conversation has unfinished work; finish or cancel it before sending another turn"
            )
        if self.has_active_conversation(actual.conversation_id):
            raise ConversationBusyError("conversation is still finishing its previous run")
        # Resolve credentials/model before committing product state. Failure to
        # prepare a run must not leave a phantom running task behind.
        graph = await self._graph_for(actual)
        self._direct_tool_call(prompt, actual.task_id)  # Validate command syntax before persisting.
        messages: list[Any] = []
        history_source = "product_history"
        if previous and previous["status"] == "succeeded":
            snapshot = await graph.aget_state({"configurable": {"thread_id": str(previous["id"])}})
            if snapshot.values and not snapshot.next and not snapshot.interrupts:
                # Carry the framework's retained messages, including its summary
                # and ToolMessages. Counters/interrupts belong to the old run.
                messages = list(snapshot.values.get("messages", []))
                history_source = "checkpoint"
        if not messages:
            # First migration/legacy or interrupted history: no invented tool
            # results or continuation. Import only the visible product history.
            branch = await self.repository.get_conversation(actual.workspace_id, actual.conversation_id)
            history = await self.repository.list_messages(
                actual.conversation_id, limit=500 if branch.get("forked_from_conversation_id") else 100,
            )
            messages = [
                {"role": str(item["role"]), "content": str(item["content"])}
                for item in history if str(item.get("role")) in {"user", "assistant", "system"}
            ]
            if branch.get("forked_from_conversation_id"):
                messages.insert(0, SystemMessage(
                    "This is an independent conversation branch containing visible user/assistant history only. "
                    "Earlier tool results and execution state were not copied. The workspace files are shared, "
                    "not a snapshot or a rollback. Inspect the current state before acting. Past approvals do not "
                    "authorize actions in this branch; follow its current permissions."
                ))
            if previous and previous["status"] != "succeeded":
                # Retain this safety instruction in checkpoint history. The
                # model-call boundary merges it with create_agent's prompt into
                # one leading system message, including on subsequent turns.
                messages.insert(0, SystemMessage(
                    "Execution boundary: the previous task ended with status " + str(previous["status"]) +
                    ". Only visible conversation history was imported, not a runnable continuation. "
                    "Earlier tool side effects may have occurred. Inspect the actual state before repeating actions; "
                    "this note does not grant permission or imply a rollback."
                ))
        await self.repository.start_chat_turn(
            actual.task_id, actual.workspace_id, actual.conversation_id, prompt, actual.request_hash,
            model_call_limit=self.max_model_calls,
            output_schema=actual.output_schema,
        )
        messages.append({"role": "user", "content": prompt})
        try:
            return await self._launch(
                {"messages": messages},
                actual,
                graph=graph,
                initial_events=[
                    (
                        "task.started",
                        {
                            "trace_id": actual.trace_id,
                            "input_text": prompt,
                            "output_mode": "json_schema" if actual.output_schema is not None else "text",
                        },
                    ),
                    (
                        "context.history",
                        {
                            "source": history_source,
                            "previous_task_id": previous["id"] if previous else None,
                            "execution_resumed": False,
                        },
                    ),
                ],
            )
        except asyncio.CancelledError:
            # No root task owns this admission yet. Drain this entry before Core
            # closes Repository; do not leave a phantom running task until restart.
            await self.repository.cancel_task(actual.workspace_id, actual.task_id)
            await self._publish_event(actual, "task.cancelled", {"message": "cancelled before execution started"})
            raise
        except Exception as exc:
            task = await self.repository.get_task(actual.workspace_id, actual.task_id)
            if task["status"] != "cancelled":
                await self.repository.update_task_status(actual.task_id, "failed", public_error(exc))
                await self._publish_event(
                    actual, "task.failed", {"message": public_error(exc), "error_type": type(exc).__name__}
                )
            raise

    async def resume_approval(
        self,
        workspace_id: str,
        approval_id: str,
        principal_id: str,
        decision: str,
        version: int,
        note: str = "",
        scope: str = "once",
    ) -> RunHandle:
        self._require_open()
        pending = await self.repository.get_approval(workspace_id, approval_id)
        async with self._conversation_locks[str(pending["conversation_id"])]:
            handle = self._active_runs.get(str(pending["task_id"]))
            if handle is not None:
                # The interrupt may already be visible while its previous root
                # task is finishing. Await it before consuming a new decision.
                await asyncio.shield(handle.wait())
            return await self._resume_approval(workspace_id, approval_id, principal_id, decision, version, note, scope)

    async def _resume_approval(
        self, workspace_id: str, approval_id: str, principal_id: str, decision: str, version: int,
        note: str = "",
        scope: str = "once",
    ) -> RunHandle:
        if decision not in {"allow", "deny"}:
            raise ValueError("decision must be allow or deny")
        if scope not in {"once", "task", "session"} or (decision == "deny" and scope != "once"):
            raise ValueError("invalid approval scope")
        pending = await self.repository.get_approval(workspace_id, approval_id)
        pending_task = await self.repository.get_task(workspace_id, str(pending["task_id"]))
        conversation = await self.repository.get_conversation(
            workspace_id, str(pending_task["conversation_id"])
        )
        request = RuntimeRunRequest(
            workspace_id=workspace_id,
            conversation_id=str(pending_task["conversation_id"]),
            task_id=str(pending_task["id"]),
            principal_id=principal_id,
            permission_mode=conversation["permission_mode"],
        )
        # Preflight permission grants before committing. A refusal must still
        # commit when model setup is unavailable: _drive reports a continuation
        # failure after the decision event, without undoing the refusal.
        graph = None
        if decision != "deny":
            graph = await self._graph_for(request)
            snapshot = await graph.aget_state({"configurable": {"thread_id": str(pending_task["id"])}})
            proposal = next((message for message in reversed(snapshot.values.get("messages", []))
                             if isinstance(message, AIMessage)), None)
            if not snapshot.interrupts or proposal is None:
                raise ValueError("approval has no matching runtime interrupt; cancel and start a new task")
            try:
                calls = self._review_calls(snapshot.interrupts, proposal.tool_calls)
            except RuntimeError as exc:
                raise ValueError("approval no longer matches the runtime checkpoint") from exc
            call = next((call for call in calls if call["id"] == pending["tool_call_id"]
                         and call["name"] == pending["tool_name"]), None)
            if call is None:
                raise ValueError("approval no longer matches the runtime checkpoint")
            review_context = _RunContext(self, request, asyncio.Queue())
            if self._execution_metadata is not None:
                review_context.execution_metadata = await self._execution_metadata(request)
            policy = self._policy_decision(call["name"], call["args"], review_context)
            if policy.outcome == "deny" or not self._matches_approval(pending, call["name"], policy):
                # Do not consume the user's version for a different normalized
                # path/payload, even if the checkpoint reused the same call ID.
                raise PermissionError("approval parameters or policy changed; cancel and start a new task")
            if scope != "once" and (not policy.reuse_scope or policy.reuse_scope != pending.get("reuse_scope")):
                raise PermissionError("this approval has no matching reusable scope; use one-time approval")
        approval = await self.repository.decide_approval(
            workspace_id, approval_id, principal_id, decision, version, note, scope
        )
        if decision == "allow":
            # Approval consumption is not execution. The tool still waits for
            # the rest of the native HITL batch to be reviewed.
            await self.repository.update_tool_call(
                str(approval["tool_call_id"]), "waiting_approval"
            )
        return await self._launch(
            # Repository stores the user's decision, not execution state.
            # _drive resumes the native HITL batch only when review is complete.
            None,
            request,
            graph=graph,
            initial_events=[
                (
                    "approval.decided",
                    {
                        **self._approval_payload(approval),
                        "decision": decision,
                    },
                )
            ],
        )

    async def recover(
        self, workspace_id: str, task_id: str, *,
        principal_id: str = "local-user", version: int | None = None, trace_id: str = "",
    ) -> RunHandle:
        """Continue the latest checkpoint without inventing a continuation payload."""
        self._require_open()
        # The public entry requires an optimistic version and an explicit owner.
        # Unversioned recovery remains internal to the existing runtime spike.
        if version is not None:
            if await self.repository.check_workspace_access(workspace_id, principal_id) != "owner":
                raise PermissionError("only the workspace owner can resume interrupted execution")
            if version < 1:
                raise ValueError("a positive task version is required")
        task = await self.repository.get_task(workspace_id, task_id)
        conversation = await self.repository.get_conversation(
            workspace_id, str(task["conversation_id"])
        )
        request = RuntimeRunRequest(
            workspace_id=workspace_id,
            conversation_id=str(task["conversation_id"]),
            task_id=task_id,
            principal_id=principal_id,
            permission_mode=conversation["permission_mode"],
            trace_id=trace_id,
        )
        async with self._conversation_locks[request.conversation_id]:
            task = await self.repository.get_task(workspace_id, task_id)
            if task["status"] in {"cancelled", "succeeded", "failed", "expired", "timed_out", "rejected"}:
                raise ValueError("terminal tasks cannot be replayed; send a new message")
            if version is not None and (task["status"] != "interrupted" or task["version"] != version):
                raise ValueError("task is not interrupted at this version; refresh its status")
            latest = await self.repository.latest_conversation_task(workspace_id, request.conversation_id)
            if latest and latest["id"] != task_id:
                raise ValueError("cannot recover an older turn after a newer turn was admitted")
            if self.has_active_conversation(request.conversation_id):
                raise ConversationBusyError("conversation still has an active run")
            if not await self.has_checkpoint(task_id):
                raise ValueError("no execution checkpoint is available for this task")
            graph = await self._graph_for(request)
            result_only = False
            if version is not None:
                conversation = await self.repository.get_conversation(workspace_id, request.conversation_id)
                if conversation.get("archived"):
                    raise ValueError("archived conversations cannot resume execution")
                snapshot = await graph.aget_state({"configurable": {"thread_id": task_id}})
                result_only = (bool(task.get("output_schema_json")) and "structured_response" in snapshot.values
                               and not snapshot.next and not snapshot.interrupts)
                if not snapshot.next and not snapshot.interrupts and not result_only:
                    raise ValueError("checkpoint has no unfinished execution; send a new message")
                direct = self._direct_tool_call(str(task["input_text"]), task_id)
                if direct is not None and direct["name"] not in {"file_read", "file_write"}:
                    raise ValueError("direct command recovery supports only file_read/file_write")
                # Execution replay stays file-only. A finished structured
                # checkpoint needs only its product result committed and never
                # re-executes the historical tools, regardless of their kinds.
                if not result_only and any(
                    call["name"] not in {"file_read", "file_write", OUTPUT_TOOL_NAME}
                    for message in snapshot.values.get("messages", []) if isinstance(message, AIMessage)
                    for call in message.tool_calls
                ):
                    raise ValueError("checkpoint includes tools outside file_read/file_write; replay is not supported")
            admitted = False
            try:
                if version is None:
                    await self.repository.update_task_status(task_id, "running")
                else:
                    await self.repository.resume_interrupted_task(workspace_id, task_id, version)
                admitted = True
                return await self._launch(None, request, graph=graph, initial_events=[
                    ("task.resumed", {"trace_id": trace_id, "execution_resumed": not result_only,
                                      "result_projection_only": result_only,
                                      "principal_id": principal_id, "previous_version": task["version"]}),
                ])
            except asyncio.CancelledError:
                if not admitted:
                    raise
                await self.repository.cancel_task(workspace_id, task_id)
                await self._publish_event(request, "task.cancelled", {"message": "cancelled before recovery started"})
                raise
            except Exception:
                if not admitted:
                    raise
                # Admission failed before a root took ownership. Preserve the
                # checkpoint for an explicit retry, not a phantom running task.
                await self.repository.update_task_status(task_id, "interrupted", "recovery could not start")
                await self._publish_event(request, "task.interrupted", {"message": "recovery could not start"})
                raise

    async def answer_question(self, workspace_id: str, question_id: str, principal_id: str,
                              answer: str, version: int) -> RunHandle:
        question = await self.repository.get_question(workspace_id, question_id)
        async with self._conversation_locks[str(question["conversation_id"])]:
            active = self._active_runs.get(str(question["task_id"]))
            if active is not None:
                await asyncio.shield(active.wait())
            conversation = await self.repository.get_conversation(workspace_id, str(question["conversation_id"]))
            request = RuntimeRunRequest(
                workspace_id=workspace_id, conversation_id=str(question["conversation_id"]),
                task_id=str(question["task_id"]), principal_id=principal_id,
                permission_mode=conversation["permission_mode"],
            )
            graph = await self._graph_for(request)
            snapshot = await graph.aget_state({"configurable": {"thread_id": request.task_id}})
            if not any(isinstance(item.value, dict) and item.value.get("kind") == "user_question"
                       and item.value.get("tool_call_id") == question["tool_call_id"]
                       for item in snapshot.interrupts):
                raise ValueError("question no longer matches the runtime checkpoint")
            question = await self.repository.answer_question(workspace_id, question_id, principal_id, answer, version)
            return await self._launch(None, request, graph=graph, initial_events=[
                ("question.answered", {"question_id": question_id, "answer": question["answer"],
                                       "version": question["version"]}),
            ])

    async def submit_input(self, workspace_id: str, task_id: str, content: str,
                           idempotency_key: str) -> dict[str, Any]:
        task = await self.repository.get_task(workspace_id, task_id)
        if str(task["input_text"]).startswith("/tool "):
            raise ValueError("direct tool commands cannot consume steering; send a new turn")
        existing = await self.repository.task_inputs(workspace_id, task_id)
        previous = next((item for item in existing if item["idempotency_key"] == idempotency_key), None)
        if previous is not None:
            if previous["content"] != content.strip():
                raise ValueError("idempotency key was already used for another message")
            return previous
        handle = self._active_runs.get(task_id)
        if handle is None or handle.root_task.done():
            raise ValueError("task is not executing; send a new turn")
        item = await self.repository.submit_task_input(workspace_id, task_id, content, idempotency_key)
        # Queued is only a receipt. Consumption is confirmed after a successful
        # model response containing this input, not merely after enqueueing it.
        await self._publish_event(RuntimeRunRequest(workspace_id=workspace_id,
            conversation_id=str(task["conversation_id"]), task_id=task_id),
            "task.input.queued", {"input_id": item["id"], "content": item["content"], "status": item["status"]})
        return item

    async def _question_interrupts(self, interrupts: Any, context: _RunContext) -> Command[Any] | None:
        """Project native interrupts and map durable user answers back to their IDs."""
        records = {item["tool_call_id"]: item for item in await self.repository.task_questions(
            context.request.workspace_id, context.request.task_id,
        )}
        answers: dict[str, str] = {}
        for item in interrupts:
            value = item.value
            if not isinstance(value, dict) or value.get("kind") != "user_question":
                raise RuntimeError("unsupported question interrupt")
            call_id = str(value["tool_call_id"])
            question = records.get(call_id)
            if question is not None and (question["title"] != value["title"]
                                         or json.loads(question["options_json"]) != value["options"]):
                raise PermissionError("question changed since it was displayed")
            if question is not None and question["status"] == "answered":
                answers[str(item.id)] = str(question["answer"])
                continue
            if question is None:
                question = await self.repository.request_question(
                    context.request.workspace_id, context.request.conversation_id, context.request.task_id,
                    call_id, context.request.principal_id, str(value["title"]), value["options"],
                )
            await self.repository.update_task_status(context.request.task_id, "waiting_approval")
            await self._emit(context, "question.requested", {
                "question_id": question["id"], "tool_call_id": call_id, "title": question["title"],
                "options": json.loads(question["options_json"]), "version": question["version"],
            })
        if len(answers) == len(interrupts):
            return Command(resume=answers)
        return None

    async def list_pending_approvals(
        self, workspace_id: str, conversation_id: str | None = None
    ) -> list[ApprovalRecord]:
        return await self.repository.list_approvals(workspace_id, conversation_id)

    async def _launch(
        self,
        graph_input: Any,
        request: RuntimeRunRequest,
        *,
        initial_events: Sequence[tuple[str, Mapping[str, object]]] = (),
        graph: Any = None,
    ) -> RunHandle:
        self._require_open()
        existing = self._active_runs.get(request.task_id)
        if existing is not None:
            if existing.root_task.done():
                self._active_runs.pop(request.task_id, None)
            else:
                raise RuntimeError(f"task is already running: {request.task_id}")
        product_task = await self.repository.get_task(request.workspace_id, request.task_id)
        # Approval/question/crash resumes use the original immutable contract,
        # not caller metadata or a later conversation setting.
        request = replace(request, output_schema=(
            json.loads(product_task["output_schema_json"]) if product_task.get("output_schema_json") else None
        ))
        cancelled = product_task["status"] == "cancelled"
        queue: asyncio.Queue[MaigoKiteEvent | object] = asyncio.Queue()
        context = _RunContext(self, request, queue)
        context.direct_tool = (
            None if cancelled else self._direct_tool_call(str(product_task["input_text"]), request.task_id)
        )
        self._require_open()
        for event_type, payload in initial_events:
            await self._emit(context, event_type, payload)
        task = asyncio.create_task(
            self._drive(graph, graph_input, context),
            name=f"maigokite-langgraph:{request.task_id}",
        )
        handle = RunHandle(request, task, queue, context.started)
        self._active_runs[request.task_id] = handle

        def release(_completed: asyncio.Task[None]) -> None:
            context.started.set()
            if self._active_runs.get(request.task_id) is handle:
                self._active_runs.pop(request.task_id, None)

        task.add_done_callback(release)
        return handle

    @staticmethod
    def _direct_tool_call(prompt: str, task_id: str) -> NativeToolCall | None:
        if not prompt.startswith("/tool "):
            return None
        try:
            _, name, payload = prompt.split(" ", 2)
            arguments = json.loads(payload)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("tool command format: /tool <name> <json-object>") from exc
        if not name or not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        return {
            "id": f"{task_id}_direct",
            "name": "exec_command" if name == "bash" else name,
            "args": arguments,
            "type": "tool_call",
        }

    async def _drive(self, graph: Any, graph_input: Any, context: _RunContext) -> None:
        config = {"configurable": {"thread_id": context.request.task_id}}
        try:
            context.started.set()
            await asyncio.sleep(0)
            task = await self.repository.get_task(context.request.workspace_id, context.request.task_id)
            context.max_subagent_calls = int(task.get("max_subagent_calls", 0))
            context.subagent_model_ids = json.loads(task.get("subagent_model_ids_json", "[]"))
            context.child_slots = asyncio.Semaphore(int(task.get("max_concurrent_subagents", 1)))
            if task["status"] == "cancelled":
                # Only explicit task cancellation ends the entire turn.
                await self._emit(context, "task.cancelled", {"message": "Task cancelled"})
                return
            # Preparation belongs to the root's cancellation/error boundary too,
            # including approval/question resumes whose decision already committed.
            request = context.request
            if graph is None:
                graph = await self._graph_for(request)
            context.execution_metadata = (
                await self._execution_metadata(request) if self._execution_metadata else {
                    "workspace_id": request.workspace_id,
                    "workspace_root": str(self.workspace_root),
                    "conversation_id": request.conversation_id,
                    "principal_id": request.principal_id,
                    "permission_mode": request.permission_mode,
                }
            )
            await self._refresh_execution_metadata(context)
            if context.max_subagent_calls == 0 or "delegate" not in context.execution_metadata.get(
                "enabled_tools", self._specs_by_name
            ):
                context.execution_metadata["enabled_tools"] = [
                    name for name in context.execution_metadata.get("enabled_tools", self._specs_by_name)
                    if name not in {"delegate", "list_subagent_models"}
                ]
            run_graph = True
            if graph_input is None:
                snapshot = await graph.aget_state(config)
                if not snapshot.values:
                    raise RuntimeError(
                        "runtime checkpoint is missing; the decision is saved but execution cannot resume"
                    )
                # A crash can follow the final checkpoint but precede the
                # product-result transaction. Only project that finished state;
                # do not invoke the graph or repeat completed tools.
                run_graph = bool(snapshot.next or snapshot.interrupts)
                if snapshot.interrupts:
                    message = next(
                        (
                            item for item in reversed(snapshot.values.get("messages", []))
                            if isinstance(item, AIMessage)
                        ),
                        None,
                    )
                    if message is None:
                        raise RuntimeError("approval checkpoint has no assistant tool calls")
                    await self._record_tool_proposals(message, context)
                    questions = all(isinstance(item.value, dict) and item.value.get("kind") == "user_question"
                                    for item in snapshot.interrupts)
                    if questions:
                        graph_input = await self._question_interrupts(snapshot.interrupts, context)
                        if graph_input is None:
                            return
                    else:
                        graph_input = await self._review_resume_input(snapshot.interrupts, context)
                    if graph_input is None:
                        # Reviewing another item is a product projection only;
                        # do not restart the graph while its interrupt is pending.
                        await self._handle_interrupts(snapshot.interrupts, context)
                        return
            # Scope exits (including cancellation cleanup) before any parent
            # terminal status is published. No detached child task survives it.
            if run_graph:
                async with asyncio.TaskGroup() as children:
                    context.child_scope = children
                    async with aclosing(graph.astream(
                        graph_input,
                        config=config,
                        context=context,
                        stream_mode=["messages", "updates", "checkpoints"],
                        durability="sync",
                        version="v2",
                    )) as stream:
                        # Close the generator even if cancellation/error occurs in
                        # event projection while LangGraph is suspended at yield.
                        async for event in stream:
                            await self._map_runtime_event(event, context)
            snapshot = await graph.aget_state(config)
            if snapshot.interrupts:
                return
            await self._complete_reasoning(context)
            task = await self.repository.get_task(
                context.request.workspace_id, context.request.task_id
            )
            if context.request.output_schema is not None and task["status"] == "running":
                if snapshot.next or "structured_response" not in snapshot.values:
                    raise StructuredResultError("模型未产生完整的结构化结果；任务未保存可供后续步骤读取的结果。")
                task, saved = await self.repository.complete_structured_task(
                    context.request.workspace_id, context.request.task_id, snapshot.values["structured_response"],
                )
                if saved is not None:
                    context.final_saved = True
                    await self._emit(
                        context, "message.completed", {"content": saved["content"], "message_id": saved["id"]}
                    )
            if str(task["status"]) == "cancelled":
                await self._emit(
                    context,
                    "task.cancelled",
                    {"message": "Task cancelled"},
                )
            elif context.request.output_schema is not None:
                if task["status"] != "succeeded":
                    raise StructuredResultError("任务已停止或失败，没有可读取的结构化结果。")
                await self._emit(context, "task.succeeded", {"result_ready": True, "output_mode": "json_schema"})
            else:
                await self.repository.update_task_status(
                    context.request.task_id, "succeeded"
                )
                await self._emit(context, "task.succeeded", {})
        except asyncio.CancelledError:
            await self._settle_compaction(graph, config, context, cancelled=True)
            task = await self.repository.cancel_task(
                context.request.workspace_id, context.request.task_id
            )
            # Completion may have committed immediately before stop arrived.
            # Do not publish cancelled over an immutable succeeded/failed row.
            status = str(task["status"])
            await self._emit(
                context,
                "task.cancelled" if status == "cancelled" else (
                    "task.succeeded" if status == "succeeded" else "task.failed"
                ),
                {"message": "cancelled by user" if status == "cancelled" else task.get("last_error", "")},
            )
            raise
        except Exception as exc:
            await self._settle_compaction(graph, config, context, cancelled=False)
            # Native TaskGroup groups a body error after draining children.
            # Keep a lone original error useful to users/inspection.
            while isinstance(exc, ExceptionGroup) and len(exc.exceptions) == 1:
                exc = exc.exceptions[0]
            if isinstance(exc, StructuredOutputError):
                exc = StructuredResultError("模型返回的结构化结果不完整或包含多个结果；未保存工作流结果。")
            task = await self.repository.get_task(
                context.request.workspace_id, context.request.task_id
            )
            if task["status"] == "cancelled":
                # A concurrent stop remains cancellation even if model/tool
                # cleanup raised an error on its way out.
                await self._emit(context, "task.cancelled", {"message": "Task cancelled"})
            elif task["status"] == "succeeded":
                # Event delivery failure cannot undo the atomic result commit.
                # Clients can always reconcile through task/result queries.
                await self._emit(context, "task.succeeded", {"result_ready": bool(task.get("output_result_json"))})
            else:
                await self.repository.update_task_status(
                    context.request.task_id, "failed", public_error(exc)
                )
                await self._emit(
                    context,
                    "task.failed",
                    {
                        "message": public_error(exc),
                        "error_type": type(exc).__name__,
                        "error_code": "STRUCTURED_OUTPUT_INVALID" if isinstance(exc, StructuredResultError) else None,
                    },
                )
        finally:
            await context.queue.put(_END)

    async def _settle_compaction(
        self, graph: Any, config: dict[str, Any], context: _RunContext, *, cancelled: bool,
    ) -> None:
        """Resolve an unprojected summary after graph cleanup, including commit/stop races."""
        if context.compaction_receipt is None:
            return
        try:
            snapshot = await graph.aget_state(config)
            await self._project_compaction(snapshot.values.get("messages", []), context)
        except Exception:
            # A checkpoint failure cannot be reported as a successful compaction.
            pass
        if context.compaction_receipt is not None:
            context.compaction_receipt = None
            await self._emit(context, "context.compaction.skipped" if cancelled else "context.compaction.failed", {
                "reason": "cancelled" if cancelled else "checkpoint_unconfirmed",
                "message": "压缩提交未确认；原始聊天记录保留，恢复时以持久上下文为准。",
            })

    async def _project_compaction(self, messages: Any, context: _RunContext) -> None:
        receipt = context.compaction_receipt
        if receipt is not None and any(getattr(item, "id", None) == receipt[0] for item in messages):
            await self._emit(context, "context.compacted", receipt[1])
            context.compaction_receipt = None

    async def has_checkpoint(self, task_id: str) -> bool:
        checkpointer = self._require_open()
        checkpoint = await checkpointer.aget_tuple(
            {"configurable": {"thread_id": task_id}}
        )
        return checkpoint is not None

    async def cancel(self, workspace_id: str, task_id: str) -> TaskRecord:
        """Cancel and await an active root task before publishing completion."""
        # Validate ownership before touching an in-memory handle.
        task = await self.repository.get_task(workspace_id, task_id)
        handle = self._active_runs.get(task_id)
        if handle is not None and not handle.root_task.done():
            await handle.cancel()
            return await self.repository.get_task(workspace_id, task_id)
        # Admission/resume owns this lock until its root is registered. Stop
        # must not race that gap and publish a terminal event before task.started.
        # Active execution above remains immediately cancellable without the lock.
        async with self._conversation_locks[str(task["conversation_id"])]:
            handle = self._active_runs.get(task_id)
            if handle is not None and not handle.root_task.done():
                await handle.cancel()
                return await self.repository.get_task(workspace_id, task_id)
            task = await self.repository.get_task(workspace_id, task_id)
            if str(task["status"]) in {"succeeded", "failed", "cancelled", "rejected", "timed_out", "expired"}:
                return task
            cancelled = await self.repository.cancel_task(workspace_id, task_id)
            if cancelled["status"] != "cancelled":
                return cancelled
            request = RuntimeRunRequest(
                workspace_id=workspace_id,
                conversation_id=str(cancelled["conversation_id"]),
                task_id=task_id,
            )
            await self._publish_event(
                request,
                "task.cancelled",
                {"message": "Cancelled by user"},
            )
            return cancelled

    async def _map_runtime_event(self, event: dict[str, Any], context: _RunContext) -> None:
        event_type = str(event.get("type") or "")
        data = event.get("data")
        if event_type == "checkpoints" and isinstance(data, dict):
            # sync durability commits the preceding step before this checkpoint
            # event. Never expose checkpoint values or summaries to the UI.
            await self._project_compaction(data.get("values", {}).get("messages", []), context)
            return
        if event_type == "messages" and isinstance(data, tuple) and data:
            metadata = data[1] if len(data) > 1 and isinstance(data[1], dict) else {}
            # Native summarization makes a model call too; its tokens are not
            # the user's answer. Only the public model node streams to the UI.
            if metadata.get("langgraph_node") != "model" or CHILD_TAG in metadata.get("tags", []):
                return
            await self._map_message(data[0], context)
            return
        if event_type != "updates" or not isinstance(data, dict):
            return
        interrupts = data.get("__interrupt__")
        if interrupts:
            await self._handle_interrupts(interrupts, context)
        # A tool batch can write messages more than once; LangGraph then streams
        # a list of channel updates, including the authoritative write_todos plan.
        updates = (
            (node, part)
            for node, update in data.items()
            for part in (update if isinstance(update, list) else [update])
        )
        for node, update in updates:
            if not isinstance(update, dict):
                continue
            messages = update.get("messages")
            if node == "model" and isinstance(messages, list) and any(
                isinstance(item, RemoveMessage) and item.id == REMOVE_ALL_MESSAGES for item in messages
            ):
                # Compaction's checkpoint replacement is not a new answer or
                # a new tool proposal; never replay its retained history.
                continue
            if isinstance(update.get("todos"), list):
                await self._emit(context, "plan.updated", {"items": update["todos"]})
            if not isinstance(messages, list):
                continue
            for message in messages:
                # HITL emits ToolMessages for rejected calls without invoking
                # the tool middleware. Project those results too.
                if isinstance(message, ToolMessage) and node in {
                    "tools", "HumanInTheLoopMiddleware.after_model"
                }:
                    await self._map_message(message, context)
                    continue
                if node != "model":
                    continue
                if not isinstance(message, AIMessage):
                    continue
                # Non-streaming integrations expose reasoning only on the final
                # message. Streaming updates contain the same text: don't repeat it.
                if not context.reasoning_streamed and (reasoning := self._reasoning_text(message)):
                    await self._append_reasoning(reasoning, context)
                await self._project_text_delta("", context, final=True)
                if not context.text_stream.reasoning_emitted:
                    _, reasoning = split_thinking(self._raw_message_text(message))
                    if reasoning:
                        await self._append_reasoning(reasoning, context, separate=True)
                # Each model turn has its own leading prefix, including turns
                # before/after a tool batch. Never carry parsing state across it.
                context.text_stream = ThinkTagStream()
                context.reasoning_streamed = False
                await self._complete_reasoning(context)
                if message.tool_calls:
                    await self._record_tool_proposals(message, context)
                else:
                    content = self._message_text(message)
                    if content and not context.final_saved and context.request.output_schema is None:
                        saved = await self.repository.append_message(
                            context.request.conversation_id,
                            "assistant",
                            content,
                        )
                        await self._emit(
                            context,
                            "message.completed",
                            {"content": content, "message_id": saved["id"]},
                        )
                        context.final_saved = True

    async def _map_message(self, message: BaseMessage, context: _RunContext) -> None:
        if isinstance(message, AIMessageChunk):
            reasoning = self._reasoning_text(message)
            if reasoning:
                context.reasoning_streamed = True
                await self._append_reasoning(reasoning, context)
            await self._project_text_delta(self._raw_message_text(message), context)
            return
        if isinstance(message, ToolMessage):
            if context.request.output_schema is not None and message.name == OUTPUT_TOOL_NAME:
                return
            call_id = str(message.tool_call_id or "")
            if not call_id or call_id in context.finished_tool_calls:
                return
            failed = getattr(message, "status", "success") == "error"
            content = self._message_text(message)
            product = message.artifact if isinstance(message.artifact, dict) else {}
            summary = str(product.get("summary") or content)[:500]
            stored = await self.repository.get_tool_call(call_id)
            tool_name = str(message.name or stored["tool_name"])
            status = "failed" if failed else "succeeded"
            if failed and stored["status"] == "denied":
                status = "denied"
            error_code = product.get("error_code")
            if status == "denied" and not error_code:
                error_code = "APPROVAL_REJECTED"
            policy_denied = status == "denied" and error_code == "TOOL_DENIED"
            await self.repository.update_tool_call(
                call_id,
                status,
                result=product.get("data", {"content": content}),
                summary=summary,
            )
            await self.repository.record_audit(
                context.request.workspace_id,
                context.request.principal_id,
                "tool.denied" if policy_denied else "tool.failed" if failed else "tool.completed",
                tool_name,
                "denied" if status == "denied" else "failed" if failed else "success",
                {"tool_call_id": call_id, "risk": stored["risk_level"], "summary": summary},
            )
            await self._emit(
                context,
                "tool.failed" if failed else "tool.completed",
                {
                    "tool_call_id": call_id,
                    "tool_name": tool_name,
                    **({"message": content} if not product else {}),
                    "summary": summary,
                    "data": product.get("data", {"content": content}),
                    "result": product.get("data", {"content": content}),
                    "error_code": error_code,
                    **({"denied": True} if status == "denied" else {}),
                    "user_hint": (
                        "该操作已驳回，未执行；继续处理任务的其他部分。"
                        if error_code == "APPROVAL_REJECTED" else product.get("user_hint")
                    ),
                    "action": product.get("action"),
                },
            )
            context.finished_tool_calls.add(call_id)
            change = product.get("file_change")
            if not failed and isinstance(change, dict):
                await self._emit(context, "file.changed", {**change, "tool_call_id": call_id})

    async def _record_tool_proposals(
        self, message: AIMessage, context: _RunContext
    ) -> None:
        context.latest_tool_calls = []
        for raw_call in message.tool_calls:
            if context.request.output_schema is not None and raw_call.get("name") == OUTPUT_TOOL_NAME:
                # LangChain's result binding is not a product capability and
                # must not create an approval, tool audit row or execution event.
                continue
            call: NativeToolCall = {
                "id": str(raw_call.get("id") or f"call_{uuid.uuid4().hex}"),
                "name": str(raw_call.get("name") or ""),
                "args": dict(raw_call.get("args") or {}),
            }
            if call["name"] == "delegate" and any(
                item["id"] == _tool_call_id(call) for item in context.latest_tool_calls
            ):
                raise ValueError("delegate call IDs must be unique within a model turn")
            context.latest_tool_calls.append(call)
            try:
                stored = await self.repository.get_tool_call(_tool_call_id(call))
            except KeyError:
                await self.repository.create_tool_call(
                    _tool_call_id(call),
                    context.request.workspace_id,
                    context.request.conversation_id,
                    context.request.task_id,
                    call["name"],
                    call["args"],
                    self._message_text(message),
                )
                await self._emit(
                    context,
                    "tool.proposed",
                    {
                        "tool_call_id": _tool_call_id(call),
                        "tool_name": call["name"],
                        "arguments": call["args"],
                    },
                )
            else:
                if (stored["workspace_id"] != context.request.workspace_id
                        or stored["conversation_id"] != context.request.conversation_id
                        or stored["task_id"] != context.request.task_id
                        or stored["tool_name"] != call["name"]):
                    raise ValueError("tool call ID already belongs to a different product operation")
                if call["name"] == "delegate" and (
                    stored["arguments_json"] != json.dumps(call["args"], ensure_ascii=False, sort_keys=True)
                    or _tool_call_id(call) in context.finished_tool_calls
                ):
                    raise ValueError("delegate call IDs cannot be reused for another dispatch")

    def _matches_approval(self, record: ApprovalRecord, name: str, decision: PolicyDecision) -> bool:
        return (
            record["tool_name"] == name
            and record["risk_level"] == decision.risk.name
            and self.repository.approval_matches_arguments(record, decision.arguments)
        )

    @staticmethod
    def _review_calls(interrupts: Any, ordered_calls: Sequence[NativeToolCall]) -> list[NativeToolCall]:
        """Match native HITL actions to the checkpoint's ordered call IDs."""
        remaining = list(ordered_calls)
        calls: list[NativeToolCall] = []
        for interrupt_value in interrupts:
            value = getattr(interrupt_value, "value", None)
            actions = value.get("action_requests") if isinstance(value, dict) else None
            if not isinstance(actions, list) or not actions:
                raise RuntimeError("unsupported runtime approval interrupt")
            for action in actions:
                index = next(
                    (
                        i for i, call in enumerate(remaining)
                        if call["name"] == action.get("name") and call["args"] == action.get("args")
                    ),
                    None,
                )
                if index is None:
                    raise RuntimeError("could not map HITL action to a checkpoint tool call")
                calls.append(remaining.pop(index))
        return calls

    async def _review_records(self, context: _RunContext) -> dict[str, ApprovalRecord]:
        records: dict[str, ApprovalRecord] = {}
        for status in ("pending", "consumed", "rejected", "expired"):
            for record in await self.repository.list_approvals(
                context.request.workspace_id, context.request.conversation_id, status
            ):
                if record["task_id"] == context.request.task_id:
                    records[str(record["tool_call_id"])] = record
        return records

    async def _review_resume_input(
        self, interrupts: Any, context: _RunContext
    ) -> Command[Any] | None:
        """Translate existing approval audit facts into one native HITL response.

        Incomplete review leaves LangGraph interrupted. No continuation or
        separate batch state is stored; a new process derives this from the
        same checkpoint and Repository decisions.
        """
        calls = self._review_calls(interrupts, context.latest_tool_calls)
        context.review_call_ids = {_tool_call_id(call) for call in calls}
        boundary_interrupts = [item for item in interrupts if item.value.get("source") == "tool_boundary"]
        context.boundary_review_ids = {
            _tool_call_id(call) for call in self._review_calls(boundary_interrupts, context.latest_tool_calls)
        }
        records = await self._review_records(context)
        reviewed = [records.get(_tool_call_id(call)) for call in calls]
        if any(record and record["status"] == "expired" for record in reviewed):
            raise PermissionError("approval has expired; start a new task")
        decisions: list[dict[str, Any]] = []
        for call, record in zip(calls, reviewed, strict=True):
            if record is not None and record["status"] == "rejected":
                decisions.append({"type": "reject", "message": self._rejection_message(record)})
                continue
            decision = self._policy_decision(call["name"], call["args"], context)
            if record is None and decision.authorization_id:
                # Native HITL still resumes the complete original batch. A
                # receipt can cover later siblings without inventing approvals.
                decisions.append({"type": "approve"})
                continue
            if record is None or record["status"] != "consumed":
                return None
            if decision.outcome == "deny" or not self._matches_approval(record, call["name"], decision):
                raise PermissionError("approved arguments no longer match the checkpoint tool call")
            decisions.append({"type": "approve"})
        # Native interrupt IDs also cover separate ToolNode boundary interrupts.
        # Preserve each interrupt's action ordering; never broadcast one decision
        # to other pending operations.
        responses = {}
        offset = 0
        for item in interrupts:
            count = len(item.value["action_requests"])
            responses[item.id] = {"decisions": decisions[offset:offset + count]}
            offset += count
        return Command(resume=responses)

    @staticmethod
    def _rejection_message(record: ApprovalRecord | None) -> str:
        message = (
            "The user rejected this operation. It was not executed. "
            "Do not retry it or bypass the refusal using another tool unless the user explicitly requests it. "
            "Continue the permitted parts of the task; if the goal requires this operation, explain the limitation."
        )
        if record is not None and record["note"]:
            message += f"\nUser's reason: {record['note']}"
        return message

    async def _handle_interrupts(
        self, interrupts: Any, context: _RunContext
    ) -> None:
        task = await self.repository.get_task(context.request.workspace_id, context.request.task_id)
        if task["status"] == "cancelled":
            # Explicit stop must not reopen another approval.
            await self._emit(context, "task.cancelled", {"message": "Task cancelled"})
            return
        if all(isinstance(item.value, dict) and item.value.get("kind") == "user_question" for item in interrupts):
            await self._question_interrupts(interrupts, context)
            return
        records = await self._review_records(context)
        for call in self._review_calls(interrupts, context.latest_tool_calls):
            approval = records.get(_tool_call_id(call))
            if approval is not None and approval["status"] == "rejected":
                # Keep the refused operation denied while reviewing siblings.
                continue
            if approval is not None and approval["status"] == "consumed":
                # Also correct this projection after a crash between committing
                # the approval decision and exposing the next review item.
                await self.repository.update_tool_call(_tool_call_id(call), "waiting_approval")
                continue
            if approval is not None and approval["status"] != "pending":
                raise PermissionError("approval is no longer pending")
            name = call["name"]
            decision = self._policy_decision(name, call["args"], context)
            if approval is None and decision.authorization_id:
                continue
            if decision.outcome == "deny" or (approval is None and decision.outcome != "approval_required"):
                raise RuntimeError("runtime interrupted for a tool the product policy did not gate")
            if approval is None:
                approval = await self.repository.create_approval(
                    context.request.workspace_id,
                    context.request.conversation_id,
                    context.request.principal_id,
                    context.request.task_id,
                    _tool_call_id(call),
                    decision.arguments,
                    decision.risk.name,
                    reuse_scope=decision.reuse_scope,
                )
                await self.repository.record_audit(
                    context.request.workspace_id,
                    context.request.principal_id,
                    "approval.created",
                    str(approval["id"]),
                    "pending",
                    {
                        "tool_call_id": _tool_call_id(call),
                        "tool_name": name,
                        "risk": decision.risk.name,
                    },
                )
            else:
                await self.repository.update_task_status(context.request.task_id, "waiting_approval")
            await self._emit(
                context,
                "task.waiting_approval",
                {
                    "tool_call_id": _tool_call_id(call),
                    "approval_id": approval["id"],
                },
            )
            await self._emit(
                context,
                "approval.requested",
                self._approval_payload(approval),
            )
            # Keep the existing single-approval API and UI. The complete batch
            # stays in the native interrupt until the final decision arrives.
            return
        raise RuntimeError("runtime requested approval for an already reviewed batch")

    async def _execute_product_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        context = request.runtime.context
        if not isinstance(context, _RunContext):
            raise RuntimeError("LangGraph runtime context is missing")
        tool_name = str(request.tool_call["name"])
        task = await self.repository.get_task(context.request.workspace_id, context.request.task_id)
        if task["status"] == "cancelled":
            return ToolMessage(
                content="Task cancelled; this tool was not executed.",
                tool_call_id=_tool_call_id(request.tool_call),
                name=tool_name,
                status="error",
            )
        arguments = dict(request.tool_call.get("args") or {})
        if await self.repository.task_inputs(context.request.workspace_id, context.request.task_id, pending=True):
            return ToolMessage(
                content=(
                    "New user input arrived. This proposed tool was not executed; "
                    "reconsider it after reading the user's message."
                ),
                tool_call_id=_tool_call_id(request.tool_call),
                name=tool_name,
                status="error",
            )
        record = (await self._review_records(context)).get(_tool_call_id(request.tool_call))
        await self._refresh_execution_metadata(context)
        authority = replace(context, execution_metadata=context.execution_metadata.copy())
        # Capture authority only after waiting for this tool's admission and
        # reading pending input/approvals. Earlier tools retain their own copy.
        request = replace(request, runtime=replace(request.runtime, context=authority))
        decision = self._policy_decision(tool_name, arguments, authority)
        approved = record if record is not None and record["status"] == "consumed" else None
        rejected = record if record is not None and record["status"] == "rejected" else None
        if (_tool_call_id(request.tool_call) in context.boundary_review_ids
                or (decision.outcome == "approval_required" and approved is None and rejected is None)):
            # A permission change can occur after after_model HITL, while an
            # earlier tool is executing. Suspend this ToolNode through native
            # LangGraph interrupt; existing Approval API/audit still authorize it.
            response = interrupt({
                "action_requests": [{"name": tool_name, "args": arguments,
                                     "description": decision.reason}],
                "review_configs": [{"action_name": tool_name, "allowed_decisions": ["approve", "reject"]}],
                "source": "tool_boundary",
            })
            if response["decisions"][0]["type"] == "reject":
                return ToolMessage(
                    content=self._rejection_message(rejected),
                    tool_call_id=_tool_call_id(request.tool_call), name=tool_name, status="error",
                )
        if rejected is not None:
            # Replaying a ToolNode after its interrupt was checkpointed away
            # must still respect the audit, even if the permission mode changed.
            return ToolMessage(
                content=self._rejection_message(rejected),
                tool_call_id=_tool_call_id(request.tool_call), name=tool_name, status="error",
            )
        if approved is not None or decision.outcome == "approval_required":
            # HITL owns suspension/resume. The product audit still owns the
            # authorization, including replay directly into a ToolNode after
            # the approval interrupt has already been checkpointed away.
            decision = self._policy_decision(tool_name, arguments, authority, approved=approved is not None)
            if approved is None or not self._matches_approval(approved, tool_name, decision):
                return ToolMessage(
                    content="No matching approval for these tool parameters; this tool was not executed.",
                    tool_call_id=_tool_call_id(request.tool_call), name=tool_name, status="error",
                )
        if decision.outcome != "allow":
            # ToolNode still projects the returned failure to the model and UI;
            # persist denial so it cannot be mistaken for a failed execution
            # or for the user's separate HITL rejection.
            await self.repository.update_tool_call(
                _tool_call_id(request.tool_call), "denied", risk_level=decision.risk.name,
            )
            return ToolMessage(
                content=decision.reason, tool_call_id=_tool_call_id(request.tool_call),
                name=tool_name, status="error", artifact={
                    "summary": decision.reason, "error_code": "TOOL_DENIED",
                    "user_hint": "当前权限或工具设置不允许此操作，未执行。",
                },
            )
        if decision.authorization_id:
            await self.repository.record_audit(
                context.request.workspace_id,
                context.request.principal_id,
                "authorization.used",
                decision.authorization_id,
                "allowed",
                {
                    "task_id": context.request.task_id,
                    "tool_call_id": _tool_call_id(request.tool_call),
                    "tool_name": tool_name,
                },
            )
        # Only a successful product policy decision selects the execution
        # boundary. Tool arguments can request escalation, never grant it.
        authority.execution_metadata["sandbox_mode"] = decision.sandbox_mode
        authority.execution_metadata["allow_network"] = decision.allow_network
        call_id = str(request.tool_call.get("id") or "")
        request = request.override(
            tool_call={**request.tool_call, "args": decision.arguments}
        )
        await self.repository.update_tool_call(
            call_id,
            "running",
            arguments=decision.arguments,
            risk_level=decision.risk.name,
        )
        await self._emit(
            context,
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
                await self._map_message(ToolMessage(
                    content=hint, tool_call_id=call_id, name=tool_name, status="error",
                    artifact={"summary": hint, "user_hint": hint, "error_code": "TOOL_CLEANUP_PENDING"},
                ), context)
            finally:
                raise exc from None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._map_message(ToolMessage(
                content=public_error(exc), tool_call_id=call_id, name=tool_name, status="error",
            ), context)
            raise
        if isinstance(result, ToolMessage):
            await self._map_message(result, context)
        return result

    async def _emit(
        self,
        context: _RunContext,
        event_type: str,
        payload: Mapping[str, object],
    ) -> None:
        async with context.event_lock:
            if context.terminal_emitted:
                return
            enriched = await self._publish_event(context.request, event_type, payload)
            if event_type in {"task.cancelled", "task.failed", "task.succeeded", "task.interrupted"}:
                context.terminal_emitted = True
            await context.queue.put(MaigoKiteEvent(event_type, enriched))

    async def _publish_event(
        self,
        request: RuntimeRunRequest,
        event_type: str,
        payload: Mapping[str, object],
    ) -> JSONObject:
        base = event_payload(event_type, {
            "task_id": request.task_id,
            "conversation_id": request.conversation_id,
            **payload,
        })
        if self._event_publisher is not None:
            await self._event_publisher(event_type, base)
            return base
        return await self.repository.append_task_event(event_type, base)

    @staticmethod
    def _approval_payload(approval: ApprovalRecord) -> ApprovalNotice:
        return approval_notice(approval)

    @classmethod
    def _message_text(cls, message: Any) -> str:
        text = cls._raw_message_text(message)
        # Tool output is user data, not provider thinking. A file containing a
        # leading <think> must remain intact in the tool result.
        return split_thinking(text)[0] if isinstance(message, AIMessage) else text

    @staticmethod
    def _raw_message_text(message: Any) -> str:
        value = getattr(message, "content", message)
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return ""
        parts: list[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif (
                isinstance(block, dict)
                and block.get("type") not in {"reasoning", "thinking"}
                and isinstance(block.get("text"), str)
            ):
                parts.append(block["text"])
        return "".join(parts)

    async def _project_text_delta(self, text: str, context: _RunContext, *, final: bool = False) -> None:
        separate = not context.text_stream.reasoning_emitted
        answer, reasoning = context.text_stream.feed(text, final=final)
        if reasoning:
            await self._append_reasoning(reasoning, context, separate=separate)
        if answer:
            await self._emit(context, "message.delta", {"content": answer})

    async def _append_reasoning(self, text: str, context: _RunContext, *, separate: bool = False) -> None:
        if not context.reasoning_started or context.reasoning_completed:
            await self._emit(context, "reasoning.started", {})
            if context.reasoning_parts:
                text = "\n\n" + text
            context.reasoning_started = True
            context.reasoning_completed = False
        elif separate:
            text = "\n\n" + text
        context.reasoning_parts.append(text)
        await self._emit(context, "reasoning.delta", {"content": text})

    async def _complete_reasoning(self, context: _RunContext) -> None:
        if context.reasoning_started and not context.reasoning_completed:
            await self._emit(context, "reasoning.completed", {"summary": "".join(context.reasoning_parts)})
            context.reasoning_completed = True

    @staticmethod
    def _reasoning_text(message: Any) -> str:
        # Prefer LangChain's public normalized blocks (including Responses API
        # summaries and Anthropic thinking); aliases are a compatible fallback.
        parts = [visible_reasoning(block) for block in getattr(message, "content_blocks", [])
                 if isinstance(block, dict) and block.get("type") in {"reasoning", "thinking"}]
        if text := "".join(parts):
            return text
        additional = getattr(message, "additional_kwargs", {}) or {}
        for key in ("reasoning_content", "reasoning", "thinking", "reasoning_details"):
            if text := visible_reasoning(additional.get(key)):
                return text
        return ""
