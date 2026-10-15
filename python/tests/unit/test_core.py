from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from agent.models.runtime import ChatModelSettings
from agent.security.whitelist import Whitelist
from agent.tools.base import ExecutionContext
from agent.tools.permission_mgr import PermissionManager, RiskLevel
from agent.utils.sandbox import (
    SandboxExecutionResult,
    SandboxExecutor,
    SandboxUnavailableError,
    _bubblewrap_works,
)
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field

from tests.fixtures.chat_model import FakeModel


class ToolCallingProvider(FakeModel):
    with_reasoning: bool = True

    def reply(self, messages, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content="文件内容是 hello")
        return AIMessage(
            content="" if self.with_reasoning else "我先读取文件。",
            additional_kwargs={"reasoning_content": "我先读取文件。"} if self.with_reasoning else {},
            tool_calls=[
            {"id": "call_read", "name": "file_read", "args": {"path": "note.txt"}},
            ],
        )


class MultiToolApprovalProvider(FakeModel):
    received_tool_ids: list[str] = Field(default_factory=list)

    def reply(self, messages, **kwargs):
        tool_messages = [item for item in messages if isinstance(item, ToolMessage)]
        if tool_messages:
            # Invocation options use shallow model copies; observe requests via
            # the shared fixture list instead of replacing a copy's field.
            self.received_tool_ids.extend(item.tool_call_id for item in tool_messages)
            return AIMessage(content="两个工具都已完成")
        return AIMessage(content="", tool_calls=[
            {"id": "call_write_first", "name": "file_write",
             "args": {"path": "created.txt", "content": "created"}},
            {"id": "call_read_second", "name": "file_read", "args": {"path": "note.txt"}},
        ])

@pytest.mark.asyncio
async def test_conversation_and_message_round_trip(core):
    conversation=await core.create_conversation(title="test")
    result=await core.run("你好",{"conversation_id":conversation["id"]})
    assert result["response"]=="reply:你好"
    assert [item["role"] for item in await core.list_messages(conversation["id"])]==["user","assistant"]


@pytest.mark.asyncio
async def test_conversation_preserves_legacy_response_mode_without_changing_model_budget(core):
    observed = []

    class OptionProvider(FakeModel):
        options: dict = Field(default_factory=dict)

        def reply(self, messages, **kwargs):
            observed.append((self.options["model"], self.options["max_tokens"]))
            return AIMessage(content="fast reply")

    core._model = None
    core.models._model_factory = lambda **options: OptionProvider(options=options)
    profile = await core.upsert_chat_model("personal", ChatModelSettings(
        id="model_fast", name="Fast", provider="ollama", model="qwen",
        base_url="http://127.0.0.1:11434", max_tokens=12000, is_default=True,
    ))
    conversation = await core.create_conversation(
        title="profile", model_id=profile.id, response_mode="fast"
    )
    result = await core.run("hello", {"conversation_id": conversation["id"]})

    assert result["response"] == "fast reply"
    assert observed == [("qwen", 12000)]
    assert conversation["model_id"] == profile.id
    assert conversation["response_mode"] == "fast"

@pytest.mark.asyncio
async def test_read_only_write_requires_approval_and_approved_write_stays_in_workspace(core,tmp_path):
    conversation=await core.create_conversation(title="tool")
    command="/tool file_write "+json.dumps({"path":"result.txt","content":"ok"})
    blocked=await core.run(command,{"conversation_id":conversation["id"]})
    assert blocked["status"]=="waiting_approval"
    approval=blocked["approval"]
    allowed=await core.decide_approval(
        "personal",approval["approval_id"],"local-user","allow",approval["version"]
    )
    assert "Wrote" in allowed["response"] and (tmp_path/"result.txt").read_text()=="ok"
    completed = await core.repository.get_tool_call(approval["tool_call_id"])
    with pytest.raises(ValueError):
        await core.decide_approval(
            "personal",approval["approval_id"],"local-user","allow",approval["version"]
        )
    assert await core.repository.get_tool_call(approval["tool_call_id"]) == completed
    assert (await core.get_task("personal", blocked["task_id"]))["status"] == "succeeded"
    assert (await core.repository.get_approval("personal", approval["approval_id"]))["status"] == "consumed"
    assert (tmp_path / "result.txt").read_text() == "ok"


@pytest.mark.asyncio
async def test_approval_event_order_and_consumed_state(core,tmp_path):
    conversation=await core.create_conversation(title="event order")
    events=[]
    async def capture(kind,payload):events.append((kind,payload))
    core.event_bus.subscribe("*",capture)

    blocked=await core.run(
        "/tool file_write "+json.dumps({"path":"ordered.txt","content":"ok"}),
        {"conversation_id":conversation["id"]},
    )

    kinds=[kind for kind,_ in events]
    assert "tool.started" not in kinds
    assert kinds.index("tool.proposed") < kinds.index("task.waiting_approval")
    assert kinds.index("task.waiting_approval") < kinds.index("approval.requested")
    approval=blocked["approval"]
    await core.decide_approval(
        "personal",approval["approval_id"],"local-user","allow",approval["version"]
    )
    decided=await core.repository.get_approval("personal",approval["approval_id"])
    assert decided["status"]=="consumed"
    assert [kind for kind,_ in events].index("approval.decided") < [kind for kind,_ in events].index("tool.started")
    assert (tmp_path/"ordered.txt").read_text(encoding="utf-8")=="ok"


