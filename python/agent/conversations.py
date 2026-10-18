"""Conversation and workspace business rules, independent of HTTP and graph execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from agent.data_contracts import ConversationRecord, MessageRecord
from agent.errors import UserFacingError
from agent.models.runtime import ChatModelRuntime
from agent.repository import ConversationBusyError, SQLiteRepository, validate_subagent_model_ids
from agent.tools.permission_mgr import PermissionManager


class ConversationService:
    """Validate product edits while Repository owns their atomic persistence."""

    RESPONSE_MODES = {"fast", "balanced", "thorough"}

    def __init__(
        self,
        repository: SQLiteRepository,
        models: ChatModelRuntime,
        policy: PermissionManager,
        *,
        require_inactive: Callable[[str], None],
        delete_threads: Callable[[Sequence[str]], Awaitable[None]],
        list_jobs: Callable[[str, str], Awaitable[list[dict[str, Any]]]],
    ) -> None:
        self.repository = repository
        self.models = models
        self._policy = policy
        self._require_inactive_conversation = require_inactive
        self._delete_threads = delete_threads
        self._list_jobs = list_jobs

    async def get_permission_settings(self, workspace_id: str) -> dict[str, Any]:
        stored = await self.repository.get_workspace_setting(workspace_id, "permissions.policy")
        if not isinstance(stored, dict):
            return {"default_mode": self._policy.mode}
        try:
            mode = self._policy.normalize_mode(str(stored.get("default_mode", self._policy.mode)))
        except ValueError:
            mode = self._policy.mode
        return {"default_mode": mode}

    async def update_permission_settings(self, workspace_id: str, default_mode: str) -> dict[str, Any]:
        try:
            default_mode = self._policy.normalize_mode(default_mode)
        except ValueError as exc:
            raise UserFacingError("无效的权限模式。", code="INVALID_PERMISSION_MODE") from exc
        value = {"default_mode": default_mode}
        await self.repository.save_workspace_setting(workspace_id, "permissions.policy", value)
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
            raise UserFacingError("无效的权限模式。", code="INVALID_PERMISSION_MODE") from exc
        if response_mode not in self.RESPONSE_MODES:
            raise UserFacingError("无效的响应模式。", code="INVALID_RESPONSE_MODE")
        if model_id:
            await self.models.get_profile(workspace_id, model_id)
        else:
            _, model_id = await self.models.list_settings(workspace_id)
        await self.models.validate_thinking(workspace_id, model_id, thinking)
        for child_model_id in validate_subagent_model_ids([] if subagent_model_ids is None else subagent_model_ids):
            await self.models.get_profile(workspace_id, child_model_id)
        return await self.repository.create_conversation(
            workspace_id,
            title,
            model_id,
            mode,
            response_mode,
            folder_id,
            max_subagent_calls,
            max_concurrent_subagents,
            subagent_model_ids,
            thinking=thinking,
            memory_enabled=memory_enabled,
        )

    async def update_conversation_permission(
        self, workspace_id: str, conversation_id: str, permission_mode: str
    ) -> ConversationRecord:
        try:
            permission_mode = self._policy.normalize_mode(permission_mode)
        except ValueError as exc:
            raise UserFacingError("无效的权限模式。", code="INVALID_PERMISSION_MODE") from exc
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
                raise UserFacingError(
                    "对话名称须为 1–200 个字符。",
                    code="CONVERSATION_TITLE_LENGTH",
                    params={"min": 1, "max": 200},
                )
        if permission_mode is not None:
            try:
                permission_mode = self._policy.normalize_mode(permission_mode)
            except ValueError as exc:
                raise UserFacingError("无效的权限模式。", code="INVALID_PERMISSION_MODE") from exc
        if response_mode is not None and response_mode not in self.RESPONSE_MODES:
            raise UserFacingError("无效的响应模式。", code="INVALID_RESPONSE_MODE")
        if model_id is not None and model_id:
            await self.models.get_profile(workspace_id, model_id)
        if model_id is not None or thinking is not None:
            self._require_inactive_conversation(conversation_id)
            current = await self.repository.get_conversation(workspace_id, conversation_id)
            if thinking is None and model_id != current.get("model_id"):
                thinking = ""  # Never carry a provider-specific level across models.
            await self.models.validate_thinking(
                workspace_id,
                model_id if model_id is not None else str(current.get("model_id") or ""),
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

    async def delete_conversation(self, workspace_id: str, conversation_id: str) -> None:
        self._require_inactive_conversation(conversation_id)
        task_ids = await self.repository.delete_conversation(workspace_id, conversation_id)
        await self._delete_threads(task_ids)

    async def fork_conversation(
        self,
        workspace_id: str,
        conversation_id: str,
        message_id: str,
        request_key: str,
    ) -> ConversationRecord:
        self._require_inactive_conversation(conversation_id)
        if len(message_id) > 200:
            raise UserFacingError("来源消息标识无效。", code="INVALID_SOURCE_MESSAGE")
        return await self.repository.fork_conversation(workspace_id, conversation_id, message_id, request_key)

    async def get_workspace_folder(self, workspace_id: str, folder_id: str) -> dict[str, Any]:
        if not folder_id:
            return {"id": "", "name": "默认工作区", "root_path": str(self._policy.workspace_root)}
        item = await self.repository.get_workspace_folder(workspace_id, folder_id)
        root = Path(item["root_path"])
        if not root.is_dir() or str(root.resolve(strict=True)) != item["root_path"]:
            raise UserFacingError(
                "工作区文件夹不存在或路径已变更，请重新选择文件夹。",
                code="WORKSPACE_FOLDER_CHANGED",
            )
        return item

    async def list_workspace_folders(self, workspace_id: str) -> list[dict[str, Any]]:
        return [
            await self.get_workspace_folder(workspace_id, ""),
            *await self.repository.list_workspace_folders(workspace_id),
        ]

    async def create_workspace_folder(self, workspace_id: str, root_path: str, name: str = "") -> dict[str, Any]:
        if not root_path.strip() or len(root_path) > 4096 or "\0" in root_path:
            raise UserFacingError("请选择有效的文件夹。", code="WORKSPACE_FOLDER_INVALID")
        root = Path(root_path).expanduser()
        if not root.is_absolute():
            raise UserFacingError("工作区文件夹须使用绝对路径。", code="WORKSPACE_FOLDER_ABSOLUTE")
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise UserFacingError("工作区路径不是文件夹。", code="WORKSPACE_NOT_DIRECTORY")
        self._policy.for_workspace(root)  # Preserve the existing filesystem-root boundary.
        if root == self._policy.workspace_root:
            return await self.get_workspace_folder(workspace_id, "")
        name = name.strip() or root.name
        if len(name) > 120:
            raise UserFacingError(
                "工作区名称不能超过 120 个字符。",
                code="WORKSPACE_NAME_LENGTH",
                params={"max": 120},
            )
        return await self.repository.create_workspace_folder(workspace_id, name, str(root))

    async def delete_workspace_folder(
        self,
        workspace_id: str,
        folder_id: str,
        principal_id: str = "local-user",
    ) -> None:
        if await self.repository.check_workspace_access(workspace_id, principal_id) != "owner":
            raise UserFacingError(
                "只有工作区所有者可以删除工作区。",
                code="WORKSPACE_OWNER_REQUIRED",
                status_code=403,
            )
        if not folder_id:
            raise UserFacingError("默认工作区不能删除。", code="DEFAULT_WORKSPACE_PROTECTED")
        # Read the registration, not the directory: a missing disk folder must
        # remain removable from the app. Terminal projections may still be cleaning up.
        await self.repository.get_workspace_folder(workspace_id, folder_id)
        for conversation in await self.repository.list_conversations(workspace_id):
            if conversation.get("folder_id") == folder_id:
                if any(job["live"] for job in await self._list_jobs(workspace_id, str(conversation["id"]))):
                    raise ConversationBusyError(
                        "工作区中有运行中的进程，请停止后再删除。",
                        code="WORKSPACE_PROCESS_ACTIVE",
                    )
                try:
                    self._require_inactive_conversation(str(conversation["id"]))
                except ConversationBusyError:
                    raise ConversationBusyError(
                        "工作区中有未结束的任务，请完成或停止后再删除。",
                        code="WORKSPACE_TASK_ACTIVE",
                    ) from None
        await self.repository.delete_workspace_folder(workspace_id, folder_id, principal_id)

    async def list_messages(self, conversation_id: str, workspace_id: str = "personal") -> list[MessageRecord]:
        await self.repository.get_conversation(workspace_id, conversation_id)
        return await self.repository.list_messages(conversation_id)

    async def folder_root(self, workspace_id: str, folder_id: str) -> str:
        folder = await self.get_workspace_folder(workspace_id, folder_id)
        return str(folder["root_path"])
