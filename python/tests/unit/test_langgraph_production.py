from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from agent.config_manager import ConfigManager
from agent.core import AgentCore
from agent.event_bus import EventBus
from agent.models.native_model import close_chat_models
from agent.observability import Observability
from agent.repository import ConversationBusyError, SQLiteRepository
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field


class RuleModel(BaseChatModel):
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    final_text: str = "done"

    def bind_tools(self, tools: Any, **kwargs: Any) -> RuleModel:
        return self

    def _reply(self, messages: list[BaseMessage]) -> AIMessage:
        if self.tool_name and not isinstance(messages[-1], ToolMessage):
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
        return AIMessage(content=self.final_text)

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _agenerate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return self._generate(messages, **kwargs)

    async def _astream(self, messages: list[BaseMessage], **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        message = self._reply(messages)
        if message.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_calls=message.tool_calls))
            return
        for character in str(message.content):
            yield ChatGenerationChunk(message=AIMessageChunk(content=character))

    @property
    def _llm_type(self) -> str:
        return "maigokite-production-rule-model"


class SlowModel(BaseChatModel):
    started: Any = Field(default_factory=asyncio.Event, exclude=True)
    cancelled: Any = Field(default_factory=asyncio.Event, exclude=True)

    def bind_tools(self, tools: Any, **kwargs: Any) -> SlowModel:
        return self

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        raise AssertionError("async streaming is required")

    async def _astream(self, messages: list[BaseMessage], **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        try:
            self.started.set()
            yield ChatGenerationChunk(message=AIMessageChunk(content="first"))
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()

    @property
    def _llm_type(self) -> str:
        return "maigokite-production-slow-model"


class SerialWritesModel(BaseChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> SerialWritesModel:
        return self

    @staticmethod
    def _reply(messages: list[BaseMessage]) -> AIMessage:
        tool_results = [message for message in messages if isinstance(message, ToolMessage)]
        calls = [
            {
                "name": "file_write",
                "args": {"path": "first.txt", "content": "first"},
                "id": "call-first-write",
                "type": "tool_call",
            },
            {
                "name": "file_write",
                "args": {"path": "second.txt", "content": "second"},
                "id": "call-second-write",
                "type": "tool_call",
            },
        ]
        if not tool_results:
            return AIMessage(content="", tool_calls=calls)
        if len(tool_results) == 1:
            return AIMessage(content="", tool_calls=[calls[1]])
        return AIMessage(content="both complete")

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _agenerate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return self._generate(messages, **kwargs)

    async def _astream(self, messages: list[BaseMessage], **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        reply = self._reply(messages)
        yield ChatGenerationChunk(message=AIMessageChunk(content=reply.content, tool_calls=reply.tool_calls))

    @property
    def _llm_type(self) -> str:
        return "maigokite-production-serial-writes-model"


class ModelOwner:
    """Observe Core-owned client cleanup; no legacy provider boundary."""

    def __init__(self, model: BaseChatModel) -> None:
        self.langchain_model = model
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


async def make_langgraph_core(
    tmp_path: Path, model: BaseChatModel, monkeypatch: pytest.MonkeyPatch
) -> tuple[AgentCore, EventBus, ModelOwner]:
    config = ConfigManager()
    config.set("agent.langgraph_checkpoint_path", str(tmp_path / "checkpoints.sqlite"))
    config.set("storage.sqlite_path", str(tmp_path / "product.sqlite"))
    config.set("workspace.root", str(tmp_path))
    config.set("permissions.default_mode", "read_only")
    config.set(
        "models.chat",
        {
            "provider": "ollama",
            "default": "runtime-test",
            "base_url": "http://127.0.0.1:11434",
        },
    )
    event_bus = EventBus()
    core = AgentCore(
        config,
        event_bus,
        Observability(),
        repository=SQLiteRepository(tmp_path / "product.sqlite"),
    )
    owner = ModelOwner(model)
    # The new runtime deliberately no longer consults the legacy factory.
    # Fake only the native provider boundary; keep the real model cache and
    # Core shutdown path, observing when owned models are released.
    monkeypatch.setattr("agent.models.runtime.build_chat_model", lambda **_kwargs: model)

    async def close_models(models):
        owned = list(models)
        if not owned:
            return
        assert owned == [model]
        if isinstance(model, SlowModel) and model.started.is_set():
            assert model.cancelled.is_set()
        await close_chat_models(owned)
        await owner.aclose()

    monkeypatch.setattr("agent.models.runtime.close_chat_models", close_models)
    await core.initialize()
    return core, event_bus, owner


@pytest.mark.asyncio
async def test_production_langgraph_chat_read_and_write_approval(tmp_path: Path, monkeypatch) -> None:
    model = RuleModel(
        tool_name="file_write",
        tool_args={"path": "created.txt", "content": "created once"},
        final_text="write complete",
    )
    core, event_bus, owner = await make_langgraph_core(tmp_path, model, monkeypatch)
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))

    event_bus.subscribe("*", capture)
    try:
        conversation = await core.create_conversation(title="production runtime", permission_mode="read_only")
        blocked = await core.run(
            "write created.txt",
            {"conversation_id": conversation["id"]},
        )
        assert blocked["status"] == "waiting_approval"
        assert not (tmp_path / "created.txt").exists()
        approval = blocked["approval"]
        result = await core.decide_approval(
            "personal",
            approval["approval_id"],
            "local-user",
            "allow",
            approval["version"],
        )
        assert result["status"] == "succeeded"
        assert result["response"] == "write complete"
        assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "created once"
        task = await core.get_task("personal", blocked["task_id"])
        assert task["status"] == "succeeded"
        kinds = [kind for kind, _payload in events]
        assert kinds.index("tool.proposed") < kinds.index("approval.requested")
        assert kinds.index("approval.decided") < kinds.index("tool.started")
        assert kinds[-1] == "task.succeeded"
        assert all(not any(key.startswith("langgraph") for key in payload) for _kind, payload in events)
    finally:
        await core.shutdown()
    assert owner.closed is True


@pytest.mark.asyncio
async def test_missing_checkpoint_cannot_fall_back_to_legacy_approval(tmp_path: Path, monkeypatch) -> None:
    core, _bus, _owner = await make_langgraph_core(
        tmp_path,
        RuleModel(tool_name="file_write", tool_args={"path": "blocked.txt", "content": "secret"}),
        monkeypatch,
    )
    try:
        conversation = await core.create_conversation(title="lost checkpoint")
        blocked = await core.run("write blocked.txt", {"conversation_id": conversation["id"]})
        assert blocked["status"] == "waiting_approval"
        approval = blocked["approval"]
        assert core._langgraph_runtime is not None
        await core._langgraph_runtime.delete_threads([blocked["task_id"]])
        with pytest.raises(ValueError, match="matching runtime interrupt"):
            await core.decide_approval("personal", approval["approval_id"], "local-user", "allow", approval["version"])
        stored = await core.repository.get_approval("personal", approval["approval_id"])
        assert stored["status"] == "pending" and stored["version"] == approval["version"]
        assert not (tmp_path / "blocked.txt").exists()
        denied = await core.decide_approval(
            "personal",
            approval["approval_id"],
            "local-user",
            "deny",
            approval["version"],
        )
        # Refusal commits independently of recovery; a missing checkpoint is
        # an explicit continuation failure, not a cancellation or permission.
        assert denied["status"] == "failed"
        stored = await core.repository.get_approval("personal", approval["approval_id"])
        assert stored["status"] == "rejected" and stored["version"] == approval["version"] + 1
        call = await core.repository.get_tool_call(approval["tool_call_id"])
        assert call["status"] == "denied"
        assert not (tmp_path / "blocked.txt").exists()
    finally:
        await core.shutdown()


@pytest.mark.asyncio
async def test_cancel_before_root_starts_still_commits_one_terminal_event(tmp_path: Path, monkeypatch) -> None:
    core, _bus, _owner = await make_langgraph_core(tmp_path, SlowModel(), monkeypatch)
    try:
        runtime = core._langgraph_runtime
        assert runtime is not None
        handle = await runtime.start("cancel immediately")
        await asyncio.wait_for(handle.cancel(), timeout=2)
        await asyncio.wait_for(handle.cancel(), timeout=2)
        events = await asyncio.wait_for(handle.collect(), timeout=2)
        assert handle.root_task.done()
        assert [event.type for event in events].count("task.cancelled") == 1
        assert events[-1].type == "task.cancelled"
        task = await core.repository.get_task("personal", handle.request.task_id)
        assert task["status"] == "cancelled"
    finally:
        await core.shutdown()


@pytest.mark.asyncio
async def test_native_runtime_does_not_construct_legacy_executors(tmp_path: Path, monkeypatch) -> None:
    core, _bus, _owner = await make_langgraph_core(tmp_path, RuleModel(), monkeypatch)
    try:
        assert not hasattr(core, "task_manager")
        assert not hasattr(core, "execution_engine")
        assert not hasattr(core, "_runtime_backend")
        conversation = await core.create_conversation(title="no legacy executors")
        result = await core.run("hello", {"conversation_id": conversation["id"]})
        assert result["status"] == "succeeded"
    finally:
        await core.shutdown()


@pytest.mark.asyncio
async def test_changed_approval_path_does_not_consume_version(tmp_path: Path, monkeypatch) -> None:
    original = tmp_path / "original.txt"
    changed = tmp_path / "changed.txt"
    link = tmp_path / "target.txt"
    link.symlink_to(original)
    core, _bus, _owner = await make_langgraph_core(
        tmp_path,
        RuleModel(tool_name="file_write", tool_args={"path": "target.txt", "content": "approved"}),
        monkeypatch,
    )
    try:
        conversation = await core.create_conversation(title="bound approval")
        blocked = await core.run("write target", {"conversation_id": conversation["id"]})
        approval = blocked["approval"]
        link.unlink()
        link.symlink_to(changed)
        with pytest.raises(PermissionError, match="parameters or policy changed"):
            await core.decide_approval("personal", approval["approval_id"], "local-user", "allow", approval["version"])
        stored = await core.repository.get_approval("personal", approval["approval_id"])
        assert (stored["status"], stored["version"]) == ("pending", approval["version"])
        assert not original.exists() and not changed.exists()
        denied = await core.decide_approval(
            "personal", approval["approval_id"], "local-user", "deny", approval["version"]
        )
        assert denied["status"] == "succeeded"
        assert denied["response"] == "done"
        assert not original.exists() and not changed.exists()
        stored = await core.repository.get_approval("personal", approval["approval_id"])
        assert stored["status"] == "rejected" and stored["version"] == approval["version"] + 1
        call = await core.repository.get_tool_call(approval["tool_call_id"])
        assert call["status"] == "denied"
    finally:
        await core.shutdown()


@pytest.mark.asyncio
async def test_global_interrupt_drains_native_runs_and_keeps_client_open(tmp_path: Path, monkeypatch) -> None:
    core, bus, owner = await make_langgraph_core(tmp_path, SlowModel(), monkeypatch)
    streaming: set[str] = set()
    both_started = asyncio.Event()
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))
        if kind == "message.delta":
            streaming.add(payload["task_id"])
            if len(streaming) == 2:
                both_started.set()

    bus.subscribe("*", capture)
    runs = []
    try:
        for index in range(2):
            conversation = await core.create_conversation(title=f"stop {index}")
            runs.append(asyncio.create_task(core.run("stream", {"conversation_id": conversation["id"]})))
        await asyncio.wait_for(both_started.wait(), timeout=3)
        await asyncio.wait_for(core.interrupt(), timeout=3)
        results = await asyncio.wait_for(asyncio.gather(*runs), timeout=3)
        assert all(result["status"] == "cancelled" for result in results)
        assert not owner.closed
        assert not core._langgraph_entry_tasks
        for task_id in streaming:
            kinds = [kind for kind, payload in events if payload.get("task_id") == task_id]
            assert kinds.count("task.cancelled") == 1 and kinds[-1] == "task.cancelled"
        count = len(events)
        await asyncio.sleep(0)
        assert len(events) == count
    finally:
        for run in runs:
            if not run.done():
                run.cancel()
        await asyncio.gather(*runs, return_exceptions=True)
        await core.shutdown()
    assert owner.closed


