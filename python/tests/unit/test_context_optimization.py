from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from agent.config_manager import ConfigManager
from agent.data_contracts import JSONObject
from agent.models.context_capacity import ContextCapacity
from agent.models.runtime import ChatModelRuntime, ChatModelSettings
from agent.models.usage import message_usage
from agent.repository import SQLiteRepository
from agent.runtime import LangGraphRuntime, ProductToolSpec, RuntimeRunRequest
from agent.runtime.context import ContextBudgetExceeded, ContextBudgetMiddleware, SafeSummarizationMiddleware
from agent.skill_registry import SkillDefinition, SkillRegistry
from agent.tools.native.skill import SkillTool
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.runtime import Runtime
from pydantic import Field

from tests.fixtures.chat_model import FakeModel
from tests.fixtures.runtime_context import BudgetContext


class InspectModel(BaseChatModel):
    requests: list[list[BaseMessage]] = Field(default_factory=list)
    summary_error: bool = False
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    repeat_tool: bool = False

    def bind_tools(self, tools: Any, **kwargs: Any) -> InspectModel:
        return self

    def _reply(self, messages: list[BaseMessage]) -> AIMessage:
        self.requests.append(messages)
        if "<primary_objective>" in str(messages[0].content):
            if self.summary_error:
                raise RuntimeError("summary provider unavailable")
            return AIMessage(content="SUMMARY_ONLY: intent, completed work, remaining tasks")
        if self.tool_name and (self.repeat_tool or not isinstance(messages[-1], ToolMessage)):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": self.tool_name,
                        "args": self.tool_args,
                        "id": f"call-{len(self.requests)}",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(
            content="final answer",
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "input_token_details": {"cache_read": 60},
            },
        )

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _agenerate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return self._generate(messages, **kwargs)

    async def _astream(self, messages: list[BaseMessage], **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        reply = self._reply(messages)
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=reply.content,
                tool_calls=reply.tool_calls,
                usage_metadata=reply.usage_metadata,
            )
        )

    @property
    def _llm_type(self) -> str:
        return "context-test"


@pytest.mark.asyncio
async def test_context_budget_preserves_messages_and_counts_tools():
    # Count tools without altering conversation messages or stable system instructions.
    messages = [HumanMessage("hello"), AIMessage("answer"), HumanMessage("next")]
    original = list(messages)
    request = ModelRequest(model=InspectModel(), messages=messages, system_message=SystemMessage("stable rules"),
                              tools=[], runtime=Runtime(context=BudgetContext(
                                  context_capacity=ContextCapacity(256, "test", "known"))))
    seen = []
    async def handler(value):
        seen.append(value)
        return ModelResponse(result=[])
    await ContextBudgetMiddleware(500).awrap_model_call(request, handler)
    assert messages == original and len(seen) == 1
    assert seen[0].messages == original and seen[0].system_message == request.system_message
    request = request.override(tools=[{
        "type": "function", "function": {"name": "large", "description": "schema documentation" * 100,
                                        "parameters": {"type": "object", "properties": {}}},
    }])
    with pytest.raises(ContextBudgetExceeded):
        await ContextBudgetMiddleware(100).awrap_model_call(request, handler)
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
async def test_context_budget_counts_result_schema_before_model_call(structured: bool) -> None:
    schema: JSONObject | None = {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "result documentation " * 100},
        },
        "required": ["answer"],
    } if structured else None
    context = BudgetContext(
        request=RuntimeRunRequest(output_schema=schema),
        context_capacity=ContextCapacity(256, "test", "known"),
    )
    messages = [HumanMessage("short request")]
    original = list(messages)
    request = ModelRequest(
        model=InspectModel(), messages=messages, tools=[], runtime=Runtime(context=context),
    )
    calls = 0

    async def handler(value: ModelRequest[BudgetContext]) -> ModelResponse:
        nonlocal calls
        calls += 1
        assert value.messages == original
        assert value.tools == []
        return ModelResponse(result=[])

    middleware = ContextBudgetMiddleware[BudgetContext](256)
    if structured:
        with pytest.raises(ContextBudgetExceeded):
            await middleware.awrap_model_call(request, handler)
    else:
        await middleware.awrap_model_call(request, handler)
    assert calls == (0 if structured else 1)
    assert messages == original
    assert context.request.output_schema == schema


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_result", [False, True])
async def test_oversized_current_turn_fails_without_slicing(tool_result):
    messages = [HumanMessage("用户指令" * (1 if tool_result else 1000))]
    if tool_result:
        messages.extend([
            AIMessage(content="", tool_calls=[{"id": "read", "name": "file_read", "args": {}}]),
            ToolMessage(content="large output" * 1000, tool_call_id="read"),
        ])
    original = list(messages)
    request = ModelRequest(model=InspectModel(), messages=messages, system_message=None, tools=[],
                              runtime=Runtime(context=BudgetContext(
                                  context_capacity=ContextCapacity(50, "test", "known"))))
    async def forbidden(_request):
        raise AssertionError("oversized request reached the model")
    with pytest.raises(ContextBudgetExceeded):
        await ContextBudgetMiddleware(50).awrap_model_call(request, forbidden)
    assert messages == original


