"""User-managed folder notes; request-time injection remains in runtime.memory."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from agent.repository import SQLiteRepository, folder_memory_scope


class FolderMemories:
    """Validate saved notes and bind settings to a currently resolved workspace folder."""

    MEMORY_CATEGORIES = {"preference", "fact", "constraint", "project", "other"}
    MEMORY_SECRET_PATTERNS = (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
        re.compile(r"\b(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*\S+", re.IGNORECASE),
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    )

    def __init__(
        self,
        repository: SQLiteRepository,
        folder_root: Callable[[str, str], Awaitable[str]],
    ) -> None:
        self.repository = repository
        self._folder_root = folder_root

    async def list_memories(
        self,
        workspace_id: str,
        user_id: str,
        query: str = "",
        limit: int = 100,
        folder_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if len(query) > 200:
            raise ValueError("memory search query must not exceed 200 characters")
        scope = await self._scope(workspace_id, folder_id)
        return await self.repository.list_user_memories(workspace_id, user_id, query, limit, folder_scope=scope)

    async def _scope(self, workspace_id: str, folder_id: str | None) -> str | None:
        if folder_id is None:
            return None
        root = await self._folder_root(workspace_id, folder_id)
        return folder_memory_scope(folder_id, root)

    async def get_memory_settings(self, workspace_id: str, user_id: str, folder_id: str) -> dict[str, Any]:
        scope = await self._scope(workspace_id, folder_id)
        enabled = await self.repository.get_preference(workspace_id, user_id, "memory.enabled." + str(scope))
        return {"folder_id": folder_id, "enabled": enabled is True}

    async def set_memory_settings(
        self, workspace_id: str, user_id: str, folder_id: str, enabled: bool
    ) -> dict[str, Any]:
        scope = await self._scope(workspace_id, folder_id)
        await self.repository.set_memory_enabled(workspace_id, user_id, str(scope), enabled)
        return {"folder_id": folder_id, "enabled": enabled}

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
        category = category.strip().lower()
        normalized_content = " ".join(content.split())
        if category not in self.MEMORY_CATEGORIES:
            raise ValueError("invalid memory category")
        if not normalized_content:
            raise ValueError("memory content is required")
        if len(normalized_content) > 4000:
            raise ValueError("memory content must not exceed 4000 characters")
        if any(pattern.search(normalized_content) for pattern in self.MEMORY_SECRET_PATTERNS):
            raise ValueError("memory content must not contain credentials or private keys")
        if not 0 <= confidence <= 1:
            raise ValueError("memory confidence must be between 0 and 1")
        item = await self.repository.upsert_user_memory(
            workspace_id,
            user_id,
            memory_id=memory_id,
            category=category,
            content=normalized_content,
            source_type="user",
            source_id=user_id,
            confidence=confidence,
            folder_scope=await self._scope(workspace_id, folder_id),
        )
        return item

    async def delete_memory(self, workspace_id: str, user_id: str, memory_id: str) -> None:
        await self.repository.delete_user_memory(workspace_id, user_id, memory_id)
