from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from agent.models.native_model import build_chat_model
from agent.runtime import (
    LangGraphRuntime,
    ProductToolSpec,
    RuntimeRunRequest,
)
from agent.tools.base import BaseTool, ExecutionContext, ToolResult
from agent.tools.native.file_read import FileReadTool
from agent.tools.native.file_write import FileWriteParams, FileWriteTool
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class RuleToolModel(BaseChatModel):
    tool_name: str
    tool_args: dict[str, Any]
    final_text: str = "tool result accepted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> RuleToolModel:
        return self

    def _reply(self, messages: list[BaseMessage]) -> AIMessage:
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content=self.final_text)
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": self.tool_name,
                    "args": self.tool_args,
                    "id": f"call-{self.tool_name}",
                    "type": "tool_call",
                }
            ],
        )

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _agenerate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return self._generate(messages, **kwargs)

    async def _astream(self, messages: list[BaseMessage], **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        message = self._reply(messages)
        if message.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    additional_kwargs={"reasoning_content": "selecting tool"},
                    tool_calls=message.tool_calls,
                )
            )
            return
        for character in str(message.content):
            yield ChatGenerationChunk(message=AIMessageChunk(content=character))

    @property
    def _llm_type(self) -> str:
        return "maigokite-rule-tool-model"