def test_cache_usage_preserves_nested_details_but_does_not_invent_missing_values():
    message = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 5,
            "total_tokens": 105,
            "input_token_details": {"cache_read": 80, "cache_creation": 10},
            "output_token_details": {"reasoning": 3},
        },
    )
    expected = {
        "input_tokens": 100,
        "output_tokens": 5,
        "total_tokens": 105,
        "cache_read_tokens": 80,
        "cache_creation_tokens": 10,
        "reasoning_tokens": 3,
    }
    assert message_usage(message) == expected
    assert message_usage(AIMessage(content="no counters")) == {}
    assert (
        message_usage(
            AIMessage(
                content="",
                response_metadata={
                    "token_usage": {
                        "prompt_tokens": 100,
                        "prompt_tokens_details": {"cached_tokens": 60},
                    }
                },
            )
        )["cache_read_tokens"]
        == 60
    )


@pytest.mark.asyncio
async def test_summary_failure_preserves_original_state_and_system_rules():
    messages = [
        SystemMessage("NEVER discard this rule"),
        *[message for _ in range(20) for message in
          (HumanMessage("old request " * 60), AIMessage("old answer " * 60))],
        HumanMessage("current request"),
    ]
    state = {"messages": messages}
    runtime = Runtime(context=BudgetContext(context_capacity=ContextCapacity(8000, "test", "known")))
    middleware = SafeSummarizationMiddleware(
        InspectModel(summary_error=True),
        input_budget=8000,
    )
    with pytest.raises(ContextBudgetExceeded, match="压缩失败"):
        await middleware.abefore_model(state, runtime)
    assert state["messages"] == messages
    assert messages[0].content == "NEVER discard this rule"
    middleware.model = InspectModel()
    update = await middleware.abefore_model(state, runtime)
    assert update is not None
    assert update["messages"][1] == messages[0]
    assert update["messages"][-1] == messages[-1]


@pytest.mark.asyncio
async def test_summary_request_over_budget_preserves_history_without_model_call():
    model = InspectModel()
    middleware = SafeSummarizationMiddleware(model, input_budget=1000)
    messages = [HumanMessage("old " * 1000), AIMessage("answer " * 1000), HumanMessage("current request")]
    state = {"messages": messages}
    with pytest.raises(ContextBudgetExceeded, match="压缩失败"):
        await middleware.abefore_model(
            state, Runtime(context=BudgetContext(
                context_capacity=ContextCapacity(1024, "test", "known"))))
    assert model.requests == []
    assert state["messages"] == messages
    assert messages[-1].content == "current request"