@pytest.mark.asyncio
async def test_production_langgraph_chat_executes_file_read_without_approval(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    core, event_bus, _owner = await make_langgraph_core(
        tmp_path,
        RuleModel(
            tool_name="file_read",
            tool_args={"path": "note.txt"},
            final_text="read complete",
        ),
        monkeypatch,
    )
    events: list[str] = []

    async def capture(kind: str, _payload: dict[str, Any]) -> None:
        events.append(kind)

    event_bus.subscribe("*", capture)
    try:
        conversation = await core.create_conversation(title="read")
        result = await core.run("read note.txt", {"conversation_id": conversation["id"]})
        assert result["status"] == "succeeded"
        assert result["response"] == "read complete"
        assert "tool.started" in events and "tool.completed" in events
        assert "approval.requested" not in events
        tool_call = await core.repository.get_tool_call("call-file_read")
        assert tool_call["status"] == "succeeded"
    finally:
        await core.shutdown()


@pytest.mark.asyncio
async def test_parallel_protected_calls_are_serialized_into_two_approvals(
    tmp_path: Path,
    monkeypatch,
) -> None:
    core, _event_bus, _owner = await make_langgraph_core(tmp_path, SerialWritesModel(), monkeypatch)
    try:
        conversation = await core.create_conversation(title="serial approvals", permission_mode="read_only")
        first = await core.run("write both files", {"conversation_id": conversation["id"]})
        assert first["status"] == "waiting_approval", first
        assert first["approval"]["tool_call_id"] == "call-first-write"
        second = await core.decide_approval(
            "personal",
            first["approval"]["approval_id"],
            "local-user",
            "allow",
            first["approval"]["version"],
        )
        assert second["status"] == "waiting_approval"
        assert second["approval"]["tool_call_id"] == "call-second-write"
        # Product review is serial, but native HITL resumes the complete batch
        # only after every required decision has been collected.
        assert not (tmp_path / "first.txt").exists()
        assert not (tmp_path / "second.txt").exists()
        final = await core.decide_approval(
            "personal",
            second["approval"]["approval_id"],
            "local-user",
            "allow",
            second["approval"]["version"],
        )
        assert final["status"] == "succeeded"
        assert final["response"] == "both complete"
        assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "first"
        assert (tmp_path / "second.txt").read_text(encoding="utf-8") == "second"
    finally:
        await core.shutdown()


@pytest.mark.asyncio
async def test_production_cancel_waits_for_model_and_stops_events(tmp_path: Path, monkeypatch) -> None:
    model = SlowModel()
    core, event_bus, owner = await make_langgraph_core(tmp_path, model, monkeypatch)
    events: list[tuple[str, dict[str, Any]]] = []
    first_delta = asyncio.Event()

    async def capture(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))
        if kind == "message.delta":
            first_delta.set()

    event_bus.subscribe("*", capture)
    conversation = await core.create_conversation(title="cancel")
    run_task = asyncio.create_task(core.run("stream", {"conversation_id": conversation["id"]}))
    try:
        await asyncio.wait_for(first_delta.wait(), timeout=2)
        task_id = next(payload["task_id"] for kind, payload in events if kind == "task.started")
        cancelled = await core.cancel_task("personal", task_id)
        result = await asyncio.wait_for(run_task, timeout=2)
        assert cancelled["status"] == "cancelled"
        assert result["status"] == "cancelled"
        await asyncio.wait_for(model.cancelled.wait(), timeout=1)
        cancelled_index = next(index for index, event in enumerate(events) if event[0] == "task.cancelled")
        assert events[cancelled_index + 1 :] == []
    finally:
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
        await core.shutdown()
    assert owner.closed is True


