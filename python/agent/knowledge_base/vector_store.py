"""Experimental precomputed-vector adapters; not connected to the Core.

Document records use the LangChain SDK type. Scores retain the backend's native
meaning: FAISS/Chroma return distances, Qdrant returns similarity scores.
"""

from __future__ import annotations

import asyncio
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings


class IVectorStore(ABC):
    """Store caller-supplied vectors without loading another embedding model."""

    @abstractmethod
    async def add_vectors(
        self, documents: list[Document], embeddings: list[list[float]]
    ) -> list[str]:
        ...

    @abstractmethod
    async def search(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[tuple[Document, float]]:
        ...

    @abstractmethod
    async def delete(self, doc_ids: list[str]) -> None:
        ...

    @abstractmethod
    async def upsert(
        self, documents: list[Document], embeddings: list[list[float]]
    ) -> list[str]:
        ...


def _check_vector(vector: list[float], dimension: int | None = None) -> None:
    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError("Vectors must be non-empty and finite")
    if dimension is not None and len(vector) != dimension:
        raise ValueError("Vector dimension does not match the index")


def _batch_ids(
    documents: list[Document], embeddings: list[list[float]], dimension: int | None = None,
) -> list[str]:
    if len(documents) != len(embeddings):
        raise ValueError("Each document must have exactly one embedding")
    ids: list[str] = []
    for document, embedding in zip(documents, embeddings, strict=True):
        _check_vector(embedding, dimension)
        dimension = len(embedding)
        metadata_id = document.metadata.get("id")
        if metadata_id is not None and (not isinstance(metadata_id, str) or not metadata_id):
            raise ValueError("Document metadata id must be a non-empty string")
        if document.id and metadata_id and document.id != metadata_id:
            raise ValueError("Document id and metadata id must match")
        ids.append(document.id or metadata_id or str(uuid4()))
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate document ids in one batch")
    return ids


def _check_query(query_embedding: list[float], top_k: int, dimension: int | None = None) -> None:
    _check_vector(query_embedding, dimension)
    if top_k <= 0:
        raise ValueError("top_k must be positive")


class _PrecomputedEmbeddings(Embeddings):
    """FAISS requires an Embeddings object even when vectors are supplied by the caller."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("This index requires caller-supplied embeddings")

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError("This index requires a caller-supplied query vector")


class _FAISSStore(IVectorStore):
    """In-memory FAISS index with explicit save; loading saved indexes remains planned."""

    def __init__(self, index_path: str, dimension: int) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        try:
            import faiss
            from langchain_community.docstore.in_memory import InMemoryDocstore
            from langchain_community.vectorstores import FAISS
        except ImportError as exc:
            raise ImportError("FAISS requires the optional vectorstores dependencies") from exc

        self._dimension = dimension
        self._index_path = index_path
        # Keep native index operations serialized, including workers that outlive caller cancellation.
        self._lock = Lock()
        self._vectorstore = FAISS(
            embedding_function=_PrecomputedEmbeddings(),
            index=faiss.IndexFlatL2(dimension),
            docstore=InMemoryDocstore(),
            index_to_docstore_id={},
        )

    async def add_vectors(
        self, documents: list[Document], embeddings: list[list[float]]
    ) -> list[str]:
        ids = _batch_ids(documents, embeddings, self._dimension)
        if not ids:
            return []

        def add() -> list[str]:
            with self._lock:
                # Reject existing IDs before the SDK adds vectors to its native index.
                if self._vectorstore.get_by_ids(ids):
                    raise ValueError("Document id already exists; FAISS upsert is not implemented")
                return self._vectorstore.add_embeddings(
                    list(zip((doc.page_content for doc in documents), embeddings, strict=True)),
                    metadatas=[doc.metadata for doc in documents], ids=ids,
                )

        return await asyncio.to_thread(add)

    async def search(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[tuple[Document, float]]:
        _check_query(query_embedding, top_k, self._dimension)

        def search() -> list[tuple[Document, float]]:
            with self._lock:
                results = self._vectorstore.similarity_search_with_score_by_vector(query_embedding, k=top_k)
                return [(document.model_copy(deep=True), float(score)) for document, score in results]

        return await asyncio.to_thread(search)

    async def delete(self, doc_ids: list[str]) -> None:
        if doc_ids:
            def delete() -> None:
                with self._lock:
                    self._vectorstore.delete(ids=list(dict.fromkeys(doc_ids)))

            await asyncio.to_thread(delete)

    async def upsert(
        self, documents: list[Document], embeddings: list[list[float]]
    ) -> list[str]:
        # Delete-then-add would lose the old document if inserting the replacement failed.
        raise NotImplementedError("FAISS replacement/recovery is not implemented; use add_vectors for new documents")

    async def save(self) -> None:
        def save() -> None:
            with self._lock:
                self._vectorstore.save_local(self._index_path)

        await asyncio.to_thread(save)


class _ChromaStore(IVectorStore):
    """Chroma collection using its public precomputed-vector operations."""

    def __init__(self, collection_name: str, persist_directory: str | None = None) -> None:
        try:
            import chromadb
        except ImportError as exc:
            raise ImportError("Chroma requires the optional vectorstores dependencies") from exc

        self._client = (
            chromadb.PersistentClient(path=persist_directory)
            if persist_directory is not None else chromadb.EphemeralClient()
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name, embedding_function=None,
        )

    async def add_vectors(
        self, documents: list[Document], embeddings: list[list[float]]
    ) -> list[str]:
        ids = _batch_ids(documents, embeddings)
        if ids:
            vectors: list[Sequence[float] | Sequence[int]] = [*embeddings]
            await asyncio.to_thread(
                self._collection.add, ids=ids, embeddings=vectors,
                documents=[doc.page_content for doc in documents],
                metadatas=[{**doc.metadata, "id": doc_id} for doc, doc_id in zip(documents, ids, strict=True)],
            )
        return ids

    async def search(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[tuple[Document, float]]:
        _check_query(query_embedding, top_k)
        vectors: list[Sequence[float] | Sequence[int]] = [query_embedding]
        result = await asyncio.to_thread(
            self._collection.query, query_embeddings=vectors, n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        documents, metadatas, distances = result["documents"], result["metadatas"], result["distances"]
        if documents is None or metadatas is None or distances is None:
            raise ValueError("Chroma query omitted the requested document fields")
        return [
            (Document(id=doc_id, page_content=content, metadata=metadata or {}), float(distance))
            for doc_id, content, metadata, distance in zip(
                result["ids"][0], documents[0], metadatas[0], distances[0], strict=True,
            )
        ]

    async def delete(self, doc_ids: list[str]) -> None:
        if doc_ids:
            await asyncio.to_thread(self._collection.delete, ids=doc_ids)

    async def upsert(
        self, documents: list[Document], embeddings: list[list[float]]
    ) -> list[str]:
        ids = _batch_ids(documents, embeddings)
        if ids:
            vectors: list[Sequence[float] | Sequence[int]] = [*embeddings]
            await asyncio.to_thread(
                self._collection.upsert, ids=ids, embeddings=vectors,
                documents=[doc.page_content for doc in documents],
                metadatas=[{**doc.metadata, "id": doc_id} for doc, doc_id in zip(documents, ids, strict=True)],
            )
        return ids


class _QdrantStore(IVectorStore):
    """Async Qdrant adapter for an existing, explicitly provisioned dense collection.

    The caller owns collection provisioning (dimension and distance policy). IDs
    are UUID strings, matching Qdrant's native string-ID contract.
    """

    def __init__(
        self, collection_name: str, host: str = "localhost", port: int = 6333,
        *, embedding_dimension: int, **kwargs: Any,
    ) -> None:
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be positive")
        try:
            from qdrant_client import AsyncQdrantClient
        except ImportError as exc:
            raise ImportError("Qdrant requires the optional vectorstores dependencies") from exc

        self._collection_name = collection_name
        self._embedding_dimension = embedding_dimension
        self._client = AsyncQdrantClient(host=host, port=port, **kwargs)

    @staticmethod
    def _point_ids(doc_ids: list[str]) -> list[str]:
        try:
            return [str(UUID(doc_id)) for doc_id in doc_ids]
        except ValueError as exc:
            raise ValueError("Qdrant document ids must be UUID strings") from exc

    async def add_vectors(
        self, documents: list[Document], embeddings: list[list[float]]
    ) -> list[str]:
        """Qdrant inserts are upserts; an existing ID replaces the stored point."""
        return await self.upsert(documents, embeddings)

    async def search(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[tuple[Document, float]]:
        _check_query(query_embedding, top_k, self._embedding_dimension)
        result = await self._client.query_points(
            collection_name=self._collection_name, query=query_embedding,
            limit=top_k, with_payload=True, with_vectors=False,
        )
        output: list[tuple[Document, float]] = []
        for point in result.points:
            payload = point.payload or {}
            content = payload.get("page_content")
            metadata = payload.get("metadata")
            if not isinstance(content, str) or not isinstance(metadata, dict):
                raise ValueError("Qdrant point does not contain a document payload")
            output.append((
                Document(id=str(point.id), page_content=content, metadata=metadata), float(point.score),
            ))
        return output

    async def delete(self, doc_ids: list[str]) -> None:
        if doc_ids:
            await self._client.delete(
                collection_name=self._collection_name, points_selector=[*self._point_ids(doc_ids)], wait=True,
            )

    async def upsert(
        self, documents: list[Document], embeddings: list[list[float]]
    ) -> list[str]:
        from qdrant_client.models import PointStruct

        ids = self._point_ids(_batch_ids(documents, embeddings, self._embedding_dimension))
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate Qdrant UUIDs in one batch")
        if ids:
            await self._client.upsert(
                collection_name=self._collection_name,
                points=[
                    PointStruct(
                        id=doc_id, vector=embedding,
                        payload={"page_content": doc.page_content, "metadata": {**doc.metadata, "id": doc_id}},
                    )
                    for doc_id, doc, embedding in zip(ids, documents, embeddings, strict=True)
                ],
                wait=True,
            )
        return ids

    async def aclose(self) -> None:
        """Close the owned client after all calls using this store have completed."""
        await self._client.close()


def create_vector_store(provider: str, config: dict[str, Any] | None = None) -> IVectorStore:
    """Construct an optional backend without provisioning models or remote collections.

    FAISS requires dimension; Qdrant requires embedding_dimension and an existing
    collection. Its owner must call aclose() when finished.
    """
    config = dict(config or {})
    if provider == "faiss":
        if "dimension" not in config:
            raise ValueError("FAISS requires an explicit embedding dimension")
        return _FAISSStore(index_path=config.pop("index_path", "./faiss_index"), **config)
    if provider == "chroma":
        return _ChromaStore(collection_name=config.pop("collection_name", "default"), **config)
    if provider == "qdrant":
        if "embedding_dimension" not in config:
            raise ValueError("Qdrant requires an explicit embedding_dimension")
        return _QdrantStore(collection_name=config.pop("collection_name", "default"), **config)
    raise ValueError(f"Unknown vector-store provider: {provider!r}")