@pytest.mark.asyncio
async def test_multi_tool_batch_resumes_all_calls_after_approval(core,tmp_path):
    (tmp_path/"note.txt").write_text("note",encoding="utf-8")
    provider=MultiToolApprovalProvider()
    core._model=provider
    conversation=await core.create_conversation(title="multi tool",permission_mode="read_only")

    blocked=await core.run("写文件后读取 note.txt",{"conversation_id":conversation["id"]})
    assert blocked["status"]=="waiting_approval"
    approval=blocked["approval"]
    result=await core.decide_approval(
        "personal",approval["approval_id"],"local-user","allow",approval["version"]
    )

    assert result["status"]=="succeeded"
    assert result["response"]=="两个工具都已完成"
    assert provider.received_tool_ids==["call_write_first","call_read_second"]
    assert (await core.repository.get_tool_call("call_write_first"))["status"]=="succeeded"
    assert (await core.repository.get_tool_call("call_read_second"))["status"]=="succeeded"
    assert (tmp_path/"created.txt").read_text(encoding="utf-8")=="created"


@pytest.mark.asyncio
async def test_client_approved_metadata_cannot_bypass_read_only_mode(core,tmp_path):
    conversation=await core.create_conversation(title="tool")
    command="/tool file_write "+json.dumps({"path":"bypass.txt","content":"no"})
    blocked=await core.run(command,{"conversation_id":conversation["id"],"approved":True})
    assert blocked["status"]=="waiting_approval"
    assert not (tmp_path/"bypass.txt").exists()


@pytest.mark.asyncio
async def test_approval_is_invalidated_if_normalized_arguments_change(core,tmp_path):
    conversation=await core.create_conversation(title="tamper")
    blocked=await core.run(
        "/tool file_write "+json.dumps({"path":"original.txt","content":"safe"}),
        {"conversation_id":conversation["id"]},
    )
    approval=blocked["approval"]
    core.repository._db().execute(
        "UPDATE tool_calls SET arguments_json=? WHERE id=?",
        (json.dumps({"path":str(tmp_path/"changed.txt"),"content":"changed"}),approval["tool_call_id"]),
    )
    core.repository._db().commit()
    with pytest.raises(PermissionError,match="no longer match"):
        await core.decide_approval(
            "personal",approval["approval_id"],"local-user","allow",approval["version"]
        )
    assert not (tmp_path/"original.txt").exists()
    assert not (tmp_path/"changed.txt").exists()


@pytest.mark.asyncio
async def test_expired_approval_disappears_from_pending_snapshot(core):
    conversation=await core.create_conversation(title="expiry")
    await core.repository.create_task_state("task-expired","personal",conversation["id"],"expired","running")
    await core.repository.create_tool_call(
        "call-expired","personal",conversation["id"],"task-expired","file_write",{"path":"expired.txt"}
    )
    approval=await core.repository.create_approval(
        "personal",conversation["id"],"local-user","task-expired","call-expired",
        {"path":"expired.txt"},"L4",ttl_minutes=-1,
    )
    assert await core.list_approvals("personal",conversation["id"])==[]
    assert (await core.repository.get_approval("personal",approval["id"]))["status"]=="expired"


@pytest.mark.asyncio
@pytest.mark.parametrize("with_reasoning", [True, False])
async def test_model_can_call_tool_and_continue_with_result(core, tmp_path, with_reasoning):
    (tmp_path/"note.txt").write_text("hello",encoding="utf-8")
    core._model=ToolCallingProvider(with_reasoning=with_reasoning)
    conversation=await core.create_conversation(title="agent loop")
    events=[]
    async def capture(kind,payload):events.append((kind,payload))
    core.event_bus.subscribe("*",capture)
    result=await core.run("读取 note.txt",{"conversation_id":conversation["id"]})
    assert result["status"]=="succeeded"
    assert result["response"]=="文件内容是 hello"
    call=await core.repository.get_tool_call("call_read")
    assert call["status"]=="succeeded"
    reasoning=[payload for kind,payload in events if kind=="reasoning.completed"]
    visible=[payload["content"] for kind,payload in events if kind=="message.delta"]
    if with_reasoning:
        assert [item["summary"] for item in reasoning] == ["我先读取文件。"]
        assert visible == ["文件内容是 hello"]
    else:
        assert reasoning == []
        assert visible == ["我先读取文件。", "文件内容是 hello"]


