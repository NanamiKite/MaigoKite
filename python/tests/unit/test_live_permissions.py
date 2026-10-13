"""Live permissions at real LangGraph tool boundaries, not a mock policy loop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from agent.tools.native.file_write import FileWriteTool
from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from pydantic import Field

from tests.unit.test_langgraph_production import RuleModel, SerialWritesModel, make_langgraph_core


class GatedModel(RuleModel):
    started: Any = Field(default_factory=asyncio.Event, exclude=True)
    release: Any = Field(default_factory=asyncio.Event, exclude=True)

    async def _astream(
        self, messages: list[BaseMessage], **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if not isinstance(messages[-1], ToolMessage):
            self.started.set()
            await self.release.wait()
        reply = self._reply(messages)
        yield ChatGenerationChunk(message=AIMessageChunk(content=reply.content, tool_calls=reply.tool_calls))


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [("workspace", "read_only", "waiting_approval"),
     ("read_only", "workspace", "succeeded"),
     ("read_only", "full_access", "succeeded")],
)
async def test_permission_change_while_model_runs(tmp_path: Path, monkeypatch, before, after, expected) -> None:
    model = GatedModel(tool_name="file_write", tool_args={"path": "live.txt", "content": "live"})
    core, _bus, _owner = await make_langgraph_core(tmp_path, model, monkeypatch)
    try:
        conversation = await core.create_conversation(title="live mode", permission_mode=before)
        work = asyncio.create_task(core.run("write", {"conversation_id": conversation["id"]}))
        await asyncio.wait_for(model.started.wait(), 3)
        await core.update_conversation_permission("personal", conversation["id"], after)
        model.release.set()
        result = await asyncio.wait_for(work, 3)
        assert result["status"] == expected
        assert (tmp_path / "live.txt").exists() == (expected == "succeeded")
        if expected == "waiting_approval":
            approval = result["approval"]
            completed = await core.decide_approval(
                "personal", approval["approval_id"], "local-user", "allow", approval["version"],
            )
            assert completed["status"] == "succeeded"
            assert (tmp_path / "live.txt").read_text() == "live"
    finally:
        model.release.set()
        await core.shutdown()


async def test_started_tool_keeps_snapshot_next_tool_requires_approval(tmp_path: Path, monkeypatch) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    observed: list[tuple[str, str, str]] = []
    executed: list[str] = []
    original = FileWriteTool.execute

    async def gated_write(tool, context, arguments):
        name = Path(arguments["path"]).name
        executed.append(name)
        if not started.is_set():
            started.set()
            before = context.metadata["permission_mode"]
            await release.wait()
            observed.append((name, before, context.metadata["permission_mode"]))
        return await original(tool, context, arguments)

    monkeypatch.setattr(FileWriteTool, "execute", gated_write)
    core, _bus, _owner = await make_langgraph_core(tmp_path, SerialWritesModel(), monkeypatch)
    try:
        conversation = await core.create_conversation(title="tool boundary", permission_mode="workspace")
        work = asyncio.create_task(core.run("write twice", {"conversation_id": conversation["id"]}))
        await asyncio.wait_for(started.wait(), 3)
        # ToolNode dispatches the batch concurrently; either write can acquire
        # the product tool lock first. Gate the actual first tool, so the other
        # remains unstarted when we revoke write access.
        assert len(executed) == 1
        first_name = executed[0]
        next_name = "second.txt" if first_name == "first.txt" else "first.txt"
        await core.update_conversation_permission("personal", conversation["id"], "read_only")
        release.set()
        blocked = await asyncio.wait_for(work, 3)
        assert observed == [(first_name, "workspace", "workspace")]
        assert (tmp_path / first_name).read_text() == Path(first_name).stem
        assert not (tmp_path / next_name).exists()
        assert blocked["status"] == "waiting_approval"
        approval = blocked["approval"]
        assert approval["tool_call_id"] == f"call-{Path(next_name).stem}-write"
        result = await core.decide_approval(
            "personal", approval["approval_id"], "local-user", "allow", approval["version"],
        )
        assert result["status"] == "succeeded"
        assert (tmp_path / "first.txt").read_text() == "first"
        assert (tmp_path / "second.txt").read_text() == "second"
        assert executed == [first_name, next_name]
    finally:
        release.set()
        await core.shutdown()


async def test_pending_approval_keeps_version_and_hash_when_mode_is_relaxed(tmp_path: Path, monkeypatch) -> None:
    core, _bus, _owner = await make_langgraph_core(
        tmp_path, RuleModel(tool_name="file_write", tool_args={"path": "approved.txt", "content": "safe"}),
        monkeypatch,
    )
    try:
        conversation = await core.create_conversation(title="pending decision", permission_mode="read_only")
        blocked = await core.run("write", {"conversation_id": conversation["id"]})
        approval = blocked["approval"]
        before = await core.repository.get_approval("personal", approval["approval_id"])
        await core.update_conversation_permission("personal", conversation["id"], "workspace")
        assert not (tmp_path / "approved.txt").exists()
        with pytest.raises(ValueError, match="version"):
            await core.decide_approval(
                "personal", approval["approval_id"], "local-user", "allow", approval["version"] + 1,
            )
        after = await core.repository.get_approval("personal", approval["approval_id"])
        assert after["arguments_hash"] == before["arguments_hash"]
        assert after["version"] == before["version"]
        assert after["status"] == "pending"
        result = await core.decide_approval(
            "personal", approval["approval_id"], "local-user", "allow", approval["version"],
        )
        assert result["status"] == "succeeded"
        assert (tmp_path / "approved.txt").read_text() == "safe"
    finally:
        await core.shutdown()
