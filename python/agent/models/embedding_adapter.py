"""EmbeddingAdapter – LangChain-backed implementation of IEmbeddingProvider.

Wraps LangChain embedding classes behind the stable ``IEmbeddingProvider``
contract so the rest of the Agent Core remains provider-agnostic.

Supported providers:
    * ``openai`` – :class:`langchain_openai.OpenAIEmbeddings`
    * ``bge`` – local sentence-transformers embeddings (BGE-M3)
    * ``gte`` – GTE embedding models via sentence-transformers
"""

from __future__ import annotations

import os
from typing import Any

from langchain_openai import OpenAIEmbeddings
from sentence_transformers import SentenceTransformerEmbeddings

from agent.models.embedding_provider import IEmbeddingProvider

# ------------------------------------------------------------------
# Private dimension lookup tables
# ------------------------------------------------------------------

_BGE_DIMENSIONS: dict[str, int] = {
    "bge-large-en": 1024,
    "bge-large-zh": 1024,
    "bge-m3": 1024,
}

_GTE_DIMENSIONS: dict[str, int] = {
    "gte-large": 1024,
    "gte-base": 768,
}

_OPENAI_DIMENSIONS: dict[str, int] = {
    "text-embedding-ada-002": 1536,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}


def _resolve_dimension(provider: str, model: str, dimensions: dict[str, int]) -> int:
    """Return the embedding dimension for *provider* / *model*.

    Falls back to 1024 when the model name is unknown.
    """
    return dimensions.get(model, 1024)


class EmbeddingAdapter(IEmbeddingProvider):
    """LangChain-backed embedding adapter.

    Args:
        provider: Provider identifier (``"openai"``, ``"bge"``, ``"gte"``).
        model: Model name passed to the underlying embedding class.
        embed_batch_size: Maximum number of texts sent per embedding batch.

    Example::

        adapter = EmbeddingAdapter(provider="bge", model="bge-m3")
        vectors = await adapter.embed(["hello world"])
    """

    def __init__(
        self,
        provider: str = "bge",
        model: str = "bge-m3",
        embed_batch_size: int = 64,
        **extra: Any,
    ) -> None:
        self.provider = provider.lower()
        self.model = model
        self.embed_batch_size = embed_batch_size
        self._extra = extra

        if self.provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "Provider 'openai' requires the environment variable "
                    "'OPENAI_API_KEY' to be set."
                )
            kwargs: dict[str, Any] = {
                "model": model,
                "chunk_size": embed_batch_size,
            }
            kwargs.update({k: v for k, v in extra.items()})
            self._embeddings = OpenAIEmbeddings(**kwargs)
        elif self.provider in ("bge", "gte"):
            kwargs = {
                "model_name": model,
                "model_kwargs": extra.get("model_kwargs", {}),
            }
            kwargs.update({k: v for k, v in extra.items()})
            self._embeddings = SentenceTransformerEmbeddings(**kwargs)
        else:
            raise ValueError(
                f"Unsupported embedding provider '{self.provider}'. "
                f"Supported: openai, bge, gte"
            )

    # ------------------------------------------------------------------
    # IEmbeddingProvider implementation
    # ------------------------------------------------------------------

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors corresponding to *texts*.
        """
        return self._embeddings.embed_documents(texts)

    async def embed_single(self, text: str) -> list[float]:
        """Generate an embedding for a single text.

        Args:
            text: Text string to embed.

        Returns:
            Embedding vector.
        """
        return self._embeddings.embed_query(text)

    @property
    def model_name(self) -> str:
        """Return the currently-selected model name."""
        return self.model

    @property
    def dimension(self) -> int:
        """Return the dimensionality of the embedding vectors."""
        if self.provider == "openai":
            return _resolve_dimension("openai", self.model, _OPENAI_DIMENSIONS)
        if self.provider == "gte":
            return _resolve_dimension("gte", self.model, _GTE_DIMENSIONS)
        # Default to BGE dimensions.
        return _resolve_dimension("bge", self.model, _BGE_DIMENSIONS)