@pytest.mark.asyncio
async def test_provider_reasoning_and_think_blocks_are_not_final_message_content(core):
    class ReasoningProvider(FakeModel):
        def reply(self, messages, **kwargs):
            return AIMessage(content="<think>规划回答。</think>最终答案。",
                             additional_kwargs={"reasoning_content": "检查上下文。"})

    core._model=ReasoningProvider()
    conversation=await core.create_conversation(title="reasoning")
    events=[]
    async def capture(kind,payload):events.append((kind,payload))
    core.event_bus.subscribe("*",capture)

    result=await core.run("开始",{"conversation_id":conversation["id"]})

    summaries=[payload["summary"] for kind,payload in events if kind=="reasoning.completed"]
    visible=[payload["content"] for kind,payload in events if kind=="message.delta"]
    assert result["response"]=="最终答案。"
    assert summaries==["检查上下文。\n\n规划回答。"]
    assert visible==["最终答案。"]


@pytest.mark.asyncio
async def test_conversation_permission_overrides_global_default(core,tmp_path):
    core._policy.mode="full_access"
    conversation=await core.create_conversation(title="read only",permission_mode="read_only")
    command="/tool file_write "+json.dumps({"path":"read-only.txt","content":"no"})
    blocked=await core.run(command,{"conversation_id":conversation["id"]})
    assert blocked["status"]=="waiting_approval"
    assert not (tmp_path/"read-only.txt").exists()


@pytest.mark.asyncio
async def test_bash_passes_normalized_workspace_to_executor_after_approval(core, tmp_path, monkeypatch):
    invocations = []

    async def execute(executor, command, timeout, **options):
        assert executor.workspace_root == executor.workdir == tmp_path.resolve()
        assert executor.mode == "workspace"
        assert executor.allow_network is False
        assert command == "pwd" and options["shell"] == "direct"
        invocations.append(command)
        return SandboxExecutionResult(exit_code=0, stdout=str(executor.workdir), sandboxed=True)

    monkeypatch.setattr(SandboxExecutor, "execute_command", execute)
    conversation=await core.create_conversation(title="bash",permission_mode="read_only")
    blocked=await core.run(
        "/tool bash "+json.dumps({"command":"pwd", "shell":"direct"}),
        {"conversation_id":conversation["id"]},
    )
    assert blocked["status"] == "waiting_approval"
    assert invocations == []
    approval=blocked["approval"]
    result=await asyncio.wait_for(core.decide_approval(
        "personal",approval["approval_id"],"local-user","allow",approval["version"]
    ),timeout=5)
    assert result["status"] == "succeeded", result
    assert result["response"].strip()==str(tmp_path)
    assert invocations == ["pwd"]


@pytest.mark.asyncio
async def test_bash_sandbox_blocks_host_reads_but_allows_workspace_writes(core,tmp_path):
    bubblewrap=shutil.which("bwrap")
    if not bubblewrap or not _bubblewrap_works(bubblewrap):
        pytest.skip("bubblewrap is unavailable; arbitrary commands fail closed on this platform")

    conversation=await core.create_conversation(title="sandbox",permission_mode="read_only")
    located = await core.run(
        '/tool bash {"command":"pwd", "shell":"direct"}', {"conversation_id":conversation["id"]},
    )
    approval = located["approval"]
    located = await core.decide_approval(
        "personal", approval["approval_id"], "local-user", "allow", approval["version"],
    )
    assert located["response"].strip() == str(tmp_path)
    await _assert_scoped_workspace_search(core, tmp_path)
    blocked=await core.run(
        "/tool bash "+json.dumps({"command":"python3 -c 'print(open(\"/etc/passwd\").read())'"}),
        {"conversation_id":conversation["id"]},
    )
    approval=blocked["approval"]
    denied_read=await core.decide_approval(
        "personal",approval["approval_id"],"local-user","allow",approval["version"]
    )
    assert "root:" not in denied_read["response"]
    assert "FileNotFoundError" in denied_read["response"]

    blocked=await core.run(
        "/tool bash "+json.dumps({"command":"python3 -c 'open(\"sandbox.txt\",\"w\").write(\"ok\")'"}),
        {"conversation_id":conversation["id"]},
    )
    approval=blocked["approval"]
    await core.decide_approval(
        "personal",approval["approval_id"],"local-user","allow",approval["version"]
    )
    assert (tmp_path/"sandbox.txt").read_text(encoding="utf-8")=="ok"


@pytest.mark.asyncio
async def test_read_only_mode_allows_workspace_reads(core,tmp_path):
    (tmp_path/"note.txt").write_text("read me",encoding="utf-8")
    conversation=await core.create_conversation(title="read",permission_mode="read_only")
    result=await core.run(
        "/tool file_read "+json.dumps({"path":"note.txt"}),
        {"conversation_id":conversation["id"]},
    )
    assert "read me" in result["response"]
    assert await core.list_approvals("personal",conversation["id"])==[]


async def _assert_scoped_workspace_search(core, tmp_path):
    (tmp_path/"workspace-only.txt").write_text("workspace needle",encoding="utf-8")
    conversation=await core.create_conversation(title="scoped search",permission_mode="read_only")

    glob_result=await core.run(
        "/tool glob "+json.dumps({"pattern":"workspace-only.txt"}),
        {"conversation_id":conversation["id"]},
    )
    grep_result=await core.run(
        "/tool grep "+json.dumps({"pattern":"workspace needle"}),
        {"conversation_id":conversation["id"]},
    )

    assert "workspace-only.txt" in glob_result["response"]
    assert "workspace-only.txt" in grep_result["response"]


