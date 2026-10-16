"""BaseTool interface and result types for the unified tool system."""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langchain_core.utils.pydantic import model_json_schema

from agent.errors import public_error
from agent.tools.execution_context import ExecutionMetadata
from agent.tools.validator import ToolValidator

if TYPE_CHECKING:
    from agent.runtime.run_context import ChildContext, RunContext


class BaseTool(ABC):
    """Abstract base class for all agent tools.

    Every concrete tool must subclass this and implement name, description,
    parameters_schema, and execute.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name of the tool."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Detailed English description of what the tool does."""
        ...

    @property
    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]:
        """JSON Schema describing the tool's parameters."""
        ...

    @abstractmethod
    async def execute(
        self, context: ExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult | ToolResultStream:
        """Execute the tool with the given arguments and context."""
        ...

    def to_langchain_tool(self) -> StructuredTool:
        """Expose the existing async implementation through public LangChain APIs.

        ToolNode owns invocation and cancellation; the runtime supplies product
        context. Credentials never become tool arguments or checkpoint state.
        JSON Schema validation is delegated to the existing standard validator.
        """
        schema = self.parameters_schema
        if {"runtime", "config"} & schema.get("properties", {}).keys():
            raise ValueError(f"{self.name}: runtime/config are reserved LangChain arguments")
        validator = ToolValidator(lambda _name: schema)

        async def invoke(runtime: ToolRuntime[object], **arguments: Any) -> ToolMessage:
            run = cast("RunContext | ChildContext", runtime.context)
            context = ExecutionContext(
                run.request.trace_id,
                run.request.task_id,
                str(runtime.tool_call_id),
                metadata=run.execution_metadata,
            )
            try:
                result = await self.execute(context, validator.sanitize(self.name, arguments))
                if isinstance(result, ToolResultStream):
                    result = await result.collect()
            except (OSError, ValueError) as exc:
                result = ToolResult.failure(
                    {}, public_error(exc), error_code="TOOL_EXECUTION_FAILED",
                )
            payload = {
                "data": result.data,
                "summary": result.summary,
                "raw": result.raw if isinstance(result.raw, str) else None,
                "error_code": result.error_code,
                "user_hint": result.user_hint,
                "action": result.action,
            }
            # Keep the full product artifact for the UI, without repeating
            # formatted raw text when structured model content already exists.
            model_payload = {key: value for key, value in payload.items() if value is not None}
            if result.data:
                model_payload.pop("raw", None)
            return ToolMessage(
                content=json.dumps(model_payload, ensure_ascii=False, default=str),
                # UI evidence is retained as an artifact, not duplicated in
                # model context. It is never a trusted authorization input.
                artifact={**payload, "file_change": result.metadata.get("file_change")},
                tool_call_id=str(runtime.tool_call_id),
                name=self.name,
                status="success" if result.success else "error",
            )

        return StructuredTool.from_function(
            coroutine=invoke, name=self.name, description=self.description,
            args_schema=schema,
        )


class ToolResult:
    """Standardized result returned by tool executions."""

    data: dict[str, Any]
    summary: str
    raw: str | bytes | None
    metadata: dict[str, Any]
    success: bool
    error_code: str | None
    user_hint: str | None
    action: dict[str, str] | None

    def __init__(
        self,
        data: dict[str, Any],
        summary: str,
        raw: str | bytes | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        success: bool = True,
        error_code: str | None = None,
        user_hint: str | None = None,
        action: dict[str, str] | None = None,
    ) -> None:
        self.data = data
        self.summary = summary
        self.raw = raw
        self.metadata = metadata or {}
        self.success = success
        self.error_code = error_code
        self.user_hint = user_hint
        self.action = action

    @classmethod
    def failure(
        cls,
        data: dict[str, Any],
        summary: str,
        *,
        error_code: str,
        user_hint: str | None = None,
        action: dict[str, str] | None = None,
        raw: str | bytes | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Build a recoverable tool failure that the model may still reason about."""
        return cls(
            data,
            summary,
            raw,
            metadata,
            success=False,
            error_code=error_code,
            user_hint=user_hint,
            action=action,
        )


class ToolResultStream(ABC):
    """Streaming variant of ToolResult for long-running operations."""

    @abstractmethod
    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        ...

    @abstractmethod
    async def collect(self) -> ToolResult:
        """Consume the stream and return a consolidated ToolResult."""
        ...


class ExecutionContext:
    """Context passed to tool executions."""

    deadline: datetime
    cancellation_token: bool
    trace_id: str
    task_id: str
    step_id: str
    metadata: ExecutionMetadata

    def __init__(
        self,
        trace_id: str,
        task_id: str,
        step_id: str,
        deadline: datetime | None = None,
        cancellation_token: bool = False,
        metadata: ExecutionMetadata | None = None,
    ) -> None:
        self.deadline = deadline or datetime.max
        self.cancellation_token = cancellation_token
        self.trace_id = trace_id
        self.task_id = task_id
        self.step_id = step_id
        self.metadata = metadata or {}


class LangChainStructuredToolBridge(BaseTool):
    """Bridge that wraps a LangChain StructuredTool into our BaseTool interface.

    This allows leveraging LangChain's StructuredTool for parameter schema
    validation and LLM-friendly descriptions while maintaining compatibility
    with the project's unified BaseTool contract.
    """

    def __init__(
        self,
        structured_tool: StructuredTool,
        name_override: str | None = None,
    ) -> None:
        self._structured_tool = structured_tool
        self._name = name_override or structured_tool.name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._structured_tool.description

    @property
    def parameters_schema(self) -> dict[str, Any]:
        """Extract JSON schema from the underlying LangChain tool definition."""
        schema = self._structured_tool.args_schema
        if isinstance(schema, dict):
            return schema
        # The SDK handles both supported Pydantic schema versions. Invalid
        # schemas must fail here rather than silently becoming an empty schema.
        return model_json_schema(schema)

    async def execute(
        self, context: ExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        """Call the wrapped LangChain function and adapt its output."""
        func = self._structured_tool.func
        if func is None:
            raise RuntimeError(f"No function bound to tool {self._name}")

        # If the function is async, await it; otherwise run synchronously
        if asyncio.iscoroutinefunction(func):
            raw_result = await func(**arguments)
        else:
            raw_result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: func(**arguments)
            )

        # Normalize the result into a dict
        if isinstance(raw_result, dict):
            result_dict = raw_result
        elif isinstance(raw_result, str):
            # Attempt to parse structured strings as JSON
            try:
                parsed = json.loads(raw_result)
                result_dict = parsed if isinstance(parsed, dict) else {"output": raw_result}
            except (json.JSONDecodeError, ValueError):
                result_dict = {"output": raw_result}
        else:
            result_dict = {"output": str(raw_result)}

        raw_output = result_dict.get("output", "")
        return ToolResult(
            data=result_dict,
            summary=str(raw_output)[:500],
            raw=raw_output if isinstance(raw_output, str) else str(raw_output),
            metadata={"tool": self._name},
        )
