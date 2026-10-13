from __future__ import annotations

from pathlib import Path

import pytest
from agent.repository import SQLiteRepository
from agent.skill_registry import SkillRegistry
from agent.tools.base import ExecutionContext
from agent.tools.native.browser import BrowserTool
from agent.tools.native.skill import SkillTool
from agent.tools.permission_mgr import PermissionManager, RiskLevel
from pydantic import ValidationError


def test_skill_registry_discovers_declarative_skills(tmp_path: Path) -> None:
    source = tmp_path / "skills" / "research" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        """---
name: research
version: 1.2.0
description: Build an evidence-backed research brief.
---
Read primary sources, record citations, and separate facts from inference.
""",
        encoding="utf-8",
    )
    registry = SkillRegistry()

    assert registry.load_directories(["skills"], tmp_path) == []
    skill = registry.get_skill("research")
    assert skill is not None
    assert skill.version == "1.2.0"
    assert "primary sources" in skill.instructions
    assert "research" in registry.catalog_prompt()


@pytest.mark.asyncio
async def test_skill_tool_lists_and_reads_instructions(tmp_path: Path) -> None:
    source = tmp_path / "skills" / "notes" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "---\nname: notes\ndescription: Produce concise notes.\n---\nUse headings.",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    assert registry.load_directories([str(source.parent.parent)], tmp_path) == []
    tool = SkillTool(registry)
    context = ExecutionContext("trace", "task", "step")

    listed = await tool.execute(context, {"action": "list"})
    loaded = await tool.execute(context, {"action": "read", "name": "notes"})

    assert listed.data["skills"][0]["name"] == "notes"
    assert loaded.raw == "Use headings."


class _FakeBrowserRuntime:
    workspace_root = Path("/workspace")

    async def open(self, url: str, *, owner, profile: str = "", visible: bool = False):
        return {"session_id": "browser_1", "url": url, "text": "Example"}


@pytest.mark.asyncio
async def test_browser_tool_adapts_runtime_result() -> None:
    tool = BrowserTool(_FakeBrowserRuntime())  # type: ignore[arg-type]
    result = await tool.execute(
        ExecutionContext("trace", "task", "step"),
        {"action": "open", "url": "https://example.com"},
    )
    assert result.success is True
    assert result.data["session_id"] == "browser_1"
    assert result.raw == "Example"


def test_browser_permissions_are_action_sensitive(tmp_path: Path) -> None:
    policy = PermissionManager("workspace", tmp_path)
    assert policy.evaluate("browser", {"action": "snapshot", "session_id": "x"}).risk == RiskLevel.L1
    assert policy.evaluate("browser", {"action": "open", "url": "https://example.com"}).risk == RiskLevel.L3
    assert policy.evaluate("browser", {"action": "click", "session_id": "x", "selector": "button"}).risk == RiskLevel.L5
    assert (
        policy.evaluate("browser", {"action": "click", "session_id": "x", "selector": "button"}).outcome
        == "approval_required"
    )


@pytest.mark.asyncio
async def test_task_event_journal_and_restart_reconciliation(tmp_path: Path) -> None:
    database = tmp_path / "agent.db"
    repository = SQLiteRepository(database)
    await repository.open()
    try:
        conversation = await repository.create_conversation("personal", "test")
        await repository.create_task_state(
            "task_1", "personal", conversation["id"], "continue work", "running", "input"
        )
        first = await repository.append_task_event(
            "task.started",
            {"task_id": "task_1", "conversation_id": conversation["id"], "input_text": "input", "trace_id": "trace_1"},
        )
        second = await repository.append_task_event(
            "tool.started",
            {
                "task_id": "task_1",
                "conversation_id": conversation["id"],
                "tool_call_id": "call_1",
                "tool_name": "file_read",
                "arguments": {"path": "sample.txt"},
            },
        )
        assert first["sequence"] == 1
        assert second["sequence"] == 2
    finally:
        await repository.close()

    reopened = SQLiteRepository(database)
    await reopened.open()
    try:
        interrupted = await reopened.recover_interrupted_tasks()
        events = await reopened.list_task_events("personal", conversation["id"], 1)
        task = await reopened.get_task("personal", "task_1")
    finally:
        await reopened.close()

    assert interrupted[0]["id"] == "task_1"
    assert task["status"] == "interrupted"
    assert [item["sequence"] for item in events] == [2]
    assert events[0]["payload"]["tool_call_id"] == "call_1"
    assert events[0]["payload"]["arguments"] == {"path": "sample.txt"}