@pytest.mark.asyncio
async def test_glob_and_grep_use_trusted_search_without_command_sandbox(core, tmp_path, monkeypatch):
    unavailable = AsyncMock(side_effect=SandboxUnavailableError("fixture: command isolation unavailable"))
    spawn = AsyncMock(wraps=asyncio.create_subprocess_exec)
    monkeypatch.setattr(SandboxExecutor, "execute_argv", unavailable)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("MAIGOKITE_SEARCH_SECRET", "fixture-do-not-inherit")
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(tmp_path / "untrusted-rg-config"))

    # Run the real policy, file reader, snapshot builder and installed ripgrep.
    # Only observe the public subprocess boundary; do not replace search results.
    await _assert_scoped_workspace_search(core, tmp_path)
    unavailable.assert_not_awaited()
    assert spawn.await_count == 3  # glob discovery; grep discovery and snapshot search
    calls = spawn.await_args_list
    for call in calls:
        executable = Path(call.args[0])
        directory = Path(call.kwargs["cwd"])
        environment = call.kwargs["env"]
        assert executable.is_absolute() and not executable.is_relative_to(tmp_path)
        assert executable.name in {"rg", "rg.exe"}
        assert not directory.is_relative_to(tmp_path)
        assert environment["PATH"] == str(executable.parent)
        assert environment["HOME"] == str(directory)
        assert not {
            "MAIGOKITE_SEARCH_SECRET", "RIPGREP_CONFIG_PATH", "OPENAI_API_KEY", "PYTHONPATH",
        } & environment.keys()
        assert {"--no-config", "--no-follow", "--no-ignore-parent", "--no-ignore-global"} <= set(call.args)
        assert not {"--pre", "--follow", "--search-zip"} & set(call.args)
        assert not directory.exists(), "temporary search directories must be reclaimed"
    assert calls[0].args[calls[0].args.index("--glob") + 1] == "workspace-only.txt"
    assert calls[0].args[-2:] == ("--", str(tmp_path.resolve()))
    assert calls[1].args[-2:] == ("--", str(tmp_path.resolve()))
    assert calls[2].args[-3:] == ("--", "workspace needle", "files")
    assert {"--json", "--no-ignore", "--hidden"} <= set(calls[2].args)
    assert calls[1].kwargs["cwd"] == calls[2].kwargs["cwd"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,arguments", [
    ("exec_command", {"command": "pwd", "shell": "direct"}),
])
async def test_unavailable_sandbox_reports_failure_without_host_fallback(core, monkeypatch, tool, arguments):
    unavailable = AsyncMock(side_effect=SandboxUnavailableError("fixture: isolation unavailable"))
    spawn = AsyncMock(side_effect=AssertionError("must not fall back to a host process"))
    monkeypatch.setattr(SandboxExecutor, "execute_argv", unavailable)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    events = []

    async def capture(kind, payload):
        events.append((kind, payload))

    core.event_bus.subscribe("*", capture)
    conversation = await core.create_conversation(permission_mode="workspace")
    result = await core.run(f"/tool {tool} " + json.dumps(arguments), {"conversation_id": conversation["id"]})
    unavailable.assert_awaited_once()
    spawn.assert_not_awaited()
    failures = [payload for kind, payload in events if kind == "tool.failed"]
    assert len(failures) == 1
    assert failures[0]["error_code"] == "SANDBOX_UNAVAILABLE"
    assert failures[0]["user_hint"]
    assert result["response"] == failures[0]["summary"]
    assert not any(kind == "tool.completed" for kind, _ in events)
    assert (await core.repository.get_tool_call(failures[0]["tool_call_id"]))["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,arguments", [
    ("glob", {"pattern": "*.txt"}),
    ("grep", {"pattern": "needle"}),
])
async def test_unavailable_ripgrep_reports_failure_without_command_fallback(
    core, tmp_path, monkeypatch, tool, arguments,
):
    # A workspace-supplied executable must not satisfy the trusted dependency.
    executable = tmp_path / "rg"
    executable.write_text("untrusted workspace executable", encoding="utf-8")
    executable.chmod(0o755)
    (tmp_path / "rg.exe").write_bytes(b"untrusted workspace executable")
    monkeypatch.setenv("PATH", str(tmp_path))
    command = AsyncMock(side_effect=AssertionError("search must not request arbitrary command execution"))
    spawn = AsyncMock(side_effect=AssertionError("untrusted search must not start a host process"))
    monkeypatch.setattr(SandboxExecutor, "execute_argv", command)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    events = []

    async def capture(kind, payload):
        events.append((kind, payload))

    core.event_bus.subscribe("*", capture)
    conversation = await core.create_conversation(permission_mode="read_only")
    result = await core.run(f"/tool {tool} " + json.dumps(arguments), {"conversation_id": conversation["id"]})
    command.assert_not_awaited()
    spawn.assert_not_awaited()
    failures = [payload for kind, payload in events if kind == "tool.failed"]
    assert len(failures) == 1
    assert failures[0]["error_code"] == "RIPGREP_UNAVAILABLE"
    assert failures[0]["user_hint"]
    assert result["response"] == failures[0]["summary"]
    assert not any(kind == "tool.completed" for kind, _ in events)
    assert (await core.repository.get_tool_call(failures[0]["tool_call_id"]))["status"] == "failed"