@pytest.mark.asyncio
async def test_sync_file_read_cancel_stops_runtime_but_not_worker_thread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "slow.txt").write_text("value", encoding="utf-8")
    model = RuleModel(
        tool_name="file_read",
        tool_args={"path": "slow.txt"},
        final_text="must not be emitted",
    )
    core, event_bus, _owner = await make_langgraph_core(tmp_path, model, monkeypatch)
    runtime = core._langgraph_runtime
    assert runtime is not None
    read_spec = next(spec for spec in runtime.tool_specs if spec.tool.name == "file_read")
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_read(path: str, encoding: str = "utf-8", max_bytes: int = 16_000, offset: int = 0) -> dict[str, Any]:
        started.set()
        release.wait(timeout=5)
        finished.set()
        return {
            "path": path,
            "content": "value",
            "size_bytes": 5,
            "truncated": False,
        }

    read_spec.runtime_tool.func = slow_read
    # file_read is now async; explicitly select the sync worker path to retain
    # this separate regression for the documented unkillable-thread limit.
    read_spec.runtime_tool.coroutine = None
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))

    event_bus.subscribe("*", capture)
    conversation = await core.create_conversation(title="sync cancel")
    run_task = asyncio.create_task(core.run("read slow.txt", {"conversation_id": conversation["id"]}))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
        task_id = next(payload["task_id"] for kind, payload in events if kind == "task.started")
        await core.cancel_task("personal", task_id)
        result = await asyncio.wait_for(run_task, timeout=2)
        assert result["status"] == "cancelled"
        assert finished.is_set() is False
        cancelled_index = next(index for index, event in enumerate(events) if event[0] == "task.cancelled")
        release.set()
        await asyncio.wait_for(asyncio.to_thread(finished.wait), timeout=2)
        await asyncio.sleep(0)
        assert events[cancelled_index + 1 :] == []
    finally:
        release.set()
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
        await core.shutdown()