@pytest.mark.parametrize(
    ("event_type", "payload", "invalid_fields"),
    [
        pytest.param("task.started", {}, {"input_text", "trace_id"}, id="task-missing-fields"),
        pytest.param("task.started", {"input_text": "input", "trace_id": 7}, {"trace_id"}, id="task-invalid-trace"),
        pytest.param("tool.started", {}, {"tool_call_id", "tool_name", "arguments"}, id="tool-missing-fields"),
        pytest.param(
            "tool.started",
            {"tool_call_id": "call_1", "tool_name": "file_read", "arguments": "sample.txt"},
            {"arguments"},
            id="tool-invalid-arguments",
        ),
    ],
)
@pytest.mark.asyncio
async def test_event_journal_rejects_invalid_payloads_without_persisting(
    tmp_path: Path, event_type: str, payload: dict[str, object], invalid_fields: set[str]
) -> None:
    repository = SQLiteRepository(tmp_path / "agent.db")
    await repository.open()
    try:
        conversation = await repository.create_conversation("personal", "test")
        await repository.create_task_state("task_1", "personal", conversation["id"], "task", "running", "input")
        with pytest.raises(ValidationError) as rejected:
            await repository.append_task_event(
                event_type, {"task_id": "task_1", "conversation_id": conversation["id"], **payload}
            )
        assert {error["loc"][0] for error in rejected.value.errors()} == invalid_fields
        assert await repository.list_task_events("personal", conversation["id"], 0) == []
        accepted = await repository.append_task_event(
            "task.started",
            {"task_id": "task_1", "conversation_id": conversation["id"], "input_text": "input", "trace_id": "trace_1"},
        )
        assert accepted["sequence"] == 1
    finally:
        await repository.close()


@pytest.mark.asyncio
async def test_cancel_task_closes_pending_approval(tmp_path: Path) -> None:
    repository = SQLiteRepository(":memory:")
    await repository.open()
    conversation = await repository.create_conversation("personal", "test")
    await repository.create_task_state("task_1", "personal", conversation["id"], "task", "running")
    await repository.create_tool_call(
        "call_1",
        "personal",
        conversation["id"],
        "task_1",
        "browser",
        {"action": "click", "selector": "button"},
    )
    approval = await repository.create_approval(
        "personal",
        conversation["id"],
        "local-user",
        "task_1",
        "call_1",
        {"action": "click", "selector": "button"},
        "L5",
    )

    task = await repository.cancel_task("personal", "task_1")
    updated = await repository.get_approval("personal", approval["id"])
    await repository.close()

    assert task["status"] == "cancelled"
    assert updated["status"] == "rejected"


@pytest.mark.asyncio
async def test_restart_interrupts_approval_that_depends_on_browser_session() -> None:
    repository = SQLiteRepository(":memory:")
    await repository.open()
    conversation = await repository.create_conversation("personal", "test")
    await repository.create_task_state("task_1", "personal", conversation["id"], "task", "running")
    await repository.create_tool_call(
        "call_1",
        "personal",
        conversation["id"],
        "task_1",
        "browser",
        {"action": "click", "session_id": "browser_lost", "selector": "button"},
    )
    approval = await repository.create_approval(
        "personal",
        conversation["id"],
        "local-user",
        "task_1",
        "call_1",
        {"action": "click", "session_id": "browser_lost", "selector": "button"},
        "L5",
    )

    interrupted = await repository.interrupt_ephemeral_approvals({"browser"})
    task = await repository.get_task("personal", "task_1")
    updated = await repository.get_approval("personal", approval["id"])
    await repository.close()

    assert interrupted[0]["id"] == "task_1"
    assert task["status"] == "interrupted"
    assert updated["status"] == "rejected"