@pytest.mark.asyncio
async def test_remaining_phase_one_native_tools_execute_through_policy(core,tmp_path,monkeypatch):
    await core.update_tool_settings("personal", {"sleep": True})
    (tmp_path/"edit.txt").write_text("before",encoding="utf-8")
    search_tool=core.tool_registry.get("web_search")
    assert search_tool is not None

    async def fake_search(query,max_results):
        return [{"title":"Result","url":"https://example.com","snippet":query}]

    monkeypatch.setattr(search_tool,"_duckduckgo_search",fake_search)
    conversation=await core.create_conversation(title="native tools",permission_mode="full_access")

    edited=await core.run(
        "/tool file_edit "+json.dumps({
            "path":"edit.txt","old_string":"before","new_string":"after",
        }),
        {"conversation_id":conversation["id"]},
    )
    slept=await core.run(
        "/tool sleep "+json.dumps({"seconds":0.001}),
        {"conversation_id":conversation["id"]},
    )
    searched=await core.run(
        "/tool web_search "+json.dumps({"query":"阶段一","engine":"duckduckgo"}),
        {"conversation_id":conversation["id"]},
    )
    fetched=await core.run(
        "/tool web_fetch "+json.dumps({"url":"file:///etc/passwd"}),
        {"conversation_id":conversation["id"]},
    )

    assert "1 replacement(s)" in edited["response"]
    assert (tmp_path/"edit.txt").read_text(encoding="utf-8")=="after"
    assert "slept" in slept["response"]
    assert "example.com" in searched["response"]
    assert "URL must start" in fetched["response"]


@pytest.mark.asyncio
async def test_optional_tool_stays_disabled_until_explicitly_enabled(core, monkeypatch):
    tool = core.tool_registry.get("sleep")
    assert tool is not None
    execute = AsyncMock(wraps=tool.execute)
    monkeypatch.setattr(tool, "execute", execute)
    conversation = await core.create_conversation(permission_mode="danger_full_access")
    events = []

    async def capture(kind, payload):
        events.append((kind, payload))

    core.event_bus.subscribe("*", capture)
    command = '/tool sleep {"seconds": 0.001}'
    context = {"conversation_id": conversation["id"]}
    await core.run(command, context)
    execute.assert_not_awaited()
    assert not any(kind == "tool.started" for kind, _ in events)
    denied = [payload for kind, payload in events if kind == "tool.failed"]
    assert len(denied) == 1 and denied[0]["denied"] is True

    await core.update_tool_settings("personal", {"sleep": True})
    events.clear()
    result = await core.run(command, context)
    execute.assert_awaited_once()
    assert result["status"] == "succeeded"
    assert [kind for kind, _ in events if kind.startswith("tool.")] == [
        "tool.proposed", "tool.started", "tool.completed",
    ]


@pytest.mark.asyncio
async def test_web_search_failure_emits_actionable_tool_event(core,monkeypatch):
    search_tool=core.tool_registry.get("web_search")
    assert search_tool is not None

    async def failed_search(query,max_results):
        raise RuntimeError("provider offline at https://example.invalid/?api_key=DO_NOT_EXPOSE")

    monkeypatch.setattr(search_tool,"_duckduckgo_search",failed_search)
    conversation=await core.create_conversation(title="search failure",permission_mode="full_access")
    events=[]
    async def capture(kind,payload):events.append((kind,payload))
    core.event_bus.subscribe("*",capture)

    result=await core.run(
        "/tool web_search "+json.dumps({"query":"NS Conjecture mathematics"}),
        {"conversation_id":conversation["id"]},
    )

    assert result["status"]=="succeeded"
    failed=[payload for kind,payload in events if kind=="tool.failed"]
    assert len(failed)==1
    assert failed[0]["error_code"]=="SEARCH_PROVIDER_UNAVAILABLE"
    assert "设置 → 工具" in failed[0]["user_hint"]
    assert failed[0]["action"]=={"label":"配置联网工具","to":"/settings?tab=tools"}
    assert not [payload for kind,payload in events if kind=="tool.completed"]
    assert "DO_NOT_EXPOSE" not in json.dumps(events, ensure_ascii=False)
    assert "DO_NOT_EXPOSE" not in result["response"]


