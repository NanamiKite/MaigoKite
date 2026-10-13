"""Reserved key/value storage contracts; product persistence uses Repository."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StorageBackend(ABC):
    """Abstract interface for a storage backend."""

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        ...

    @abstractmethod
    async def set(self, key: str, value: Any) -> None:
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        ...

    @abstractmethod
    async def keys(self, prefix: str = "") -> list[str]:
        ...

    @abstractmethod
    async def clear(self) -> None:
        ...


class FileStorage(StorageBackend):
    """Reserved filesystem backend; persistence and key mapping are not implemented."""

    async def get(self, key: str) -> Any | None:
        raise NotImplementedError("FileStorage.get is not implemented")

    async def set(self, key: str, value: Any) -> None:
        raise NotImplementedError("FileStorage.set is not implemented")

    async def delete(self, key: str) -> None:
        raise NotImplementedError("FileStorage.delete is not implemented")

    async def keys(self, prefix: str = "") -> list[str]:
        raise NotImplementedError("FileStorage.keys is not implemented")

    async def clear(self) -> None:
        raise NotImplementedError("FileStorage.clear is not implemented")


class MemoryStorage(StorageBackend):
    """Reserved in-memory backend; operations are not implemented yet."""

    async def get(self, key: str) -> Any | None:
        raise NotImplementedError("MemoryStorage.get is not implemented")

    async def set(self, key: str, value: Any) -> None:
        raise NotImplementedError("MemoryStorage.set is not implemented")

    async def delete(self, key: str) -> None:
        raise NotImplementedError("MemoryStorage.delete is not implemented")

    async def keys(self, prefix: str = "") -> list[str]:
        raise NotImplementedError("MemoryStorage.keys is not implemented")

    async def clear(self) -> None:
        raise NotImplementedError("MemoryStorage.clear is not implemented")