@pytest.mark.asyncio
async def test_compaction_keeps_latest_user_request_and_tool_pair():
    messages = [
        *[message for _ in range(20) for message in
          (HumanMessage("old request " * 60), AIMessage("old answer " * 60))],
        HumanMessage("EXACT CURRENT CONSTRAINT"),
        AIMessage("", tool_calls=[{"id": "read", "name": "file_read", "args": {}}]),
        ToolMessage("read content", tool_call_id="read"),
    ]
    middleware = SafeSummarizationMiddleware(
        InspectModel(),
        input_budget=8000,
    )
    update = await middleware.abefore_model(
        {"messages": messages}, Runtime(context=BudgetContext(
            context_capacity=ContextCapacity(8000, "test", "known"))),
    )
    assert update is not None
    assert update["messages"][-3:] == messages[-3:]


@pytest.mark.asyncio
async def test_cancellation_during_summary_waits_for_cleanup_and_emits_no_summary(tmp_path: Path):
    class SlowSummary(InspectModel):
        started: Any = Field(default_factory=asyncio.Event, exclude=True)
        stopped: Any = Field(default_factory=asyncio.Event, exclude=True)

        async def _agenerate(self, messages, **kwargs):
            try:
                assert "<primary_objective>" in str(messages[0].content)
                self.started.set()
                await asyncio.Event().wait()
            finally:
                self.stopped.set()
            raise AssertionError("summary generation should be cancelled")

    model = SlowSummary(profile={"max_input_tokens": 8000})
    async with LangGraphRuntime(
        model=model,
        workspace_root=tmp_path,
        checkpoint_path=tmp_path / "checkpoints.db",
        product_db_path=tmp_path / "product.db",
        context_max_tokens=8000,
    ) as runtime:
        conversation = await runtime.repository.create_conversation("personal", "long history")
        for _ in range(20):
            await runtime.repository.append_message(conversation["id"], "user", "old request " * 60)
            await runtime.repository.append_message(conversation["id"], "assistant", "old answer " * 60)
        handle = await runtime.start("current request", RuntimeRunRequest(conversation_id=conversation["id"]))
        await asyncio.wait_for(model.started.wait(), timeout=30)
        await handle.cancel()
        assert model.stopped.is_set() and handle.root_task.done()
        events = await asyncio.wait_for(handle.collect(), timeout=10)
        assert events[-1].type == "task.cancelled"
        assert not any(event.type.startswith("message.") for event in events)
        records = await runtime.repository.list_messages(conversation["id"])
        assert len(records) == 41


@pytest.mark.asyncio
async def test_runtime_compacts_to_checkpoint_without_leaking_summary_to_ui(tmp_path: Path):
    model = InspectModel(profile={"max_input_tokens": 8000})
    async with LangGraphRuntime(
        model=model,
        workspace_root=tmp_path,
        checkpoint_path=tmp_path / "checkpoints.db",
        product_db_path=tmp_path / "product.db",
        context_max_tokens=8000,
        system_prompt="STABLE PERSONAL RULES",
    ) as runtime:
        conversation = await runtime.repository.create_conversation("personal", "long history")
        for _ in range(20):
            await runtime.repository.append_message(conversation["id"], "user", "old request " * 60)
            await runtime.repository.append_message(conversation["id"], "assistant", "old answer " * 60)
        handle = await runtime.start("CURRENT REQUEST", RuntimeRunRequest(conversation_id=conversation["id"]))
        events = await asyncio.wait_for(handle.collect(), timeout=10)
        await handle.wait()
        assert events[-1].type == "task.succeeded"
        visible = [event.payload.get("content", "") for event in events if event.type.startswith("message.")]
        assert visible and all("SUMMARY_ONLY" not in text for text in visible)
        assert len(model.requests) == 2
        request = model.requests[-1]
        assert request[0].text.startswith("STABLE PERSONAL RULES\n\nUse write_todos for complex work")
        assert "Read files in bounded pages" in request[0].text
        assert request[-1].content == "CURRENT REQUEST"
        assert any("SUMMARY_ONLY" in str(message.content) for message in request)
        graph = await runtime._graph_for(handle.request)
        checkpoint = await graph.aget_state({"configurable": {"thread_id": handle.request.task_id}})
        assert any("SUMMARY_ONLY" in str(message.content) for message in checkpoint.values["messages"])
        records = await runtime.repository.list_messages(conversation["id"])
        assert len(records) == 42  # Original messages are not rewritten by compaction.
        usage = [event.payload["usage"] for event in events if event.type == "model.usage"]
        assert usage == [{"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cache_read_tokens": 60}]