class EchoModel(BaseChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> EchoModel:
        return self

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    async def _agenerate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return self._generate(messages, **kwargs)

    async def _astream(self, messages: list[BaseMessage], **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=AIMessageChunk(content="ok"))

    @property
    def _llm_type(self) -> str:
        return "maigokite-echo-model"


class SlowStreamModel(BaseChatModel):
    started: Any = Field(default_factory=asyncio.Event, exclude=True)
    cancelled: Any = Field(default_factory=asyncio.Event, exclude=True)

    def bind_tools(self, tools: Any, **kwargs: Any) -> SlowStreamModel:
        return self

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        raise AssertionError("the cancellation test must use the async stream")

    async def _astream(self, messages: list[BaseMessage], **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        try:
            self.started.set()
            yield ChatGenerationChunk(message=AIMessageChunk(content="first"))
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()

    @property
    def _llm_type(self) -> str:
        return "maigokite-slow-stream-model"


class ErrorModel(BaseChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> ErrorModel:
        return self

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        raise RuntimeError("provider unavailable")

    async def _astream(self, messages: list[BaseMessage], **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        raise RuntimeError("provider unavailable")
        yield

    @property
    def _llm_type(self) -> str:
        return "maigokite-error-model"


class StubProductTool(BaseTool):
    def __init__(self, name: str, description: str = "test tool") -> None:
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {}

    async def execute(self, context: ExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        raise AssertionError("the runtime StructuredTool must execute directly")


class ValueArgs(BaseModel):
    value: str


class SimulatedProcessCrash(BaseException):
    pass


def assert_process_crash(error: BaseException) -> None:
    if isinstance(error, BaseExceptionGroup):
        assert error.exceptions
        for child in error.exceptions:
            assert_process_crash(child)
    else:
        assert isinstance(error, SimulatedProcessCrash)


def adapter_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "workspace_root": tmp_path,
        "checkpoint_path": tmp_path / "checkpoints.sqlite",
        "product_db_path": tmp_path / "product.sqlite",
    }


async def collect_types(handle: Any) -> list[str]:
    events = await handle.collect()
    await handle.wait()
    return [event.type for event in events]


@pytest.mark.asyncio
async def test_read_tool_loop_and_stable_ui_events(tmp_path: Path) -> None:
    (tmp_path / "input.txt").write_text("runtime spike", encoding="utf-8")
    model = RuleToolModel(
        tool_name="file_read",
        tool_args={"path": "input.txt"},
        final_text="I read the file.",
    )
    async with LangGraphRuntime(model=model, **adapter_paths(tmp_path)) as adapter:
        handle = await adapter.start("read the input file")
        events = await handle.collect()
        await handle.wait()

    event_types = [event.type for event in events]
    assert event_types == [
        "task.started",
        "context.history",
        "context.updated",
        "context.updated",
        "model.call.started",
        "reasoning.started",
        "reasoning.delta",
        "model.call.completed",
        "reasoning.completed",
        "tool.proposed",
        "tool.started",
        "tool.completed",
        "context.updated",
        "context.updated",
        "model.call.started",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.delta",
        "model.call.completed",
        "message.completed",
        "task.succeeded",
    ]
    proposed = next(event for event in events if event.type == "tool.proposed")
    assert proposed.payload["tool_name"] == "file_read"
    assert proposed.payload["arguments"] == {"path": "input.txt"}
    assert not any(event.type == "approval.requested" for event in events)
    assert all("langgraph" not in event.payload for event in events)


@pytest.mark.asyncio
async def test_invalid_tool_arguments_fail_before_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[dict[str, Any]] = []

    async def forbidden_read(_tool: Any, _context: ExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        executed.append(arguments)
        raise AssertionError("invalid arguments must be rejected before execution")

    monkeypatch.setattr(FileReadTool, "execute", forbidden_read)
    assert "path" in FileReadTool().parameters_schema["required"]
    model = RuleToolModel(
        tool_name="file_read",
        tool_args={},
        final_text="The tool input was invalid.",
    )
    async with LangGraphRuntime(model=model, **adapter_paths(tmp_path)) as adapter:
        handle = await adapter.start("call file_read without its required path")
        events = await handle.collect()
        await handle.wait()

    event_types = [event.type for event in events]
    assert "tool.failed" in event_types
    failure = next(event for event in events if event.type == "tool.failed")
    assert failure.payload["tool_name"] == "file_read"
    assert failure.payload["error_code"] == "TOOL_DENIED"
    assert failure.payload["denied"] is True
    assert failure.payload["summary"] == "invalid arguments for tool 'file_read'"
    assert "tool.started" not in event_types
    assert executed == []
    assert event_types[-1] == "task.succeeded"


@pytest.mark.asyncio
async def test_approval_survives_restart_and_executes_side_effect_once(
    tmp_path: Path,
) -> None:
    model = RuleToolModel(
        tool_name="file_write",
        tool_args={"path": "approved.txt", "content": "written once"},
    )
    adapter = LangGraphRuntime(model=model, **adapter_paths(tmp_path))
    await adapter.open()
    first = await adapter.start("write the approved file")
    first_events = await first.collect()
    await first.wait()
    approvals = await adapter.list_pending_approvals("personal")
    assert [event.type for event in first_events][-2:] == [
        "task.waiting_approval",
        "approval.requested",
    ]
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval["version"] == 1
    assert approval["arguments_hash"]
    assert not (tmp_path / "approved.txt").exists()
    await adapter.close()

    restarted = LangGraphRuntime(
        model=RuleToolModel(
            tool_name="file_write",
            tool_args={"path": "approved.txt", "content": "written once"},
        ),
        **adapter_paths(tmp_path),
    )
    await restarted.open()
    resumed = await restarted.resume_approval(
        "personal",
        approval["id"],
        "local-user",
        "allow",
        approval["version"],
    )
    resumed_types = await collect_types(resumed)
    assert resumed_types == [
        "approval.decided",
        "tool.started",
        "tool.completed",
        "file.changed",
        "context.updated",
        "context.updated",
        "model.call.started",
        *("message.delta" for _ in "tool result accepted"),
        "model.call.completed",
        "message.completed",
        "task.succeeded",
    ]
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "written once"
    receipt = await restarted.repository.get_approval("personal", approval["id"])
    assert receipt["status"] == "consumed"
    written_at = (tmp_path / "approved.txt").stat().st_mtime_ns
    events_before = await restarted.repository.list_task_events("personal", resumed.request.conversation_id)
    with pytest.raises(ValueError, match="no matching runtime interrupt"):
        await restarted.resume_approval(
            "personal",
            approval["id"],
            "local-user",
            "allow",
            approval["version"],
        )
    assert await restarted.repository.get_approval("personal", approval["id"]) == receipt
    assert await restarted.repository.list_task_events("personal", resumed.request.conversation_id) == events_before
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "written once"
    assert (tmp_path / "approved.txt").stat().st_mtime_ns == written_at
    await restarted.close()


@pytest.mark.asyncio
async def test_rejected_approval_never_executes_tool(tmp_path: Path) -> None:
    model = RuleToolModel(
        tool_name="file_write",
        tool_args={"path": "rejected.txt", "content": "must not exist"},
        final_text="The write was rejected.",
    )
    async with LangGraphRuntime(model=model, **adapter_paths(tmp_path)) as adapter:
        initial = await adapter.start("try to write")
        await initial.collect()
        await initial.wait()
        approval = (await adapter.list_pending_approvals("personal"))[0]
        rejected = await adapter.resume_approval(
            "personal",
            approval["id"],
            "local-user",
            "deny",
            approval["version"],
            note="Keep this in the conversation instead of writing a file.",
        )
        events = await rejected.collect()
        await rejected.wait()

    assert not (tmp_path / "rejected.txt").exists()
    event_types = [event.type for event in events]
    assert "tool.started" not in event_types
    assert "tool.completed" not in event_types
    assert "task.cancelled" not in event_types
    assert "task.failed" not in event_types
    assert event_types[-1] == "task.succeeded"
    result = next(event for event in events if event.type == "tool.failed")
    assert result.payload["error_code"] == "APPROVAL_REJECTED"
    assert "Keep this in the conversation" in result.payload["message"]
    answer = next(event for event in events if event.type == "message.completed")
    assert answer.payload["content"] == model.final_text


@pytest.mark.asyncio
async def test_model_stream_cancellation_stops_tokens(tmp_path: Path) -> None:
    model = SlowStreamModel()
    async with LangGraphRuntime(model=model, **adapter_paths(tmp_path)) as adapter:
        handle = await adapter.start("stream forever")
        iterator = handle.events().__aiter__()
        seen = []
        while True:
            event = await asyncio.wait_for(anext(iterator), timeout=2)
            seen.append(event)
            if event.type == "message.delta":
                break
        await handle.cancel()
        async for event in iterator:
            seen.append(event)
        task = await adapter.repository.get_task("personal", handle.request.task_id)

    assert model.cancelled.is_set()
    assert task["status"] == "cancelled"
    assert [event.type for event in seen].count("message.delta") == 1
    assert seen[-1].type == "task.cancelled"


@pytest.mark.asyncio
async def test_async_tool_cancellation_propagates_cleanup(tmp_path: Path) -> None:
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def blocking_tool(value: str) -> str:
        """Block until the root run is cancelled."""
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
        return value

    runtime_tool = StructuredTool.from_function(
        coroutine=blocking_tool,
        name="sleep",
        description="Wait for cancellation.",
        args_schema=ValueArgs,
    )
    spec = ProductToolSpec(StubProductTool("sleep"), runtime_tool)
    model = RuleToolModel(tool_name="sleep", tool_args={"value": "x"})
    async with LangGraphRuntime(
        model=model,
        tools=[spec],
        **adapter_paths(tmp_path),
    ) as adapter:
        handle = await adapter.start(
            "run the slow tool",
            RuntimeRunRequest(permission_mode="full_access"),
        )
        iterator = handle.events().__aiter__()
        seen = []
        while True:
            event = await asyncio.wait_for(anext(iterator), timeout=2)
            seen.append(event)
            if event.type == "tool.started":
                break
        await asyncio.wait_for(started.wait(), timeout=2)
        await handle.cancel()
        async for event in iterator:
            seen.append(event)

    assert cleaned.is_set()
    assert "tool.started" in [event.type for event in seen]
    assert "tool.completed" not in [event.type for event in seen]
    assert seen[-1].type == "task.cancelled"


@pytest.mark.asyncio
async def test_provider_error_maps_to_task_failed(tmp_path: Path) -> None:
    async with LangGraphRuntime(
        model=ErrorModel(),
        **adapter_paths(tmp_path),
    ) as adapter:
        handle = await adapter.start("fail")
        events = await handle.collect()
        await handle.wait()
        task = await adapter.repository.get_task("personal", handle.request.task_id)

    assert task["status"] == "failed"
    assert events[-1].type == "task.failed"
    assert events[-1].payload["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_crash_after_approval_before_effect_replays_tool_once(
    tmp_path: Path,
) -> None:
    target = tmp_path / "after-approval.txt"

    async def crash_before_effect(
        path: str,
        content: str,
        encoding: str = "utf-8",
        create_dirs: bool = True,
    ) -> dict[str, Any]:
        """Simulate process death before the first external write."""
        raise SimulatedProcessCrash()

    crashing_tool = StructuredTool.from_function(
        coroutine=crash_before_effect,
        name="file_write",
        description="Crash before writing.",
        args_schema=FileWriteParams,
    )
    model = RuleToolModel(
        tool_name="file_write",
        tool_args={"path": "after-approval.txt", "content": "once"},
    )
    adapter = LangGraphRuntime(
        model=model,
        tools=[ProductToolSpec(FileWriteTool(), crashing_tool)],
        **adapter_paths(tmp_path),
    )
    await adapter.open()
    initial = await adapter.start("write after approval")
    await initial.collect()
    await initial.wait()
    approval = (await adapter.list_pending_approvals("personal"))[0]
    crashing = await adapter.resume_approval(
        "personal",
        approval["id"],
        "local-user",
        "allow",
        approval["version"],
    )
    await crashing.collect()
    with pytest.raises((SimulatedProcessCrash, BaseExceptionGroup)) as failure:
        await crashing.wait()
    assert_process_crash(failure.value)
    assert not target.exists()
    await adapter.close()

    write_tool = FileWriteTool()
    restarted = LangGraphRuntime(
        model=RuleToolModel(
            tool_name="file_write",
            tool_args={"path": "after-approval.txt", "content": "once"},
        ),
        tools=[ProductToolSpec(write_tool, write_tool.to_langchain_tool())],
        **adapter_paths(tmp_path),
    )
    await restarted.open()
    recovered = await restarted.recover("personal", crashing.request.task_id)
    recovered_events = await recovered.collect()
    await recovered.wait()
    await restarted.close()

    assert target.read_text(encoding="utf-8") == "once"
    assert [event.type for event in recovered_events].count("tool.started") == 1


@pytest.mark.asyncio
async def test_side_effect_can_replay_after_crash_before_tool_result_checkpoint(
    tmp_path: Path,
) -> None:
    effect_path = tmp_path / "effect-count.txt"

    async def crashing_write(
        path: str,
        content: str,
        encoding: str = "utf-8",
        create_dirs: bool = True,
    ) -> dict[str, Any]:
        """Create the side effect and then simulate process death."""
        count = int(effect_path.read_text() or "0") if effect_path.exists() else 0
        effect_path.write_text(str(count + 1), encoding="utf-8")
        raise SimulatedProcessCrash()

    crashing_runtime_tool = StructuredTool.from_function(
        coroutine=crashing_write,
        name="file_write",
        description="Write and then crash.",
        args_schema=FileWriteParams,
    )
    crashing_spec = ProductToolSpec(FileWriteTool(), crashing_runtime_tool)
    model = RuleToolModel(
        tool_name="file_write",
        tool_args={"path": "effect-count.txt", "content": "ignored"},
    )
    adapter = LangGraphRuntime(
        model=model,
        tools=[crashing_spec],
        **adapter_paths(tmp_path),
    )
    await adapter.open()
    initial = await adapter.start("perform a crash-prone write")
    await initial.collect()
    await initial.wait()
    approval = (await adapter.list_pending_approvals("personal"))[0]
    crashing = await adapter.resume_approval(
        "personal",
        approval["id"],
        "local-user",
        "allow",
        approval["version"],
    )
    await crashing.collect()
    with pytest.raises((SimulatedProcessCrash, BaseExceptionGroup)) as failure:
        await crashing.wait()
    assert_process_crash(failure.value)
    assert effect_path.read_text(encoding="utf-8") == "1"
    await adapter.close()

    async def recovered_write(
        path: str,
        content: str,
        encoding: str = "utf-8",
        create_dirs: bool = True,
    ) -> dict[str, Any]:
        """Repeat the same side effect and return normally."""
        count = int(effect_path.read_text(encoding="utf-8"))
        effect_path.write_text(str(count + 1), encoding="utf-8")
        return {"path": path, "count": count + 1}

    recovered_tool = StructuredTool.from_function(
        coroutine=recovered_write,
        name="file_write",
        description="Recover the write.",
        args_schema=FileWriteParams,
    )
    restarted = LangGraphRuntime(
        model=RuleToolModel(
            tool_name="file_write",
            tool_args={"path": "effect-count.txt", "content": "ignored"},
        ),
        tools=[ProductToolSpec(FileWriteTool(), recovered_tool)],
        **adapter_paths(tmp_path),
    )
    await restarted.open()
    recovered = await restarted.recover("personal", crashing.request.task_id)
    recovered_events = await recovered.collect()
    await recovered.wait()
    await restarted.close()

    assert effect_path.read_text(encoding="utf-8") == "2"
    assert "tool.started" in [event.type for event in recovered_events]
    assert "task.succeeded" in [event.type for event in recovered_events]


@pytest.mark.asyncio
async def test_file_write_reconciles_matching_content_after_result_checkpoint_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "reconciled.txt"

    async def write_then_crash(
        path: str,
        content: str,
        encoding: str = "utf-8",
        create_dirs: bool = True,
    ) -> dict[str, Any]:
        Path(path).write_text(content, encoding=encoding)
        raise SimulatedProcessCrash()

    crashing_tool = StructuredTool.from_function(
        coroutine=write_then_crash,
        name="file_write",
        description="Write content and crash before returning.",
        args_schema=FileWriteParams,
    )
    model = RuleToolModel(
        tool_name="file_write",
        tool_args={"path": "reconciled.txt", "content": "stable content"},
    )
    adapter = LangGraphRuntime(
        model=model,
        tools=[ProductToolSpec(FileWriteTool(), crashing_tool)],
        **adapter_paths(tmp_path),
    )
    await adapter.open()
    initial = await adapter.start("write safely")
    await initial.collect()
    await initial.wait()
    approval = (await adapter.list_pending_approvals("personal"))[0]
    crashed = await adapter.resume_approval(
        "personal",
        approval["id"],
        "local-user",
        "allow",
        approval["version"],
    )
    await crashed.collect()
    with pytest.raises((SimulatedProcessCrash, BaseExceptionGroup)) as failure:
        await crashed.wait()
    assert_process_crash(failure.value)
    await adapter.close()
    assert target.read_text(encoding="utf-8") == "stable content"

    # Exercise the production tool, not a fake callable skipped by a runtime
    # name special-case. Observe actual write opens without mocking the compare.
    from builtins import open as real_open

    write_opens: list[str] = []

    def observe_open(path, mode="r", *args, **kwargs):
        if Path(path) == target and any(flag in mode for flag in "wax+"):
            write_opens.append(mode)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("agent.tools.native.file_write.open", observe_open, raising=False)
    before = target.stat()
    recovery_tool = FileWriteTool()
    restarted = LangGraphRuntime(
        model=RuleToolModel(
            tool_name="file_write",
            tool_args={"path": "reconciled.txt", "content": "stable content"},
        ),
        tools=[ProductToolSpec(recovery_tool, recovery_tool.to_langchain_tool())],
        **adapter_paths(tmp_path),
    )
    await restarted.open()
    recovered = await restarted.recover("personal", crashed.request.task_id)
    events = await recovered.collect()
    await recovered.wait()
    await restarted.close()

    assert write_opens == []
    assert target.stat().st_mtime_ns == before.st_mtime_ns
    assert target.read_text(encoding="utf-8") == "stable content"
    completed = next(event for event in events if event.type == "tool.completed")
    assert completed.payload["result"]["reconciled"] is True
    assert completed.payload["result"]["bytes_written"] == 0
    change = next(event for event in events if event.type == "file.changed")
    assert change.payload["reconciled"] is True
    assert change.payload["before_hash"] is None
    assert change.payload["diff_truncated"] is True


@pytest.mark.asyncio
async def test_async_sqlite_checkpoint_handles_small_desktop_concurrency(
    tmp_path: Path,
) -> None:
    paths = adapter_paths(tmp_path)
    start = time.perf_counter()
    async with LangGraphRuntime(model=EchoModel(), **paths) as adapter:
        handles = await asyncio.gather(*(adapter.start(f"request {index}") for index in range(12)))
        event_sets = await asyncio.gather(*(handle.collect() for handle in handles))
        await asyncio.gather(*(handle.wait() for handle in handles))
    elapsed = time.perf_counter() - start

    assert all(events[-1].type == "task.succeeded" for events in event_sets)
    assert paths["checkpoint_path"].stat().st_size < 5_000_000
    assert elapsed < 10


def test_model_factory_uses_native_provider_integrations() -> None:
    from langchain_anthropic import ChatAnthropic
    from langchain_ollama import ChatOllama
    from langchain_openai import ChatOpenAI

    openai = build_chat_model(
        provider="openai",
        model="gpt-test",
        api_key="test-key",
    )
    anthropic = build_chat_model(
        provider="anthropic",
        model="claude-test",
        api_key="test-key",
    )
    ollama = build_chat_model(
        provider="ollama",
        model="qwen-test",
        base_url="http://127.0.0.1:11434",
    )
    compatible = build_chat_model(
        provider="openai_compatible",
        model="local-test",
        base_url="http://127.0.0.1:1234/v1",
    )

    assert isinstance(openai, ChatOpenAI)
    assert isinstance(anthropic, ChatAnthropic)
    assert isinstance(ollama, ChatOllama)
    assert isinstance(compatible, ChatOpenAI)
    assert str(compatible.openai_api_base).rstrip("/") == "http://127.0.0.1:1234/v1"
