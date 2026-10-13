"""IMemorySystem - Abstract interface for the unified memory system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from agent.data_contracts import MessageRecord

if TYPE_CHECKING:
    from agent.repository import SQLiteRepository


class IMemorySystem(ABC):
    """Abstract interface combining short-term, conversation, and user memory."""

    @abstractmethod
    async def save_short_term(self, key: str, value: Any) -> None:
        ...

    @abstractmethod
    async def retrieve_short_term(self, key: str) -> Any | None:
        ...

    @abstractmethod
    async def append_conversation(self, turn: dict[str, str]) -> None:
        ...

    @abstractmethod
    async def query_conversation(self, n: int) -> list[MessageRecord]:
        ...

    @abstractmethod
    async def save_user_preference(self, key: str, value: Any) -> None:
        ...

    @abstractmethod
    async def get_user_preference(self, key: str) -> Any | None:
        ...

    @abstractmethod
    async def clear(self) -> None:
        ...


class MemorySystem(IMemorySystem):
    """Phase-one memory facade: volatile scratch state plus SQLite history/profile."""

    def __init__(
        self, repository: SQLiteRepository, workspace_id: str = "personal", user_id: str = "local-user",
    ) -> None:
        from agent.memory.short_term import ShortTermMemory
        self.repository = repository
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.conversation_id: str | None = None
        self.short_term = ShortTermMemory()

    async def save_short_term(self, key: str, value: Any) -> None:
        await self.short_term.set(key, value)

    async def retrieve_short_term(self, key: str) -> Any | None:
        return await self.short_term.get(key)

    async def append_conversation(self, turn: dict[str, str]) -> None:
        if self.conversation_id is None:
            conversation = await self.repository.create_conversation(self.workspace_id, "Memory conversation")
            self.conversation_id = conversation["id"]
        await self.repository.append_message(self.conversation_id, turn["role"], turn["content"])

    async def query_conversation(self, n: int) -> list[MessageRecord]:
        if self.conversation_id is None:
            return []
        return await self.repository.list_messages(self.conversation_id, n)

    async def save_user_preference(self, key: str, value: Any) -> None:
        await self.repository.save_preference(self.workspace_id, self.user_id, key, value)

    async def get_user_preference(self, key: str) -> Any | None:
        return await self.repository.get_preference(self.workspace_id, self.user_id, key)

    async def clear(self) -> None:
        await self.short_term.clear()
