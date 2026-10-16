"""Run one isolated trial through the product Core and its public approval API."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from agent.config_manager import ConfigManager
from agent.core import AgentCore
from agent.data_contracts import JSONObject
from agent.event_bus import EventBus
from agent.models.native_model import close_chat_models
from agent.observability import Observability
from agent.repository import SQLiteRepository
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import TypeAdapter

from .cases import Case, Observation

JSON_OBJECT: TypeAdapter[JSONObject] = TypeAdapter(JSONObject)


class RequestRecorder(AsyncCallbackHandler):
    """Capture model input messages, not serialized clients or authentication."""

    def __init__(self, observation: Observation, emit: Callable[[JSONObject], None]) -> None:
        self.observation = observation
        self.emit = emit

    async def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: UUID, **kwargs: Any,
    ) -> None:
        for batch in messages:
            payload = [JSON_OBJECT.validate_python(message.model_dump(mode="json")) for message in batch]
            self.observation.model_requests.append(payload)
            self.emit({"type": "eval.model_input", "messages": list(payload)})


def discard_event(event: JSONObject) -> None:
    pass


def snapshot(root: Path) -> dict[str, str | None]:
    """Keep small synthetic artifacts; unreadable/large/link artifacts fail grading."""
    result: dict[str, str | None] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            result[path.relative_to(root).as_posix()] = None
        elif path.is_file():
            try:
                result[path.relative_to(root).as_posix()] = (
                    path.read_text(encoding="utf-8") if path.stat().st_size <= 100_000 else None
                )
            except (OSError, UnicodeError):
                result[path.relative_to(root).as_posix()] = None
    return result


async def run_trial(
    case: Case, model: BaseChatModel, *, timeout: int = 90, max_calls: int = 8,
    context_window: int = 16384, temp_parent: Path | None = None,
    emit: Callable[[JSONObject], None] = discard_event,
) -> Observation:
    started = time.monotonic()
    with TemporaryDirectory(prefix="maigokite-trial-", dir=temp_parent) as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        for name, content in case.files.items():
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        # Deliberately do not load user YAML, environment overrides or keyring.
        config = ConfigManager()
        config.set("workspace.root", str(workspace))
        config.set("storage.sqlite_path", str(root / "product.sqlite"))
        config.set("agent.langgraph_checkpoint_path", str(root / "checkpoint.sqlite"))
        config.set("browser.enabled", False)
        config.set("tools.default_tools", ["file_read", "file_write"])
        config.set("tools.max_iterations", max_calls)
        config.set("context.max_tokens", context_window)
        config.set("permissions.default_mode", case.permission_mode)
        config.set("models.chat", {"provider": "ollama", "default": "eval-injected"})
        bus = EventBus()
        core = AgentCore(config, bus, Observability(), model, SQLiteRepository(root / "product.sqlite"))
        observation = Observation(status="not_started", response="", files={})
        model.callbacks = [RequestRecorder(observation, emit)]

        async def capture(kind: str, payload: JSONObject) -> None:
            observation.events.append({"type": kind, "payload": payload})
            emit({"type": kind, "payload": payload})

        bus.subscribe("*", capture)
        try:
            async with asyncio.timeout(timeout):
                await core.initialize()
                # Explicit allowlist: no shell, browser, MCP, image API or delegation.
                settings = await core.get_tool_settings("personal")
                await core.update_tool_settings("personal", {
                    item["name"]: item["name"] in {"file_read", "file_write"}
                    for item in settings["tools"]
                })
                conversation = await core.create_conversation(permission_mode=case.permission_mode)
                for prompt in case.prompts:
                    result = await core.run(
                        prompt, {"conversation_id": conversation["id"], "principal_id": "eval-user"},
                    )
                    for _ in range(max_calls):
                        if result["status"] != "waiting_approval":
                            break
                        approval = result["approval"]
                        observation.approval_snapshots.append(snapshot(workspace))
                        path = (workspace / str(approval["arguments"].get("path", ""))).resolve()
                        outputs = {workspace / name for name in case.expected_files.keys() | case.json_files.keys()}
                        allowed = (case.approval == "allow_outputs" and approval["tool_name"] == "file_write"
                                   and path in outputs)
                        decision = "allow" if allowed else "deny"
                        observation.decisions.append(decision)
                        result = await core.decide_approval(
                            "personal", approval["approval_id"], "eval-user", decision, approval["version"],
                            note="Only the requested output path is approved." if allowed else
                                 "Do not retry or bypass this denial; continue with the requested alternative.",
                        )
                    task = await core.get_task("personal", result["task_id"])
                    observation.task_statuses.append(task["status"])
                    observation.model_calls += task["model_calls_used"]
                    observation.status = result["status"]
                    observation.response = result.get("response", "")
                    if result["status"] != "succeeded":
                        break
        finally:
            # Core cancels/drains active runs before the caller-owned model closes.
            try:
                await core.shutdown()
            finally:
                try:
                    await close_chat_models([model])
                finally:
                    emit({"type": "eval.artifacts", "files": dict(snapshot(workspace))})
        observation.files = snapshot(workspace)
        observation.elapsed_seconds = time.monotonic() - started
        return observation