@pytest.mark.asyncio
async def test_web_search_uses_workspace_tool_credential(core,monkeypatch):
    search_tool=core.tool_registry.get("web_search")
    assert search_tool is not None
    observed=[]

    async def fake_tavily(query,api_key,max_results):
        observed.append((query,api_key,max_results))
        return [{"title":"Result","url":"https://example.com","snippet":"ok"}]

    monkeypatch.setattr(search_tool,"_tavily_search",fake_tavily)
    await core.set_tool_credential("personal","tavily","secret-tavily-key")
    conversation=await core.create_conversation(title="configured search",permission_mode="full_access")
    result=await core.run(
        "/tool web_search "+json.dumps({"query":"configured provider","max_results":3}),
        {"conversation_id":conversation["id"]},
    )

    assert result["status"]=="succeeded"
    assert observed==[("configured provider","secret-tavily-key",3)]
    assert "example.com" in result["response"]


@pytest.mark.asyncio
async def test_web_search_rejects_incomplete_google_credential(core):
    await core.set_tool_credential("personal","google","google-api-key-without-cx")
    conversation=await core.create_conversation(title="invalid search credential",permission_mode="full_access")
    events=[]
    async def capture(kind,payload):events.append((kind,payload))
    core.event_bus.subscribe("*",capture)

    result=await core.run(
        "/tool web_search "+json.dumps({"query":"configured provider","engine":"google"}),
        {"conversation_id":conversation["id"]},
    )

    assert result["status"]=="succeeded"
    failed=[payload for kind,payload in events if kind=="tool.failed"]
    assert failed[0]["error_code"]=="SEARCH_PROVIDER_INVALID_CONFIGURATION"
    assert failed[0]["action"]["to"]=="/settings?tab=tools"


@pytest.mark.asyncio
async def test_unknown_tool_is_denied_before_started_event(core):
    conversation=await core.create_conversation(title="unknown tool")
    events=[]
    async def capture(kind,payload):events.append((kind,payload))
    core.event_bus.subscribe("*",capture)

    result=await core.run("/tool missing_tool {}",{"conversation_id":conversation["id"]})

    assert result["response"] == "tool is not enabled for this run"
    assert "tool.started" not in [kind for kind,_ in events]
    failed=[payload for kind,payload in events if kind=="tool.failed"]
    assert failed and failed[0]["denied"] is True
    assert failed[0]["error_code"] == "TOOL_DENIED"
    assert (await core.repository.get_tool_call(failed[0]["tool_call_id"]))["status"] == "denied"
    audit=await core.repository.list_audit_events("personal")
    assert any(item["action"]=="tool.denied" and item["resource"]=="missing_tool" for item in audit)


@pytest.mark.asyncio
async def test_approved_file_error_is_projected_without_crashing_the_run(core, tmp_path):
    conversation=await core.create_conversation(title="approved failure")
    events=[]
    async def capture(kind,payload):events.append((kind,payload))
    core.event_bus.subscribe("*",capture)
    blocked=await core.run(
        "/tool file_write "+json.dumps({"path":".","content":"cannot write a directory"}),
        {"conversation_id":conversation["id"]},
    )
    approval=blocked["approval"]

    result = await core.decide_approval(
        "personal", approval["approval_id"], "local-user", "allow", approval["version"]
    )

    task=await core.get_task("personal", blocked["task_id"])
    assert result["status"] == task["status"] == "succeeded"
    assert (await core.repository.get_tool_call(approval["tool_call_id"]))["status"] == "failed"
    failures = [payload for kind, payload in events if kind == "tool.failed"]
    assert len(failures) == 1
    assert failures[0]["error_code"] == "TOOL_EXECUTION_FAILED"
    assert failures[0]["tool_call_id"] == approval["tool_call_id"]
    assert result["response"] == failures[0]["summary"]
    kinds = [kind for kind, _ in events]
    assert kinds[-1] == "task.succeeded"
    assert "tool.completed" not in kinds
    assert "task.failed" not in kinds
    assert "file.changed" not in kinds
    assert tmp_path.is_dir()


@pytest.mark.asyncio
async def test_unhandled_approved_tool_exception_fails_task_with_safe_diagnostics(core, tmp_path, monkeypatch):
    tool = core.tool_registry.get("file_write")
    assert tool is not None
    execute = AsyncMock(side_effect=RuntimeError("https://example.invalid/?api_key=DO_NOT_EXPOSE"))
    monkeypatch.setattr(tool, "execute", execute)
    events = []

    async def capture(kind, payload):
        events.append((kind, payload))

    core.event_bus.subscribe("*", capture)
    conversation = await core.create_conversation(permission_mode="read_only")
    blocked = await core.run(
        '/tool file_write {"path":"unwritten.txt", "content":"never"}',
        {"conversation_id": conversation["id"]},
    )
    approval = blocked["approval"]
    execute.assert_not_awaited()
    result = await core.decide_approval(
        "personal", approval["approval_id"], "local-user", "allow", approval["version"],
    )
    execute.assert_awaited_once()
    assert result["status"] == "failed"
    assert (await core.get_task("personal", blocked["task_id"]))["status"] == "failed"
    assert (await core.repository.get_tool_call(approval["tool_call_id"]))["status"] == "failed"
    kinds = [kind for kind, _ in events]
    assert kinds.count("tool.failed") == kinds.count("task.failed") == 1
    assert kinds[-1] == "task.failed"
    assert "tool.completed" not in kinds and "task.succeeded" not in kinds
    assert "DO_NOT_EXPOSE" not in json.dumps(events, ensure_ascii=False)
    assert "DO_NOT_EXPOSE" not in json.dumps(result, ensure_ascii=False)
    assert not (tmp_path / "unwritten.txt").exists()