@pytest.mark.asyncio
async def test_core_shutdown_cancels_runs_before_closing_model_owner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = SlowModel()
    core, event_bus, owner = await make_langgraph_core(tmp_path, model, monkeypatch)
    first_delta = asyncio.Event()
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))
        if kind == "message.delta":
            first_delta.set()

    event_bus.subscribe("*", capture)
    conversation = await core.create_conversation(title="shutdown")
    run_task = asyncio.create_task(core.run("stream", {"conversation_id": conversation["id"]}))
    try:
        await asyncio.wait_for(first_delta.wait(), timeout=2)
        await asyncio.wait_for(core.shutdown(), timeout=5)
        # Shutdown cancels the owned API entry task too; there is no surviving
        # caller to receive a normal result. The durable product state remains
        # cancelled and must be committed before clients/repository close.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_task, timeout=2)
        assert model.cancelled.is_set() and owner.closed
        assert not core._langgraph_entry_tasks
        assert [kind for kind, _ in events].count("task.cancelled") == 1
        assert events[-1][0] == "task.cancelled"
        repository = SQLiteRepository(tmp_path / "product.sqlite")
        await repository.open()
        try:
            task = await repository.get_task("personal", events[-1][1]["task_id"])
            assert task["status"] == "cancelled"
        finally:
            await repository.close()
        with pytest.raises(ConversationBusyError, match="starting, closing, or applying extensions"):
            await core.answer_question("personal", "unused", "local-user", "answer", 1)
        assert not core._langgraph_entry_tasks
        assert events[-1][0] == "task.cancelled"
    finally:
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
        if core._initialized:
            await core.shutdown()


