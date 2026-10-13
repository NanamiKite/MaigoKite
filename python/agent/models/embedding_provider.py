"""IEmbeddingProvider - Abstract interface for embedding model providers."""

from __future__ import annotations

from abc import ABC, abstractmethod


class IEmbeddingProvider(ABC):
    """Interface for embedding model providers."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
        ...

    @abstractmethod
    async def embed_single(self, text: str) -> list[float]:
        """Generate an embedding for a single text."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensionality of the embedding vectors."""
        ...


class BGEProvider(IEmbeddingProvider):
    """Reserved local BGE provider; model loading is not implemented yet."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("BGEProvider.embed is not implemented")

    async def embed_single(self, text: str) -> list[float]:
        raise NotImplementedError("BGEProvider.embed_single is not implemented")

    @property
    def model_name(self) -> str:
        raise NotImplementedError("BGEProvider.model_name is not implemented")

    @property
    def dimension(self) -> int:
        raise NotImplementedError("BGEProvider.dimension is not implemented")


class OpenAIEmbeddingProvider(IEmbeddingProvider):
    """Reserved provider; use EmbeddingAdapter for the existing SDK integration."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("OpenAIEmbeddingProvider.embed is not implemented; use EmbeddingAdapter")

    async def embed_single(self, text: str) -> list[float]:
        raise NotImplementedError("OpenAIEmbeddingProvider.embed_single is not implemented; use EmbeddingAdapter")

    @property
    def model_name(self) -> str:
        raise NotImplementedError("OpenAIEmbeddingProvider.model_name is not implemented")

    @property
    def dimension(self) -> int:
        raise NotImplementedError("OpenAIEmbeddingProvider.dimension is not implemented")


class GTEProvider(IEmbeddingProvider):
    """Reserved GTE provider; model loading is not implemented yet."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("GTEProvider.embed is not implemented")

    async def embed_single(self, text: str) -> list[float]:
        raise NotImplementedError("GTEProvider.embed_single is not implemented")

    @property
    def model_name(self) -> str:
        raise NotImplementedError("GTEProvider.model_name is not implemented")

    @property
    def dimension(self) -> int:
        raise NotImplementedError("GTEProvider.dimension is not implemented")