@pytest.mark.asyncio
async def test_official_model_call_limit_bounds_tool_loop(tmp_path: Path):
    (tmp_path / "note.txt").write_text("hello")
    model = InspectModel(tool_name="file_read", tool_args={"path": "note.txt"}, repeat_tool=True)
    async with LangGraphRuntime(
        model=model,
        workspace_root=tmp_path,
        checkpoint_path=tmp_path / "checkpoints.db",
        product_db_path=tmp_path / "product.db",
        max_model_calls=2,
    ) as runtime:
        handle = await runtime.start("read forever")
        events = await asyncio.wait_for(handle.collect(), timeout=10)
        await handle.wait()
        assert events[-1].type == "task.failed"
        assert len(model.requests) == 2


@pytest.mark.asyncio
async def test_skill_workflow_instructions_load_only_on_demand(tmp_path: Path):
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="daily-notes",
            version="1",
            description="整理私人笔记",
            kind="workflow",
            instructions="PRIVATE_WORKFLOW_BODY: read first; write only after approval.",
            source_path="test",
        )
    )
    tool = SkillTool(registry)
    model = InspectModel(tool_name="skill", tool_args={"action": "read", "name": "daily-notes"})
    async with LangGraphRuntime(
        model=model,
        workspace_root=tmp_path,
        checkpoint_path=tmp_path / "checkpoints.db",
        product_db_path=tmp_path / "product.db",
        system_prompt=registry.catalog_prompt(),
        tools=[ProductToolSpec(tool, tool.to_langchain_tool())],
    ) as runtime:
        handle = await runtime.start("按 daily-notes 工作流整理")
        events = await asyncio.wait_for(handle.collect(), timeout=10)
        await handle.wait()
        assert events[-1].type == "task.succeeded"
        assert "PRIVATE_WORKFLOW_BODY" not in str(model.requests[0])
        assert "PRIVATE_WORKFLOW_BODY" in str(model.requests[1][-1].content)
        assert model.requests[1][0] == model.requests[0][0]
        assert any(event.type == "tool.completed" for event in events)


@pytest.mark.asyncio
async def test_credential_rotation_changes_graph_cache_key_without_closing_active_client():
    repository = SQLiteRepository(":memory:")
    await repository.open()
    runtime = ChatModelRuntime(ConfigManager(), repository)
    try:
        profile = await runtime.upsert_settings(
            "personal",
            ChatModelSettings(
                provider="openai_compatible",
                model="local-model",
                base_url="http://127.0.0.1:1234",
            ),
        )
        key, model = await runtime.get_langchain_model("personal", profile.id)
        same_key, same_model = await runtime.get_langchain_model("personal", profile.id)
        assert same_key == key and same_model is model
        await runtime.set_credential("personal", profile.provider, "test-only-key", profile.id)
        next_key, next_model = await runtime.get_langchain_model("personal", profile.id)
        assert next_key != key and next_model is not model
        assert not model.root_async_client.is_closed()
    finally:
        await runtime.close()
        await repository.close()
    assert model.root_async_client.is_closed()
    assert next_model.root_async_client.is_closed()


@pytest.mark.asyncio
async def test_native_usage_event_projects_provider_counters(core):
    class UsageModel(FakeModel):
        def reply(self, messages, **kwargs):
            return AIMessage(content="hello", usage_metadata={
                "input_tokens": 100, "output_tokens": 4, "total_tokens": 104,
                "input_token_details": {"cache_read": 60},
            })
    core._model = UsageModel()
    events = []
    async def collect(kind, payload):
        events.append(payload)
    core.event_bus.subscribe("model.usage", collect)
    result = await core.run("hello")
    assert result["status"] == "succeeded"
    assert len(events) == 1
    assert events[0]["usage"] == {
        "input_tokens": 100, "cache_read_tokens": 60, "output_tokens": 4, "total_tokens": 104,
    }
