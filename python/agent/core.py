"""Product composition root around the single LangGraph execution runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pathlib import Path
from typing import Any, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel

from agent.browser_runtime import BrowserRuntime
from agent.config_manager import ConfigManager
from agent.data_contracts import (
    JSON_OBJECT,
    RUN_METADATA,
    ApprovalNotice,
    ApprovalRecord,
    ConversationRecord,
    JSONObject,
    MessageRecord,
    RunMetadata,
    RunResult,
    TaskEventRecord,
    TaskRecord,
    approval_notice,
)
from agent.event_bus import EventBus
from agent.extensions import ExtensionCatalog, ExtensionConfig, MCPServerStatus, close_mcp_clients, startup_config
from agent.mcp import MCPClient
from agent.media import ImageReference
from agent.memory.notes import FolderMemories
from agent.models.image_generation import IMAGE_SETTING_KEY, ImageSettings
from agent.models.runtime import ChatModelRuntime, ChatModelSettings
from agent.observability import Observability
from agent.process_registry import ProcessRegistry
from agent.repository import (
    ConversationBusyError,
    MessageReplay,
    SQLiteRepository,
    validate_subagent_model_ids,
)
from agent.runtime import LangGraphRuntime, ProductToolSpec, RunHandle, RuntimeRunRequest
from agent.safety import SAFETY_SETTING_KEY, normalize_safety_settings
from agent.security.injection_filter import InjectionFilter
from agent.security.whitelist import Whitelist
from agent.skill_registry import SkillRegistry
from agent.structured_output import validate_output_schema
from agent.tools.execution_context import ExecutionMetadata
from agent.tools.native import (
    BashTool,
    BrowserTool,
    FileEditTool,
    FileReadTool,
    FileWriteTool,
    GlobTool,
    GrepTool,
    SkillTool,
    SleepTool,
    TodoWriteTool,
    WebFetchTool,
    WebSearchTool,
)
from agent.tools.native.bash import ExecCommandTool
from agent.tools.native.image_generate import ImageGenerateTool
from agent.tools.permission_mgr import PermissionManager
from agent.tools.tool_registry import ToolRegistry

_EntryResult = TypeVar("_EntryResult")


class AgentCore:
    RESPONSE_MODES = {"fast", "balanced", "thorough"}

    def __init__(
        self,
        config: ConfigManager,
        event_bus: EventBus,
        observability: Observability,
        model: BaseChatModel | None = None,
        repository: SQLiteRepository | None = None,
    ) -> None:
        self.config = config
        self._image_credential_provider: Callable[[str, str], Awaitable[str]] | None = None
        self.event_bus = event_bus
        self.observability = observability
        # Injected models are owned by the caller; configured models by self.models.
        self._model = model
        db_path = config.get(
            "storage.sqlite_path", config.get("memory.storage.sqlite_path", "~/.maigokite/data/agent.db")
        )
        self.repository = repository or SQLiteRepository(db_path)
        self.models = ChatModelRuntime(config, self.repository)
        self._context_max_tokens = int(config.get("context.max_tokens", 32000))
        self._extensions = ExtensionCatalog()
        workspace_root = config.workspace_root
        self._browser: BrowserRuntime | None = None
        if bool(config.get("browser.enabled", True)):
            executable_path = str(config.get("browser.executable_path", "")).strip()
            self._browser = BrowserRuntime(
                workspace_root,
                headless=bool(config.get("browser.headless", True)),
                executable_path=executable_path or None,
                max_sessions=int(config.get("browser.max_sessions", 4)),
                idle_timeout_seconds=float(config.get("browser.idle_timeout_seconds", 1800)),
            )
        allowlist_config = config.get("permissions.allowlist", {})
        if not isinstance(allowlist_config, dict):
            raise ValueError("permissions.allowlist must be an object")

        def string_set(key: str) -> set[str]:
            values = allowlist_config.get(key, [])
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise ValueError(f"permissions.allowlist.{key} must be a string array")
            return {value for value in values if value.strip()}

        allowed_paths = set()
        for value in string_set("paths"):
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = workspace_root / candidate
            allowed_paths.add(str(candidate.resolve()))
        allowlist = Whitelist(
            tools=string_set("tools"),
            command_prefixes=string_set("command_prefixes"),
            allowed_paths=allowed_paths,
        )
        circuit_breaker = config.get("permissions.circuit_breaker", {})
        if not isinstance(circuit_breaker, dict):
            raise ValueError("permissions.circuit_breaker must be an object")
        blocked_patterns = circuit_breaker.get("blocked_patterns", [])
        if not isinstance(blocked_patterns, list) or not all(isinstance(value, str) for value in blocked_patterns):
            raise ValueError("permissions.circuit_breaker.blocked_patterns must be a string array")
        if circuit_breaker.get("enabled", True) is False:
            blocked_patterns = []
        self._policy = PermissionManager(
            config.get("permissions.default_mode", "workspace"),
            workspace_root,
            allowlist,
            blocked_patterns,
        )
        self._input_filter = InjectionFilter()
        self._initialized = False
        self._shutdown_task: asyncio.Task[None] | None = None
        self._max_tool_iterations = int(config.get("tools.max_iterations", 8))
        self._extension_lock = asyncio.Lock()
        self._extension_reload_task: asyncio.Task[dict[str, Any]] | None = None
        self._extensions_reloading = False
        self._extension_error = ""
        # Failed cleanup remains owned until a later apply/shutdown drains it.
        self._retired_runtimes: list[LangGraphRuntime] = []
        self._retired_mcp_clients: dict[str, MCPClient] = {}
        self._applied_extension_revision = -1
        self._tool_credentials: dict[tuple[str, str], str] = {}
        self._safety_credential_provider: Callable[[str], Awaitable[str]] | None = None
        self._langgraph_runtime: LangGraphRuntime | None = None
        self._langgraph_entry_tasks: set[asyncio.Task[Any]] = set()
        self.processes = ProcessRegistry()
        self.memories = FolderMemories(self.repository, self._memory_folder_root)

    async def initialize(self) -> None:
        if self._initialized:
            return
        if self._shutdown_task is not None and not self._shutdown_task.done():
            raise RuntimeError("Agent Core is shutting down")
        self._shutdown_task = None
        try:
            self._policy.workspace_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"无法创建工作区目录 {self._policy.workspace_root}；"
                "请使用可写的安装目录，或通过 MAIGOKITE_WORKSPACE_ROOT 指定工作区。"
            ) from exc
        await self.repository.open()
        self.event_bus.set_sink(self.repository.append_task_event)
        try:
            applied = await self.repository.get_workspace_setting("personal", "extensions.applied_config")
            extension_config = (
                ExtensionConfig.model_validate(applied) if applied is not None else await self._read_extension_config()
            )
            self._extensions = await self._prepare_extension_catalog(extension_config)
            await self._extensions.connect_servers(
                extension_config.mcp_servers, self._policy.workspace_root, self.observability,
            )
        except BaseException as exc:
            try:
                await self.shutdown()
            except Exception as cleanup:
                raise BaseExceptionGroup("Core startup and cleanup failed", [exc, cleanup]) from None
            raise
        try:
            self._configured_tool_selection()
            self._langgraph_runtime = await self._create_langgraph_runtime(
                self._extensions.registry, self._extensions.skills,
            )
            await self.repository.save_workspace_setting(
                "personal", "extensions.applied_config", self._extensions.config.model_dump(),
            )
            self._applied_extension_revision = self._extensions.config.revision
            self._initialized = True
            await self.reconcile_interrupted_tasks()
            await self.observability.log("info", "Agent Core initialized")
        except BaseException as exc:
            try:
                await self.shutdown()
            except Exception as cleanup:
                raise BaseExceptionGroup("Core startup and cleanup failed", [exc, cleanup]) from None
            raise

    async def reconcile_interrupted_tasks(self) -> list[TaskRecord]:
        """Mark orphaned product runs interrupted; only checkpoints can resume them."""
        interrupted = await self.repository.recover_interrupted_tasks()
        interrupted.extend(await self.repository.interrupt_ephemeral_approvals({"browser"}))
        for task in interrupted:
            await self.event_bus.publish(
                "task.interrupted",
                {
                    "task_id": task["id"],
                    "conversation_id": task["conversation_id"],
                    "message": task.get("last_error") or "Agent Core stopped before a safe continuation checkpoint",
                },
            )
        return interrupted

    async def shutdown(self) -> None:
        # A caller disconnect/repeated shutdown must not interrupt cleanup.
        # This owns Core teardown only, not a second execution/cancellation tree.
        self._initialized = False
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown(), name="maigokite-core-shutdown")
        try:
            await asyncio.shield(self._shutdown_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._shutdown_task)
            raise

    async def _shutdown(self) -> None:
        # Stop admission and drain entry tasks before closing their shared clients.
        self._initialized = False
        # Also wait for apply preflight, before its resource-reload task exists.
        async with self._extension_lock:
            pass
        if self._extension_reload_task is not None:
            await asyncio.gather(self._extension_reload_task, return_exceptions=True)
        current = asyncio.current_task()
        entries = [task for task in self._langgraph_entry_tasks if task is not current]
        if entries:
            for task in entries:
                task.cancel()
            await asyncio.gather(*entries, return_exceptions=True)
        errors: list[Exception] = []
        closers = []
        if self._langgraph_runtime is not None:
            closers.append(self._langgraph_runtime.close)
        closers.append(self._extensions.close)
        closers.append(self._close_retired_extensions)
        if self._browser is not None:
            closers.append(self._browser.close)
        closers.extend([self.models.close, self.repository.close])
        # Runtime first drains all roots; only then may shared transports close.
        for close in closers:
            try:
                await close()
            except Exception as exc:
                errors.append(exc)
        self._langgraph_runtime = None
        self.event_bus.clear()
        self._extensions.registry = ToolRegistry()
        self._tool_credentials.clear()
        self._extensions.env.clear()
        self._extensions.servers.clear()
        self._initialized = False
        if errors:
            raise ExceptionGroup("Agent Core cleanup failed", errors)

    async def _prepare_extension_catalog(
        self, config: ExtensionConfig, *, restore_only: bool = False,
    ) -> ExtensionCatalog:
        catalog = ExtensionCatalog(config=config)
        registry = catalog.registry
        for tool_type in (FileReadTool, FileWriteTool, FileEditTool, BashTool, ExecCommandTool,
                          WebSearchTool, WebFetchTool, GlobTool, GrepTool, SleepTool):
            registry.register(tool_type())
        registry.register(TodoWriteTool(self.repository))
        registry.register(ImageGenerateTool(self.repository, self._get_image_credential))
        if restore_only:
            catalog.skills = self._extensions.skills
            catalog.inventory = self._extensions.inventory
            catalog.skill_errors = self._extensions.skill_errors
        else:
            await catalog.load_skills(self._policy.workspace_root)
        registry.register(SkillTool(catalog.skills))
        if self._browser is not None:
            registry.register(BrowserTool(self._browser))
        return catalog

    async def _close_retired_extensions(self) -> None:
        errors: list[Exception] = []
        for runtime in list(self._retired_runtimes):
            try:
                await runtime.close()
            except Exception as exc:
                errors.append(exc)
            else:
                self._retired_runtimes.remove(runtime)
        try:
            await close_mcp_clients(self._retired_mcp_clients)
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise ExceptionGroup("Extension cleanup incomplete", errors)

    async def _create_langgraph_runtime(self, registry: ToolRegistry, skills: SkillRegistry) -> LangGraphRuntime:
        """Reuse product tools; LangChain owns all runtime invocation."""
        from agent.runtime.subagents import DelegateTool, ListSubagentModelsTool
        from agent.tools.native.ask_user_question import AskUserQuestionTool

        registry.register(AskUserQuestionTool())
        registry.register(DelegateTool())
        registry.register(ListSubagentModelsTool())
        specs = [
            ProductToolSpec(tool, tool.to_langchain_tool())
            for tool in registry.list_tools() if tool.name != "bash"
        ]

        configured = str(self.config.get("agent.langgraph_checkpoint_path", "")).strip()
        if configured:
            checkpoint_path = configured
        else:
            # The actual product repository is authoritative, including an
            # explicitly injected temporary repository. Never silently write
            # its checkpoints to the user's unrelated default database path.
            db_value = self.repository.path
            if db_value == ":memory:":
                checkpoint_path = str(
                    self._policy.workspace_root / ".maigokite-langgraph-checkpoints.sqlite"
                )
            else:
                database_path = Path(db_value).expanduser()
                checkpoint_path = str(
                    database_path.with_name(
                        f"{database_path.stem}.langgraph{database_path.suffix or '.sqlite'}"
                    )
                )
        runtime = LangGraphRuntime(
            model_resolver=self._resolve_langgraph_model,
            model_catalog=self.models.subagent_catalog,
            workspace_root=self._policy.workspace_root,
            checkpoint_path=checkpoint_path,
            repository=self.repository,
            policy=self._policy,
            event_publisher=self.event_bus.publish,
            tools=specs,
            system_prompt=skills.catalog_prompt(),
            context_max_tokens=self._context_max_tokens,
            max_model_calls=self._max_tool_iterations,
            execution_metadata=self._langgraph_execution_metadata,
            safety_settings=self.get_safety_settings,
            safety_credential=self._get_safety_credential,
        )
        try:
            await runtime.open()
        except BaseException:
            self._retired_runtimes.append(runtime)
            raise
        return runtime

    async def _langgraph_execution_metadata(self, request: RuntimeRunRequest) -> ExecutionMetadata:
        return await self._execution_metadata(
            request.workspace_id, request.conversation_id, request.principal_id,
        )

    async def _get_safety_credential(self, workspace_id: str) -> str:
        if self._safety_credential_provider is None:
            return ""
        return await self._safety_credential_provider(workspace_id)

    def set_safety_credential_provider(self, provider: Callable[[str], Awaitable[str]]) -> None:
        self._safety_credential_provider = provider

    def set_image_credential_provider(self, provider: Callable[[str, str], Awaitable[str]]) -> None:
        self._image_credential_provider = provider

    async def _get_image_credential(self, workspace_id: str, reference: str) -> str:
        if self._image_credential_provider is None:
            return ""
        return await self._image_credential_provider(workspace_id, reference)

    def _configured_tool_selection(self) -> tuple[set[str], set[str]]:
        """Defaults are visibility preferences, disabled_tools is a config ceiling."""
        defaults = self.config.get("tools.default_tools", list(ToolRegistry.DEFAULT_TOOLS))
        disabled = self.config.get("tools.disabled_tools", [])
        for key, values in (("default_tools", defaults), ("disabled_tools", disabled)):
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                raise ValueError(f"tools.{key} must be a string array")
        defaults = ["exec_command" if name == "bash" else name for name in defaults]
        disabled = ["exec_command" if name == "bash" else name for name in disabled]
        available = {tool.name for tool in self._extensions.registry.list_tools()}
        known = available | set(ToolRegistry.DEFAULT_TOOLS) | {"sleep", "todo_write"}
        known |= {"ask_user_question", "delegate", "list_subagent_models"}
        # A stopped/removed MCP server must not make all native tools unusable.
        # Stale namespaced preferences stay inert until that tool is discovered.
        unknown = {name for name in (set(defaults) | set(disabled)) - known if "__" not in name}
        if unknown:
            raise ValueError(f"unknown configured tools: {', '.join(sorted(unknown))}")
        # Connected MCP tools are selected at their server boundary first.
        defaults = set(defaults) | {name for name in available if "__" in name}
        defaults |= {"ask_user_question", "delegate", "list_subagent_models"}
        return defaults - {"bash"}, set(disabled) | {"bash"}

    async def get_tool_settings(self, workspace_id: str) -> dict[str, Any]:
        defaults, disabled = self._configured_tool_selection()
        overrides = await self.repository.get_workspace_setting(workspace_id, "tools.selection")
        overrides = overrides if isinstance(overrides, dict) else {}
        return {"tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "source": "mcp" if "__" in tool.name else "builtin",
                "enabled": tool.name not in disabled and overrides.get(tool.name, tool.name in defaults) is True,
                "configurable": tool.name not in disabled,
                "default_enabled": tool.name in defaults and tool.name not in disabled,
            }
            for tool in self._extensions.registry.list_tools() if tool.name != "bash"
        ]}

    async def update_tool_settings(self, workspace_id: str, enabled: dict[str, bool]) -> dict[str, Any]:
        catalog = {item["name"]: item for item in (await self.get_tool_settings(workspace_id))["tools"]}
        for name, value in enabled.items():
            if name not in catalog or not isinstance(value, bool):
                raise ValueError(f"invalid tool selection: {name}")
            if value and not catalog[name]["configurable"]:
                raise ValueError(f"tool disabled by startup configuration: {name}")
        await self.repository.update_tool_selection(workspace_id, enabled)
        return await self.get_tool_settings(workspace_id)

    async def _enabled_tool_names(self, workspace_id: str) -> list[str]:
        running = {name for item in await self.list_mcp_servers() if item["status"] == "running"
                   for name in item["tool_names"]}
        # Keep saved preferences intact, but never advertise a dead connection
        # as executable. Admission rechecks this list before every tool call.
        return [item["name"] for item in (await self.get_tool_settings(workspace_id))["tools"]
                if item["enabled"] and (item["source"] != "mcp" or item["name"] in running)]

    async def _resolve_langgraph_model(
        self, workspace_id: str, model_id: str, thinking: str
    ) -> tuple[str, BaseChatModel]:
        if self._model is not None:
            return f"injected:{id(self._model)}", self._model
        return await self.models.get_langchain_model(workspace_id, model_id, thinking=thinking)

    async def get_permission_settings(self, workspace_id: str) -> dict[str, Any]:
        stored = await self.repository.get_workspace_setting(workspace_id, "permissions.policy")
        if not isinstance(stored, dict):
            return {"default_mode": self._policy.mode}
        try:
            mode = self._policy.normalize_mode(str(stored.get("default_mode", self._policy.mode)))
        except ValueError:
            mode = self._policy.mode
        return {"default_mode": mode}

    async def update_permission_settings(
        self, workspace_id: str, default_mode: str
    ) -> dict[str, Any]:
        try:
            default_mode = self._policy.normalize_mode(default_mode)
        except ValueError as exc:
            raise ValueError("invalid permission mode") from exc
        value = {"default_mode": default_mode}
        await self.repository.save_workspace_setting(workspace_id, "permissions.policy", value)
        return value

    async def get_safety_settings(self, workspace_id: str) -> dict[str, Any]:
        stored = await self.repository.get_workspace_setting(workspace_id, SAFETY_SETTING_KEY)
        return normalize_safety_settings(stored)

    async def update_safety_settings(
        self, workspace_id: str, settings: dict[str, Any]
    ) -> dict[str, Any]:
        # The DTO already bounds threshold and list length; normalization drops
        # unknown keys (credential_configured) and repairs legacy stored values.
        value = normalize_safety_settings(settings)
        if value["enabled"] and not (
            value["decision_base_url"].strip() and value["decision_model"].strip()
        ):
            raise ValueError(
                "decision_base_url and decision_model are required when safety review is enabled"
            )
        await self.repository.save_workspace_setting(workspace_id, SAFETY_SETTING_KEY, value)
        return value

    async def create_conversation(
        self,
        workspace_id: str = "personal",
        title: str = "",
        model_id: str = "",
        permission_mode: str | None = None,
        response_mode: str = "balanced",
        folder_id: str = "",
        max_subagent_calls: int = 0,
        max_concurrent_subagents: int = 1,
        subagent_model_ids: list[str] | None = None,
        thinking: str = "",
        memory_enabled: bool = False,
    ) -> ConversationRecord:
        mode = permission_mode or (await self.get_permission_settings(workspace_id))["default_mode"]
        try:
            mode = self._policy.normalize_mode(mode)
        except ValueError as exc:
            raise ValueError("invalid permission mode") from exc
        if response_mode not in self.RESPONSE_MODES:
            raise ValueError("invalid response mode")
        if model_id:
            await self.models.get_profile(workspace_id, model_id)
        else:
            _, model_id = await self.models.list_settings(workspace_id)
        await self.models.validate_thinking(workspace_id, model_id, thinking)
        for child_model_id in validate_subagent_model_ids([] if subagent_model_ids is None else subagent_model_ids):
            await self.models.get_profile(workspace_id, child_model_id)
        return await self.repository.create_conversation(
            workspace_id, title, model_id, mode, response_mode, folder_id,
            max_subagent_calls, max_concurrent_subagents, subagent_model_ids,
            thinking=thinking, memory_enabled=memory_enabled,
        )

    async def update_conversation_permission(
        self, workspace_id: str, conversation_id: str, permission_mode: str
    ) -> ConversationRecord:
        try:
            permission_mode = self._policy.normalize_mode(permission_mode)
        except ValueError as exc:
            raise ValueError("invalid permission mode") from exc
        return await self.repository.update_conversation_permission(workspace_id, conversation_id, permission_mode)

    async def update_conversation_settings(
        self,
        workspace_id: str,
        conversation_id: str,
        *,
        model_id: str | None = None,
        response_mode: str | None = None,
        thinking: str | None = None,
        permission_mode: str | None = None,
        title: str | None = None,
        folder_id: str | None = None,
        archived: bool | None = None,
        max_subagent_calls: int | None = None,
        max_concurrent_subagents: int | None = None,
        subagent_model_ids: list[str] | None = None,
        memory_enabled: bool | None = None,
    ) -> ConversationRecord:
        if folder_id is not None or archived is not None:
            self._require_inactive_conversation(conversation_id)
        if folder_id is not None:
            await self.get_workspace_folder(workspace_id, folder_id)
        if title is not None:
            title = title.strip()
            if not title or len(title) > 200:
                raise ValueError("对话名称须为 1–200 个字符。")
        if permission_mode is not None:
            try:
                permission_mode = self._policy.normalize_mode(permission_mode)
            except ValueError as exc:
                raise ValueError("invalid permission mode") from exc
        if response_mode is not None and response_mode not in self.RESPONSE_MODES:
            raise ValueError("invalid response mode")
        if model_id is not None and model_id:
            await self.models.get_profile(workspace_id, model_id)
        if model_id is not None or thinking is not None:
            self._require_inactive_conversation(conversation_id)
            current = await self.repository.get_conversation(workspace_id, conversation_id)
            if thinking is None and model_id != current.get("model_id"):
                thinking = ""  # Never carry a provider-specific level across models.
            await self.models.validate_thinking(
                workspace_id, model_id if model_id is not None else str(current.get("model_id") or ""),
                thinking if thinking is not None else str(current.get("thinking") or ""),
            )
        if subagent_model_ids is not None:
            for child_model_id in validate_subagent_model_ids(subagent_model_ids):
                await self.models.get_profile(workspace_id, child_model_id)
        return await self.repository.update_conversation_settings(
            workspace_id,
            conversation_id,
            model_id=model_id,
            response_mode=response_mode,
            thinking=thinking,
            permission_mode=permission_mode,
            title=title,
            folder_id=folder_id,
            archived=archived,
            max_subagent_calls=max_subagent_calls,
            max_concurrent_subagents=max_concurrent_subagents,
            subagent_model_ids=subagent_model_ids,
            memory_enabled=memory_enabled,
        )

    async def list_conversations(self, workspace_id: str = "personal") -> list[ConversationRecord]:
        return await self.repository.list_conversations(workspace_id)

    async def get_conversation(self, workspace_id: str, conversation_id: str) -> ConversationRecord:
        return await self.repository.get_conversation(workspace_id, conversation_id)

    def _require_inactive_conversation(self, conversation_id: str) -> None:
        if self._langgraph_runtime is not None and self._langgraph_runtime.has_active_conversation(conversation_id):
            raise ConversationBusyError("此对话的任务尚未结束，请稍后再管理。")

    async def delete_conversation(self, workspace_id: str, conversation_id: str) -> None:
        self._require_inactive_conversation(conversation_id)
        task_ids = await self.repository.delete_conversation(workspace_id, conversation_id)
        if self._langgraph_runtime is not None:
            await self._langgraph_runtime.delete_threads(task_ids)

    async def fork_conversation(
        self, workspace_id: str, conversation_id: str, message_id: str, request_key: str,
    ) -> ConversationRecord:
        self._require_inactive_conversation(conversation_id)
        if len(message_id) > 200:
            raise ValueError("Invalid source message ID")
        return await self.repository.fork_conversation(workspace_id, conversation_id, message_id, request_key)

    async def get_workspace_folder(self, workspace_id: str, folder_id: str) -> dict[str, Any]:
        if not folder_id:
            return {"id": "", "name": "默认工作区", "root_path": str(self._policy.workspace_root)}
        item = await self.repository.get_workspace_folder(workspace_id, folder_id)
        root = Path(item["root_path"])
        if not root.is_dir() or str(root.resolve(strict=True)) != item["root_path"]:
            raise ValueError("工作区文件夹不存在或路径已变更，请重新选择文件夹。")
        return item

    async def list_workspace_folders(self, workspace_id: str) -> list[dict[str, Any]]:
        return [await self.get_workspace_folder(workspace_id, ""),
                *await self.repository.list_workspace_folders(workspace_id)]

    async def create_workspace_folder(self, workspace_id: str, root_path: str, name: str = "") -> dict[str, Any]:
        if not root_path.strip() or len(root_path) > 4096 or "\0" in root_path:
            raise ValueError("请选择有效的文件夹。")
        root = Path(root_path).expanduser()
        if not root.is_absolute():
            raise ValueError("工作区文件夹须使用绝对路径。")
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("工作区路径不是文件夹。")
        self._policy.for_workspace(root)  # Preserve the existing filesystem-root boundary.
        if root == self._policy.workspace_root:
            return await self.get_workspace_folder(workspace_id, "")
        name = name.strip() or root.name
        if len(name) > 120:
            raise ValueError("工作区名称不能超过 120 个字符。")
        return await self.repository.create_workspace_folder(workspace_id, name, str(root))

    async def delete_workspace_folder(
        self, workspace_id: str, folder_id: str, principal_id: str = "local-user",
    ) -> None:
        if await self.repository.check_workspace_access(workspace_id, principal_id) != "owner":
            raise PermissionError("只有工作区所有者可以删除工作区。")
        if not folder_id:
            raise ValueError("默认工作区不能删除。")
        # Read the registration, not the directory: a missing disk folder must
        # remain removable from the app. Terminal projections may still be cleaning up.
        await self.repository.get_workspace_folder(workspace_id, folder_id)
        for conversation in await self.repository.list_conversations(workspace_id):
            if conversation.get("folder_id") == folder_id:
                if any(job["live"] for job in await self.list_jobs(workspace_id, str(conversation["id"]))):
                    raise ConversationBusyError("工作区中有运行中的进程，请停止后再删除。")
                try:
                    self._require_inactive_conversation(str(conversation["id"]))
                except ConversationBusyError:
                    raise ConversationBusyError("工作区中有未结束的任务，请完成或停止后再删除。") from None
        await self.repository.delete_workspace_folder(workspace_id, folder_id, principal_id)

    async def list_messages(self, conversation_id: str, workspace_id: str = "personal") -> list[MessageRecord]:
        await self.repository.get_conversation(workspace_id, conversation_id)
        return await self.repository.list_messages(conversation_id)

    async def run(self, prompt: str, metadata: RunMetadata | None = None) -> RunResult:
        if not self._initialized:
            raise RuntimeError("Agent Core is not initialized")
        metadata = RUN_METADATA.validate_python(metadata or {}, strict=True)
        metadata["output_schema"] = validate_output_schema(metadata.get("output_schema"))
        key = str(metadata.get("idempotency_key") or "")
        if key:
            if len(key) > 128 or not key.isascii() or any(ord(char) < 33 or ord(char) > 126 for char in key):
                raise ValueError("Idempotency-Key must contain 1 to 128 printable ASCII characters without spaces")
            conversation_id = str(metadata.get("conversation_id") or metadata.get("session_id") or "")
            if not conversation_id:
                raise ValueError("a conversation is required for an idempotent message")
            workspace_id = str(metadata.get("workspace_id", "personal"))
            await self.repository.get_conversation(workspace_id, conversation_id)
            identity = "\x00".join(
                (workspace_id, conversation_id, str(metadata.get("principal_id", "local-user")), key)
            )
            metadata["task_id"] = "task_" + hashlib.sha256(identity.encode()).hexdigest()
            payload: JSONObject = {"content": prompt, "attachments": [*metadata.get("attachments", [])]}
            if metadata["output_schema"] is not None:
                # Preserve existing chat submission hashes; structured contracts
                # are part of identity and cannot change under a reused key.
                payload["output_schema"] = metadata["output_schema"]
            body = json.dumps(payload,
                              sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            metadata["request_hash"] = hashlib.sha256(body.encode()).hexdigest()
        try:
            if key:
                await self.repository.check_message_replay(workspace_id, metadata["task_id"], metadata["request_hash"])
            return await self._run_chat(prompt, metadata)
        except MessageReplay as replay:
            return {"task_id": replay.task["id"], "conversation_id": replay.task["conversation_id"],
                    "status": replay.task["status"], "replayed": True}

    async def _run_chat(self, prompt: str, metadata: RunMetadata) -> RunResult:
        if not self._initialized:
            raise RuntimeError("Agent Core is shutting down")
        prompt = self._input_filter.sanitize_input(prompt)
        return await self._track_langgraph_entry(
            self._run_langgraph_chat(prompt, metadata)
        )

    async def _run_langgraph_chat(
        self, prompt: str, metadata: RunMetadata
    ) -> RunResult:
        runtime = self._langgraph_runtime
        if runtime is None:
            raise RuntimeError("LangGraph runtime is not initialized")
        workspace_id = str(metadata.get("workspace_id", "personal"))
        principal_id = str(metadata.get("principal_id", "local-user"))
        conversation_id = str(
            metadata.get("conversation_id") or metadata.get("session_id") or ""
        )
        if not conversation_id:
            conversation_id = (
                await self.create_conversation(workspace_id, title=prompt[:30])
            )["id"]
        else:
            try:
                await self.repository.get_conversation(workspace_id, conversation_id)
            except KeyError:
                if metadata.get("request_hash"):
                    raise
                conversation_id = (
                    await self.create_conversation(workspace_id, title=prompt[:30])
                )["id"]
        conversation = await self.repository.get_conversation(
            workspace_id, conversation_id
        )
        await self.get_workspace_folder(workspace_id, str(conversation.get("folder_id") or ""))
        request = RuntimeRunRequest(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            principal_id=principal_id,
            permission_mode=conversation["permission_mode"],
            trace_id=str(metadata.get("trace_id") or f"trace_{uuid.uuid4().hex}"),
            task_id=str(metadata.get("task_id") or ""),
            request_hash=str(metadata.get("request_hash") or ""),
            output_schema=metadata.get("output_schema"),
            attachments=tuple(ImageReference.model_validate(item) for item in metadata.get("attachments", [])),
        )
        handle = await runtime.start(prompt, request)
        await self._wait_for_run(handle)
        return await self._langgraph_result(
            workspace_id, handle.request.task_id, conversation_id
        )

    @staticmethod
    async def _wait_for_run(handle: RunHandle) -> None:
        """Drain events and await root cleanup when the caller is cancelled."""
        try:
            async for _event in handle.events():
                pass  # Events already persist/stream; do not retain a second full list.
            await handle.wait()
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                await asyncio.shield(handle.cancel())
                raise

    async def _track_langgraph_entry(
        self, operation: Coroutine[Any, Any, _EntryResult]
    ) -> _EntryResult:
        if not self._initialized or self._extensions_reloading:
            operation.close()
            raise ConversationBusyError("Agent Core is starting, closing, or applying extensions; retry when ready")
        current = asyncio.current_task()
        if current is not None:
            self._langgraph_entry_tasks.add(current)
        try:
            return await operation
        finally:
            if current is not None:
                self._langgraph_entry_tasks.discard(current)

    async def _langgraph_result(
        self, workspace_id: str, task_id: str, conversation_id: str
    ) -> RunResult:
        task = await self.repository.get_task(workspace_id, task_id)
        status = task["status"]
        if status == "waiting_approval":
            questions = [item for item in await self.repository.list_questions(workspace_id, conversation_id)
                         if str(item["task_id"]) == task_id]
            if questions:
                return {"task_id": task_id, "conversation_id": conversation_id,
                        "status": status, "questions": questions}
            approvals = await self.repository.list_approvals(
                workspace_id, conversation_id
            )
            approval = next(
                item for item in approvals if str(item["task_id"]) == task_id
            )
            return {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "status": status,
                "approval": self._approval_payload(approval),
            }
        if status == "succeeded":
            messages = await self.repository.list_messages(conversation_id)
            response = next(
                (
                    str(item["content"])
                    for item in reversed(messages)
                    if str(item["role"]) == "assistant"
                ),
                "",
            )
            return {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "response": response,
                "status": status,
            }
        if status == "cancelled":
            return {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "status": status,
                "error_code": "TASK_CANCELLED",
            }
        return {
            "task_id": task_id,
            "conversation_id": conversation_id,
            "status": status,
            "error": str(task.get("last_error") or "Agent execution failed"),
            "error_code": "AGENT_ERROR",
            "retryable": False,
        }

    async def _execution_metadata(
        self,
        workspace_id: str,
        conversation_id: str,
        principal_id: str,
    ) -> ExecutionMetadata:
        conversation = await self.repository.get_conversation(workspace_id, conversation_id)
        folder = await self.get_workspace_folder(workspace_id, str(conversation.get("folder_id") or ""))
        api_keys = {
            service: secret
            for (scope, service), secret in self._tool_credentials.items()
            if scope == workspace_id and secret
        }
        return {
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "principal_id": principal_id,
            "permission_mode": self._policy.normalize_mode(conversation["permission_mode"]),
            "process_registry": self.processes,
            "command_limits": JSON_OBJECT.validate_python(
                self.config.get("tools.command_limits", {}) or {}, strict=True
            ),
            "linux_sandbox": JSON_OBJECT.validate_python(
                self.config.get("tools.linux_sandbox", {}) or {}, strict=True
            ),
            "windows_sandbox": JSON_OBJECT.validate_python(
                self.config.get("tools.windows_sandbox", {}) or {}, strict=True
            ),
            "approved": False,
            "workspace_root": folder["root_path"],
            "api_keys": api_keys,
            "image_configuration": ImageSettings.model_validate(
                await self.repository.get_workspace_setting(workspace_id, IMAGE_SETTING_KEY) or {}
            ).fingerprint(),
            "enabled_tools": await self._enabled_tool_names(workspace_id),
            "model_id": str(conversation.get("model_id") or ""),
        }

    @staticmethod
    def _approval_payload(approval: ApprovalRecord) -> ApprovalNotice:
        return approval_notice(approval)

    async def list_approvals(self, workspace_id: str, conversation_id: str | None = None) -> list[ApprovalNotice]:
        return [
            self._approval_payload(item) for item in await self.repository.list_approvals(workspace_id, conversation_id)
        ]

    async def decide_approval(
        self,
        workspace_id: str,
        approval_id: str,
        principal_id: str,
        decision: str,
        version: int,
        note: str = "",
        scope: str = "once",
    ) -> RunResult:
        if not self._initialized:
            raise RuntimeError("Agent Core is not accepting new runtime requests")
        pending = await self.repository.get_approval(workspace_id, approval_id)
        # Only native HITL may authorize execution. Historical approvals without
        # checkpoints remain inspectable and rejectable, but cannot be replayed.
        return await self._track_langgraph_entry(
            self._resume_langgraph_approval(
                workspace_id, approval_id, principal_id, decision, version,
                str(pending["task_id"]), str(pending["conversation_id"]), note, scope,
            )
        )

    async def _resume_langgraph_approval(
        self,
        workspace_id: str,
        approval_id: str,
        principal_id: str,
        decision: str,
        version: int,
        task_id: str,
        conversation_id: str,
        note: str = "",
        scope: str = "once",
    ) -> RunResult:
        runtime = self._langgraph_runtime
        if runtime is None:
            raise RuntimeError("LangGraph runtime is not initialized")
        handle = await runtime.resume_approval(
            workspace_id,
            approval_id,
            principal_id,
            decision,
            version,
            note,
            scope,
        )
        await self._wait_for_run(handle)
        return await self._langgraph_result(
            workspace_id, task_id, conversation_id
        )

    async def interrupt(self) -> None:
        if not self._initialized:
            raise RuntimeError("Agent Core is not accepting new runtime requests")
        if self._langgraph_runtime is None:
            raise RuntimeError("LangGraph runtime is not initialized")
        await self._track_langgraph_entry(self._langgraph_runtime.cancel_active_runs())

    async def get_task(self, workspace_id: str, task_id: str) -> TaskRecord:
        return await self.repository.get_task(workspace_id, task_id)

    async def resume_task(
        self, workspace_id: str, task_id: str, principal_id: str, version: int, trace_id: str = "",
    ) -> RunResult:
        return await self._track_langgraph_entry(
            self._resume_task(workspace_id, task_id, principal_id, version, trace_id),
        )

    async def _resume_task(
        self, workspace_id: str, task_id: str, principal_id: str, version: int, trace_id: str,
    ) -> RunResult:
        runtime = self._langgraph_runtime
        if runtime is None:
            raise RuntimeError("LangGraph runtime is not initialized")
        handle = await runtime.recover(
            workspace_id, task_id, principal_id=principal_id, version=version, trace_id=trace_id,
        )
        await self._wait_for_run(handle)
        return await self._langgraph_result(workspace_id, task_id, handle.request.conversation_id)

    async def answer_question(self, workspace_id: str, question_id: str, principal_id: str,
                              answer: str, version: int) -> RunResult:
        return await self._track_langgraph_entry(
            self._answer_question(workspace_id, question_id, principal_id, answer, version),
        )

    async def _answer_question(self, workspace_id: str, question_id: str, principal_id: str,
                               answer: str, version: int) -> RunResult:
        runtime = self._langgraph_runtime
        if runtime is None:
            raise RuntimeError("LangGraph runtime is not initialized")
        handle = await runtime.answer_question(workspace_id, question_id, principal_id, answer, version)
        await self._wait_for_run(handle)
        return await self._langgraph_result(workspace_id, handle.request.task_id, handle.request.conversation_id)

    async def list_jobs(self, workspace_id: str, conversation_id: str = "") -> list[dict[str, Any]]:
        jobs = []
        for call in await self.repository.list_command_calls(workspace_id, conversation_id):
            result = json.loads(call["result_json"] or "{}")
            data = result.get("data", result)
            data = data if isinstance(data, dict) else {}
            status = str(call["status"])
            active = self.processes.snapshot(workspace_id, str(call["id"]))
            if active is None and "return_code" not in data:
                # A proposal (or sandbox setup failure) is not a spawned job.
                continue
            if not active and status == "running" and call["task_status"] in {"interrupted", "cancelled", "failed"}:
                status = str(call["task_status"])
            jobs.append(
                {
                    "id": str(call["id"]),
                    "task_id": str(call["task_id"]),
                    "conversation_id": str(call["conversation_id"]),
                    "command": str(json.loads(call["arguments_json"]).get("command", "")),
                    "status": status,
                    "live": bool(active and active["live"]),
                    "live_logs_available": bool(active and active["live_logs_available"]),
                    "stdin_available": bool(active and active["stdin_available"]),
                    "log_tail": str(active["log_tail"])
                    if active
                    else "\n".join(str(data.get(key) or "")[-4000:] for key in ("stdout", "stderr")),
                }
            )
        return jobs

    async def stop_job(self, workspace_id: str, job_id: str, principal_id: str = "local-user") -> None:
        call = await self.repository.get_tool_call(job_id)
        if call["workspace_id"] != workspace_id or call["tool_name"] not in {"exec_command", "bash"}:
            raise KeyError(job_id)
        await self.processes.stop(workspace_id, job_id)
        await self.repository.record_audit(workspace_id, principal_id, "process.stopped", job_id, "success", {})

    async def write_job_input(self, workspace_id: str, job_id: str, principal_id: str, text: str, eof: bool) -> None:
        call = await self.repository.get_tool_call(job_id)
        if call["workspace_id"] != workspace_id or call["tool_name"] not in {"exec_command", "bash"}:
            raise KeyError(job_id)
        await self.processes.write_input(workspace_id, job_id, text, eof)
        # Never persist stdin contents: users may enter passwords or other secrets.
        await self.repository.record_audit(workspace_id, principal_id, "process.input", job_id, "success",
                                           {"bytes": len(text.encode("utf-8")), "eof": eof})

    async def list_tasks(self, workspace_id: str, conversation_id: str = "", limit: int = 100) -> list[TaskRecord]:
        return await self.repository.list_tasks(workspace_id, conversation_id, limit)

    async def submit_task_input(
        self, workspace_id: str, task_id: str, content: str, idempotency_key: str
    ) -> dict[str, Any]:
        if not self._initialized or self._langgraph_runtime is None:
            raise RuntimeError("Agent runtime is not ready")
        return await self._langgraph_runtime.submit_input(workspace_id, task_id, content, idempotency_key)

    async def list_task_events(
        self,
        workspace_id: str,
        conversation_id: str,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[TaskEventRecord]:
        await self.repository.get_conversation(workspace_id, conversation_id)
        return await self.repository.list_task_events(workspace_id, conversation_id, after_sequence, limit)

    async def cancel_task(self, workspace_id: str, task_id: str) -> TaskRecord:
        if not self._initialized:
            raise RuntimeError("Agent Core is not accepting new runtime requests")
        if self._langgraph_runtime is None:
            raise RuntimeError("LangGraph runtime is not initialized")
        # Cancellation waits for the owning root task, including cleanup.
        return await self._track_langgraph_entry(
            self._langgraph_runtime.cancel(workspace_id, task_id)
        )

    @property
    def _mcp_clients(self) -> Mapping[str, MCPClient]:
        """Expose the active catalog's connections to existing lifecycle diagnostics."""
        return self._extensions.clients

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._extensions.registry

    async def get_chat_model_context(self, workspace_id: str, model_id: str) -> dict[str, Any]:
        from agent.models.context_capacity import resolve_context_capacity
        from agent.runtime.context import context_payload

        _, model = await self.models.get_langchain_model(workspace_id, model_id)
        capacity = await resolve_context_capacity(model)
        return context_payload(model, capacity, self._context_max_tokens)

    async def upsert_chat_model(self, workspace_id: str, settings: ChatModelSettings) -> ChatModelSettings:
        return await self.models.upsert_settings(workspace_id, settings)

    async def set_chat_model_credential(
        self, workspace_id: str, provider: str, api_key: str, model_id: str = ""
    ) -> None:
        await self.models.set_credential(workspace_id, provider, api_key, model_id)

    async def set_tool_credential(self, workspace_id: str, service: str, api_key: str) -> None:
        if service not in {"tavily", "google"}:
            raise ValueError("unsupported tool credential service")
        key = (workspace_id, service)
        if api_key:
            self._tool_credentials[key] = api_key
        else:
            self._tool_credentials.pop(key, None)

    async def get_user_preferences(self, workspace_id: str, user_id: str) -> dict[str, str]:
        value = await self.repository.get_preference(workspace_id, user_id, "interface.assistant_name")
        name = str(value).strip() if value is not None else ""
        return {"assistant_name": name or "助手"}

    async def update_user_preferences(self, workspace_id: str, user_id: str, assistant_name: str) -> dict[str, str]:
        name = " ".join(assistant_name.split())
        if not name:
            raise ValueError("assistant name is required")
        if len(name) > 32:
            raise ValueError("assistant name must not exceed 32 characters")
        if any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise ValueError("assistant name contains control characters")
        await self.repository.save_preference(workspace_id, user_id, "interface.assistant_name", name)
        return {"assistant_name": name}

    async def list_memories(
        self, workspace_id: str, user_id: str, query: str = "", limit: int = 100, folder_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.memories.list_memories(workspace_id, user_id, query, limit, folder_id)

    async def _memory_folder_root(self, workspace_id: str, folder_id: str) -> str:
        folder = await self.get_workspace_folder(workspace_id, folder_id)
        return str(folder["root_path"])

    async def get_memory_settings(self, workspace_id: str, user_id: str, folder_id: str) -> dict[str, Any]:
        return await self.memories.get_memory_settings(workspace_id, user_id, folder_id)

    async def set_memory_settings(
        self, workspace_id: str, user_id: str, folder_id: str, enabled: bool
    ) -> dict[str, Any]:
        return await self.memories.set_memory_settings(workspace_id, user_id, folder_id, enabled)

    async def upsert_memory(
        self,
        workspace_id: str,
        user_id: str,
        *,
        memory_id: str = "",
        category: str,
        content: str,
        confidence: float = 1.0,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        return await self.memories.upsert_memory(
            workspace_id,
            user_id,
            memory_id=memory_id,
            category=category,
            content=content,
            confidence=confidence,
            folder_id=folder_id,
        )

    async def delete_memory(self, workspace_id: str, user_id: str, memory_id: str) -> None:
        await self.memories.delete_memory(workspace_id, user_id, memory_id)

    async def get_workspace_data_summary(self, workspace_id: str) -> dict[str, Any]:
        return await self.repository.get_workspace_data_summary(workspace_id)

    async def list_mcp_servers(self) -> list[MCPServerStatus]:
        return self._extensions.server_status()

    async def list_skills(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._extensions.skills.list_skills()]

    async def _read_extension_config(self) -> ExtensionConfig:
        saved = await self.repository.get_workspace_setting("personal", "extensions.config")
        if saved is None:
            saved = await self.repository.get_workspace_setting("personal", "extensions.applied_config")
        return ExtensionConfig.model_validate(saved) if saved is not None else startup_config(self.config)

    @staticmethod
    def _require_personal_extensions(workspace_id: str) -> None:
        if workspace_id != "personal":
            raise PermissionError("Extensions are managed by the personal workspace owner")

    async def get_extensions(self, workspace_id: str) -> dict[str, Any]:
        self._require_personal_extensions(workspace_id)
        config = await self._read_extension_config()
        return {
            "config": config.model_dump(), "applied_revision": self._applied_extension_revision,
            "active_config": self._extensions.config.model_dump(include={"revision", "mcp_servers"}),
            "extension_error": self._extension_error,
            "reload_required": config.revision != self._applied_extension_revision,
            "reloading": self._extensions_reloading,
            "runtime_ready": self._langgraph_runtime is not None,
            "servers": await self.list_mcp_servers(), "skills": self._extensions.inventory,
            "skill_errors": self._extensions.skill_errors,
        }

    async def update_extensions(self, workspace_id: str, value: dict[str, Any]) -> dict[str, Any]:
        self._require_personal_extensions(workspace_id)
        # Pydantic validates the entire replacement before any persistence.
        candidate = ExtensionConfig.model_validate(value)
        candidate.validate_paths(self._policy.workspace_root)
        async with self._extension_lock:
            if not self._initialized:
                raise ConversationBusyError("Agent Core is not ready to save extensions")
            current = await self._read_extension_config()
            if candidate.revision != current.revision:
                raise ConversationBusyError("Extension settings changed; reload the page before saving")
            candidate.revision += 1
            await self.repository.save_workspace_setting(workspace_id, "extensions.config", candidate.model_dump())
            await self.repository.record_audit(workspace_id, "local-user", "extensions.saved", "extensions", "success",
                                               {"revision": candidate.revision})
        return await self.get_extensions(workspace_id)

    async def reload_extensions(
        self, workspace_id: str, revision: int, credentials: dict[str, Any], *, restore_only: bool = False,
    ) -> dict[str, Any]:
        self._require_personal_extensions(workspace_id)
        if self._extension_reload_task is not None and not self._extension_reload_task.done():
            raise ConversationBusyError("扩展正在应用，请刷新状态，勿重复提交。")
        # The owner holds admission/lock until cleanup, even if HTTP callers leave.
        self._extension_reload_task = asyncio.create_task(
            self._reload_extensions(workspace_id, revision, credentials, restore_only=restore_only),
        )
        self._extension_reload_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        return await asyncio.shield(self._extension_reload_task)

    async def _reload_extensions(
        self, workspace_id: str, revision: int, credentials: dict[str, Any], *, restore_only: bool,
    ) -> dict[str, Any]:
        async with self._extension_lock:
            if not self._initialized or self._extensions_reloading or self._langgraph_entry_tasks:
                raise ConversationBusyError("请先结束正在执行的任务，再应用扩展。")
            # No await between blocking admission and checking persisted tasks.
            self._extensions_reloading = True
            try:
                candidate = self._extensions.config if restore_only else await self._read_extension_config()
                if candidate.revision != revision:
                    raise ConversationBusyError("Extension settings changed; reload before applying")
                if await self.repository.has_unfinished_tasks(include_waiting=not restore_only):
                    raise ConversationBusyError("请先结束运行中的任务；应用新配置还需要完成或取消待处理审批和提问。")
                env = self._extensions.resolve_credentials(candidate, credentials, restore_only=restore_only)
                await self._apply_extensions(candidate, env, restore_only=restore_only)
            finally:
                self._extensions_reloading = False
        return await self.get_extensions(workspace_id)

    async def _apply_extensions(
        self, candidate: ExtensionConfig, env: dict[str, dict[str, str]], *, restore_only: bool,
    ) -> None:
        # Stage replacements without stopping the live catalog. Admission is
        # blocked by the caller; failed staging only releases newly owned clients.
        previous = self._langgraph_runtime
        previous_clients = self._extensions.clients
        staged: ExtensionCatalog | None = None
        runtime: LangGraphRuntime | None = None
        self._extension_error = ""
        try:
            await self._close_retired_extensions()
            staged = await self._prepare_extension_catalog(candidate, restore_only=restore_only)
            staged.env = env
            registry, clients = staged.registry, staged.clients
            if staged.skill_errors and not restore_only:
                raise ValueError("Skill 扫描有错误，未应用新配置：" + "; ".join(staged.skill_errors[:5]))
            unavailable = await staged.stage_connections(
                self._extensions, self._policy.workspace_root, self.observability, restore_only=restore_only,
            )
            runtime = await self._create_langgraph_runtime(registry, staged.skills)
            # This is last-applied product configuration, NOT execution state.
            # Unapplied edits must not silently replace a pending approval's server after restart.
            await self.repository.save_workspace_setting(
                "personal", "extensions.applied_config", candidate.model_dump()
            )
        except BaseException:
            self._extension_error = "扩展未能应用，保留原配置、聊天入口及已有连接；请检查失败服务后重试。"
            if runtime is not None:
                self._retired_runtimes.append(runtime)
            if staged is not None:
                self._retired_mcp_clients.update(
                    {
                        name: client for name, client in staged.clients.items()
                        if client is not previous_clients.get(name)
                    }
                )
            try:
                await self._close_retired_extensions()
            except Exception:
                self._extension_error += " 部分资源尚未完成清理，请重试或重启服务。"
            raise
        # No await between committing the prepared catalog and its execution entry.
        self._extensions, self._langgraph_runtime = staged, runtime
        self._applied_extension_revision = candidate.revision
        self._retired_mcp_clients.update({name: client for name, client in previous_clients.items()
                                         if client is not clients.get(name)})
        if unavailable:
            self._extension_error = "部分 MCP 尚未恢复，其他已连接服务仍可使用；请检查扩展状态与凭证。"
        if previous is not None:
            self._retired_runtimes.append(previous)
        try:
            await self._close_retired_extensions()
        except Exception:
            self._extension_error += " 新配置已生效，旧资源清理未完成；请重新应用或重启服务。"
        await self.repository.record_audit(
            "personal", "local-user", "extensions.restored" if restore_only else "extensions.applied",
            "extensions", "partial" if unavailable else "success",
            {"revision": candidate.revision, "unavailable_namespaces": unavailable},
        )