@pytest.mark.asyncio
async def test_runtime_keeps_recorded_product_write_semantics(tmp_path: Path, monkeypatch) -> None:
    # Legacy comparison was recorded before deletion; keep its product milestones,
    # not an executable copy of the retired runtime.
    core, bus, _ = await make_langgraph_core(
        tmp_path,
        RuleModel(
            tool_name="file_write", tool_args={"path": "result.txt", "content": "same"}, final_text="write complete"
        ),
        monkeypatch,
    )
    events = []

    async def capture(kind, payload):
        events.append(kind)

    bus.subscribe("*", capture)
    try:
        conversation = await core.create_conversation(title="product milestones")
        blocked = await core.run("write result.txt", {"conversation_id": conversation["id"]})
        approval = blocked["approval"]
        completed = await core.decide_approval(
            "personal",
            approval["approval_id"],
            "local-user",
            "allow",
            approval["version"],
        )
        assert completed["status"] == "succeeded"
        assert completed["response"] == "write complete"
        assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "same"
        milestones = [
            "task.started",
            "tool.proposed",
            "task.waiting_approval",
            "approval.requested",
            "approval.decided",
            "tool.started",
            "tool.completed",
            "message.completed",
            "task.succeeded",
        ]
        assert [event for event in events if event in milestones] == milestones
    finally:
        await core.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["owner", "version", "pending", "checkpoint", "archived", "archive_race", "newer", "terminal", "unsafe"]
)
async def test_public_recovery_rejects_without_replaying(tmp_path: Path, monkeypatch, case: str) -> None:
    model = RuleModel(tool_name="file_write", tool_args={"path": "blocked.txt", "content": "no replay"})
    core, bus, _ = await make_langgraph_core(tmp_path, model, monkeypatch)
    try:
        conversation = await core.create_conversation(title="recovery admission")
        blocked = await core.run("write blocked.txt", {"conversation_id": conversation["id"]})
        task_id = blocked["task_id"]
        runtime = core._langgraph_runtime
        assert runtime is not None
        # Model the product status seen after a process interruption; leave the
        # actual native checkpoint/approval unchanged to verify rejection.
        if case != "pending":
            await core.repository.update_task_status(task_id, "interrupted")
        if case == "checkpoint":
            await runtime.delete_threads([task_id])
        elif case == "archived":
            await core.repository.update_conversation_settings("personal", conversation["id"], archived=True)
        elif case == "archive_race":
            original_admit = core.repository.resume_interrupted_task

            async def archive_before_commit(workspace, recovered_task, version):
                await core.repository.update_conversation_settings(workspace, conversation["id"], archived=True)
                await original_admit(workspace, recovered_task, version)

            monkeypatch.setattr(core.repository, "resume_interrupted_task", archive_before_commit)
        elif case == "newer":
            await core.repository.start_chat_turn("newer-task", "personal", conversation["id"], "another turn", "new")
        elif case == "terminal":
            await core.cancel_task("personal", task_id)
        elif case == "unsafe":
            from agent.runtime.langgraph_runtime import RuntimeRunRequest

            graph = await runtime._graph_for(RuntimeRunRequest(conversation_id=conversation["id"], task_id=task_id))
            await graph.aupdate_state(
                {"configurable": {"thread_id": task_id}},
                {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "unsafe-call",
                                    "name": "exec_command",
                                    "args": {"command": "echo blocked"},
                                    "type": "tool_call",
                                }
                            ],
                        ),
                    ]
                },
            )
        before = await core.get_task("personal", task_id)
        captured = []

        async def capture(kind, payload):
            captured.append(kind)

        bus.subscribe("*", capture)
        expected = PermissionError if case == "owner" else ValueError
        with pytest.raises(expected):
            await core.resume_task(
                "personal",
                task_id,
                "intruder" if case == "owner" else "local-user",
                before["version"] - 1 if case == "version" else before["version"],
            )
        assert await core.get_task("personal", task_id) == before
        assert not captured
        assert not (tmp_path / "blocked.txt").exists()
    finally:
        await core.shutdown()