@pytest.mark.asyncio
async def test_file_read_reports_binary_input_cleanly(core,tmp_path):
    (tmp_path/"binary.dat").write_bytes(b"abc\x00def")
    conversation=await core.create_conversation(title="binary")
    result=await core.run(
        "/tool file_read "+json.dumps({"path":"binary.dat"}),
        {"conversation_id":conversation["id"]},
    )
    assert result["response"] == "不支持读取二进制文件，请选择文本文件或使用支持该格式的工具。"
    assert "abc" not in result["response"]


@pytest.mark.asyncio
async def test_todos_are_persistent_and_can_be_completed(core):
    tool=core.tool_registry.get("todo_write")
    assert tool is not None
    context=ExecutionContext("trace","task","step",metadata={"workspace_id":"personal"})
    added=await tool.execute(context,{"operation":"add","text":"完成第一阶段"})
    todo_id=added.data["items"][0]["id"]
    completed=await tool.execute(context,{"operation":"update","id":todo_id,"done":True})
    assert completed.data["items"][0]["done"] is True
    listed=await tool.execute(context,{"operation":"list"})
    assert listed.data["items"]==completed.data["items"]

def test_policy_matrix_and_hard_deny(tmp_path):
    read_only=PermissionManager("read_only",tmp_path)
    assert read_only.evaluate("file_read",{"path":"a.txt"}).outcome=="allow"
    assert read_only.evaluate("file_write",{"path":"a.txt"}).outcome=="approval_required"
    assert read_only.evaluate("bash",{"command":"rm -rf /"},approved=True).outcome=="deny"
    assert read_only.evaluate("file_read",{"path":"../outside"}).risk==RiskLevel.L5
    workspace=PermissionManager("workspace",tmp_path)
    assert workspace.evaluate("file_write",{"path":"a.txt"}).outcome=="allow"
    assert workspace.evaluate("bash",{"command":"git status", "shell":"direct"}).outcome=="allow"
    assert workspace.evaluate("bash",{"command":"git diff --output=/tmp/leak"}).outcome=="deny"
    full_access=PermissionManager("full_access",tmp_path)
    assert full_access.evaluate("web_search",{"query":"test"}).outcome=="allow"
    assert read_only.evaluate("fixture__echo",{"text":"ok"}).outcome=="deny"
    assert read_only.evaluate("fixture__echo",{"text":"ok"},risk_override=1).outcome=="allow"

    configured=PermissionManager("full_access",tmp_path,blocked_patterns=["dd if="])
    assert configured.evaluate(
        "bash",{"command":"dd if=input.bin of=output.bin"},approved=True,
    ).outcome=="deny"

