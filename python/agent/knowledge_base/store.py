"""Document storage using LangChain records, independent of the chat Repository."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any
from uuid import uuid4

from langchain_core.documents import Document as LCDocument


class IDocumentStore(ABC):
    """Interface for storing and retrieving documents."""

    @abstractmethod
    async def add(self, document: LCDocument) -> str:
        ...

    @abstractmethod
    async def add_many(self, documents: list[LCDocument]) -> list[str]:
        ...

    @abstractmethod
    async def get(self, doc_id: str) -> LCDocument | None:
        ...

    @abstractmethod
    async def delete(self, doc_id: str) -> None:
        ...

    @abstractmethod
    async def update(
        self, doc_id: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        ...

    @abstractmethod
    async def count(self) -> int:
        ...


class InMemoryDocumentStore(IDocumentStore):
    """A simple in-memory implementation of ``IDocumentStore``.

    Documents are stored using ``langchain_core.documents.Document`` under the hood.
    Useful for prototyping or when an external vector-store backend is not needed.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._docs: dict[str, LCDocument] = {}

    async def add(self, document: LCDocument) -> str:
        """Insert a document; duplicate explicit IDs must be changed via update()."""
        return (await self.add_many([document]))[0]

    async def add_many(self, documents: list[LCDocument]) -> list[str]:
        """Add multiple documents at once.

        Args:
            documents: List of LangChain ``Document`` instances.

        Returns:
            List of assigned document IDs.
        """
        async with self._lock:
            pending: dict[str, LCDocument] = {}
            for doc in documents:
                metadata_id = doc.metadata.get("id")
                if metadata_id is not None and (not isinstance(metadata_id, str) or not metadata_id):
                    raise ValueError("Document metadata id must be a non-empty string")
                if doc.id and metadata_id and doc.id != metadata_id:
                    raise ValueError("Document id and metadata id must match")
                # UUIDs do not reuse an existing key after an earlier document is deleted.
                doc_id = doc.id or metadata_id or str(uuid4())
                if doc_id in self._docs or doc_id in pending:
                    raise ValueError("Document id already exists; use update to replace its content")
                stored = doc.model_copy(deep=True)
                stored.id = doc_id
                stored.metadata["id"] = doc_id
                pending[doc_id] = stored
            # Validate the whole batch before changing the store.
            self._docs.update(pending)
            return list(pending)

    async def get(self, doc_id: str) -> LCDocument | None:
        """Retrieve a single document by ID.

        Args:
            doc_id: The document identifier.

        Returns:
            The ``LCDocument`` if found, otherwise ``None``.
        """
        async with self._lock:
            document = self._docs.get(doc_id)
            return document.model_copy(deep=True) if document is not None else None

    async def delete(self, doc_id: str) -> None:
        """Delete a document by ID.

        Args:
            doc_id: The document identifier to remove.
        """
        async with self._lock:
            self._docs.pop(doc_id, None)

    async def update(
        self, doc_id: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Update the content and optional metadata of an existing document.

        Args:
            doc_id: Identifier of the document to update.
            content: New page content.
            metadata: New metadata dictionary (merged with existing).
        """
        async with self._lock:
            doc = self._docs.get(doc_id)
            if doc is None:
                raise KeyError(doc_id)
            if metadata is not None and "id" in metadata and metadata["id"] != doc_id:
                raise ValueError("Updating content must not change the document id")
            new_metadata = {**(doc.metadata or {}), **(metadata or {})}
            self._docs[doc_id] = LCDocument(
                id=doc_id,
                page_content=content,
                metadata=new_metadata,
            ).model_copy(deep=True)

    async def count(self) -> int:
        """Return the total number of stored documents.

        Returns:
            Integer count of documents.
        """
        async with self._lock:
            return len(self._docs)