@pytest.mark.asyncio
async def test_public_recovery_does_not_grant_pending_approval(tmp_path: Path, monkeypatch) -> None:
    core, bus, _ = await make_langgraph_core(
        tmp_path,
        RuleModel(tool_name="file_write", tool_args={"path": "pending.txt", "content": "pending"}),
        monkeypatch,
    )
    try:
        conversation = await core.create_conversation(title="pending recovery")
        blocked = await core.run("write pending.txt", {"conversation_id": conversation["id"]})
        task_id = blocked["task_id"]
        await core.repository.update_task_status(task_id, "interrupted")
        task = await core.get_task("personal", task_id)
        result = await core.resume_task("personal", task_id, "local-user", task["version"], "recovery-test")
        assert result["status"] == "waiting_approval"
        assert not (tmp_path / "pending.txt").exists()
        stored = await core.repository.get_approval("personal", blocked["approval"]["approval_id"])
        assert stored["status"] == "pending" and stored["version"] == 1
    finally:
        await core.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown", [False, True])
async def test_public_recovery_cancellation_drains_root(tmp_path: Path, monkeypatch, shutdown: bool) -> None:
    started, cleaned = asyncio.Event(), asyncio.Event()
    original_stream = RuleModel._astream

    async def stream(self, messages, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            try:
                started.set()
                yield ChatGenerationChunk(message=AIMessageChunk(content="recovering"))
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        else:
            async for chunk in original_stream(self, messages, **kwargs):
                yield chunk

    monkeypatch.setattr(RuleModel, "_astream", stream)
    core, bus, owner = await make_langgraph_core(
        tmp_path,
        RuleModel(tool_name="file_write", tool_args={"path": "once.txt", "content": "once"}),
        monkeypatch,
    )
    recovery = None
    captured = []

    async def capture(kind, payload):
        if kind == "task.cancelled":
            assert cleaned.is_set(), "cancelled was published before model cleanup"
        captured.append(kind)

    bus.subscribe("*", capture)
    try:
        conversation = await core.create_conversation(title="cancel recovered run")
        blocked = await core.run("write once.txt", {"conversation_id": conversation["id"]})
        task_id = blocked["task_id"]
        approval = blocked["approval"]
        # Simulate a process dying immediately after its durable approval commit.
        await core.repository.decide_approval(
            "personal", approval["approval_id"], "local-user", "allow", approval["version"]
        )
        await core.repository.update_task_status(task_id, "interrupted")
        task = await core.get_task("personal", task_id)
        recovery = asyncio.create_task(core.resume_task("personal", task_id, "local-user", task["version"]))
        await asyncio.wait_for(started.wait(), 5)
        if shutdown:
            await core.shutdown()
            with pytest.raises(asyncio.CancelledError):
                await recovery
            assert owner.closed
        else:
            result = await core.cancel_task("personal", task_id)
            assert result["status"] == "cancelled"
            assert (await recovery)["status"] == "cancelled"
            assert not owner.closed  # Cancelling one run does not close shared clients.
        assert cleaned.is_set() and recovery.done()
        assert captured.count("task.cancelled") == 1 and captured[-1] == "task.cancelled"
        boundary = list(captured)
        await asyncio.sleep(0)
        assert captured == boundary
        assert (tmp_path / "once.txt").read_text() == "once"
    finally:
        if recovery is not None and not recovery.done():
            recovery.cancel()
            await asyncio.gather(recovery, return_exceptions=True)
        if core._initialized:
            await core.shutdown()