def test_allowlist_narrows_tools_commands_and_workspace_paths(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    allowlist=Whitelist(
        tools={"file_read","bash"},
        command_prefixes={"git status"},
        allowed_paths={str(docs)},
    )
    policy=PermissionManager("workspace",tmp_path,allowlist)

    assert policy.evaluate("file_read",{"path":"docs/note.txt"}).outcome=="allow"
    assert policy.evaluate("file_read",{"path":"private.txt"}).outcome=="deny"
    assert policy.evaluate("file_write",{"path":"docs/note.txt"}).outcome=="deny"
    assert policy.evaluate("bash",{"command":"git status","workdir":"docs"}).outcome=="allow"
    assert policy.evaluate("bash",{"command":"pwd"}).outcome=="deny"


@pytest.mark.asyncio
async def test_core_loads_allowlist_configuration(tmp_path):
    from agent.config_manager import ConfigManager
    from agent.core import AgentCore
    from agent.event_bus import EventBus
    from agent.observability import Observability
    from agent.repository import SQLiteRepository

    config=ConfigManager()
    config.set("workspace.root",str(tmp_path))
    config.set("permissions.allowlist",{
        "tools":["file_read"],"command_prefixes":[],"paths":["docs"],
    })
    configured=AgentCore(config,EventBus(),Observability(),repository=SQLiteRepository(":memory:"))
    try:
        assert configured._policy.evaluate("file_read",{"path":"docs/a.txt"}).outcome=="allow"
        assert configured._policy.evaluate("grep",{"pattern":"a"}).outcome=="deny"
    finally:
        await configured.shutdown()

def test_legacy_permission_modes_migrate_conservatively(tmp_path):
    assert PermissionManager("plan",tmp_path).mode=="read_only"
    assert PermissionManager("manual",tmp_path).mode=="read_only"
    assert PermissionManager("accept_edits",tmp_path).mode=="workspace"
    for mode in ("auto", "full_access"):
        policy = PermissionManager(mode, tmp_path)
        assert policy.mode == "workspace_auto"
        assert policy.evaluate("file_read", {"path": "../outside.txt"}).outcome == "deny"

def test_windows_auto_shell_selection_requires_approval(tmp_path, monkeypatch):
    import agent.utils.command_shell as command_shell

    with monkeypatch.context() as windows:
        windows.setattr(command_shell.os, "name", "nt")
        windows.setattr(command_shell, "powershell_executable", lambda _shell: "C:/PowerShell/7/pwsh.exe")
        selected = command_shell.resolve_command_shell("auto")
    assert selected == "pwsh"
    decision = PermissionManager("workspace", tmp_path).evaluate(
        "bash", {"command": "git status", "shell": selected},
    )
    assert decision.outcome == "approval_required"

    with monkeypatch.context() as windows:
        windows.setattr(command_shell.os, "name", "nt")
        windows.setattr(
            command_shell,
            "powershell_executable",
            lambda _shell: (_ for _ in ()).throw(FileNotFoundError()),
        )
        fallback = command_shell.resolve_command_shell("auto")
    assert fallback == "powershell"
    assert PermissionManager("workspace", tmp_path).evaluate(
        "bash", {"command": "git status", "shell": fallback},
    ).outcome == "approval_required"


def test_filesystem_root_cannot_be_used_as_workspace():
    with pytest.raises(ValueError,match="filesystem root"):
        PermissionManager("workspace","/")

@pytest.mark.asyncio
async def test_workspace_conversations_are_isolated(core):
    await core.repository.ensure_workspace("team-a","A","team")
    item=await core.create_conversation("team-a","shared")
    assert [x["id"] for x in await core.list_conversations("team-a")]==[item["id"]]
    assert await core.list_conversations("personal")==[]

@pytest.mark.asyncio
async def test_workspace_membership_is_default_deny(core):
    assert await core.repository.check_workspace_access("personal", "local-user") == "owner"
    assert await core.repository.check_workspace_access("personal", "unknown-user") is None

@pytest.mark.asyncio
async def test_assistant_name_is_persisted_per_user(core):
    assert await core.get_user_preferences("personal", "local-user") == {"assistant_name": "助手"}
    saved=await core.update_user_preferences("personal", "local-user", "  小七  Agent  ")
    assert saved == {"assistant_name": "小七 Agent"}
    assert await core.get_user_preferences("personal", "local-user") == saved
    assert await core.get_user_preferences("personal", "another-user") == {"assistant_name": "助手"}

@pytest.mark.asyncio
async def test_assistant_name_validation(core):
    with pytest.raises(ValueError, match="required"):
        await core.update_user_preferences("personal", "local-user", "   ")
    with pytest.raises(ValueError, match="32"):
        await core.update_user_preferences("personal", "local-user", "x"*33)


@pytest.mark.asyncio
async def test_user_memories_are_persistent_searchable_and_user_scoped(core):
    created = await core.upsert_memory(
        "personal", "local-user", category="preference",
        content="  用户偏好使用中文回答  ", confidence=0.9,
    )
    assert created["content"] == "用户偏好使用中文回答"
    assert created["source_type"] == "user"
    assert [item["id"] for item in await core.list_memories(
        "personal", "local-user", "中文",
    )] == [created["id"]]
    assert await core.list_memories("personal", "another-user") == []

    updated = await core.upsert_memory(
        "personal", "local-user", memory_id=created["id"], category="constraint",
        content="只使用中文回答", confidence=1.0,
    )
    assert updated["category"] == "constraint"
    await core.delete_memory("personal", "local-user", created["id"])
    assert await core.list_memories("personal", "local-user") == []
    assert [event["action"] for event in await core.repository.list_audit_events("personal")] == [
        "memory.deleted", "memory.updated", "memory.created",
    ]
    with pytest.raises(KeyError):
        await core.delete_memory("personal", "another-user", created["id"])


@pytest.mark.asyncio
async def test_memory_validation_and_workspace_data_summary(core):
    with pytest.raises(ValueError, match="category"):
        await core.upsert_memory(
            "personal", "local-user", category="secret", content="not allowed",
        )
    with pytest.raises(ValueError, match="required"):
        await core.upsert_memory(
            "personal", "local-user", category="fact", content="  ",
        )
    with pytest.raises(ValueError, match="credentials"):
        await core.upsert_memory(
            "personal", "local-user", category="fact", content="api_key=sk-test-0123456789abcdef",
        )

    conversation = await core.create_conversation(title="summary")
    await core.repository.append_message(conversation["id"], "user", "hello")
    await core.upsert_memory(
        "personal", "local-user", category="fact", content="summary fact",
    )
    summary = await core.get_workspace_data_summary("personal")
    assert summary["storage_backend"] == "sqlite"
    assert summary["conversation_count"] == 1
    assert summary["message_count"] == 1
    assert summary["memory_count"] == 1
    assert summary["audit_event_count"] == 1
