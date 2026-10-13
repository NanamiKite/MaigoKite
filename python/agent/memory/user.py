"""Workspace-scoped persistent user preferences."""
from __future__ import annotations

from typing import Any

from agent.repository import SQLiteRepository


class UserMemory:
    def __init__(
        self, repository: SQLiteRepository, workspace_id: str = "personal", user_id: str = "local-user"
    ) -> None:
        self._repository = repository
        self._workspace_id = workspace_id
        self._user_id = user_id

    async def save_preference(self, category: str, key: str, value: Any) -> None:
        await self._repository.save_preference(self._workspace_id, self._user_id, f"{category}.{key}", value)

    async def get_preference(self, category: str, key: str) -> Any | None:
        return await self._repository.get_preference(self._workspace_id, self._user_id, f"{category}.{key}")
