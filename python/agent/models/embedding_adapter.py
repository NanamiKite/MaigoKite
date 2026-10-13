"""Optional embedding adapter using LangChain's public async Embeddings contract.

Local model loading remains planned. A caller can supply an already configured
SDK backend without making the chat installation depend on a local ML stack.
"""

from __future__ import annotations

import math
from typing import Any

from langchain_core.embeddings import Embeddings

from agent.models.embedding_provider import IEmbeddingProvider


class EmbeddingAdapter(IEmbeddingProvider):
    """Adapt an SDK backend; dimensions come from configuration or actual vectors.

    An injected backend is owned by its caller. BGE/GTE need such a backend until
    local loading and its dependency/lifecycle policy are implemented.
    """

    def __init__(
        self,
        provider: str = "bge",
        model: str = "bge-m3",
        embed_batch_size: int = 64,
        *,
        backend: Embeddings | None = None,
        dimension: int | None = None,
        **extra: Any,
    ) -> None:
        self.provider = provider.lower()
        if self.provider not in {"openai", "bge", "gte"}:
            raise ValueError("Unsupported embedding provider; expected openai, bge or gte")
        if embed_batch_size <= 0:
            raise ValueError("embed_batch_size must be positive")
        configured_dimension = dimension if dimension is not None else extra.get("dimensions")
        if configured_dimension is not None and (
            type(configured_dimension) is not int or configured_dimension <= 0
        ):
            raise ValueError("Embedding dimension must be a positive integer")
        self.model = model
        self.embed_batch_size = embed_batch_size
        self._dimension: int | None = configured_dimension

        if backend is not None:
            if extra:
                raise ValueError("Configure an injected embedding backend directly; extra options would be ignored")
            self._embeddings = backend
        elif self.provider == "openai":
            from langchain_openai import OpenAIEmbeddings

            # The SDK accepts an explicit key or its documented environment setting.
            self._embeddings = OpenAIEmbeddings(model=model, chunk_size=embed_batch_size, **extra)
        else:
            raise NotImplementedError(
                "Local BGE/GTE model loading is not implemented; supply a configured LangChain Embeddings backend"
            )

    def _check_vectors(self, vectors: list[list[float]], expected_count: int) -> None:
        if len(vectors) != expected_count:
            raise ValueError("Embedding backend returned an unexpected number of vectors")
        if not vectors:
            return
        dimension = len(vectors[0])
        if not dimension or any(
            len(vector) != dimension or any(not math.isfinite(value) for value in vector)
            for vector in vectors
        ):
            raise ValueError("Embedding backend returned empty, inconsistent or non-finite vectors")
        if self._dimension is not None and dimension != self._dimension:
            raise ValueError("Embedding dimension does not match the configured or previously observed dimension")
        self._dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = await self._embeddings.aembed_documents(texts)
        self._check_vectors(vectors, len(texts))
        return vectors

    async def embed_single(self, text: str) -> list[float]:
        vector = await self._embeddings.aembed_query(text)
        self._check_vectors([vector], 1)
        return vector

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError("Embedding dimension is unknown; configure it or generate an embedding first")
        return self._dimension
