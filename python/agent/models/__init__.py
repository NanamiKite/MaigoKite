"""Native chat model construction and optional embedding/image integrations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.models.embedding_adapter import EmbeddingAdapter
    from agent.models.image_adapter import ImageAdapter

__all__ = ["EmbeddingAdapter", "ImageAdapter"]


def __getattr__(name: str) -> type[EmbeddingAdapter] | type[ImageAdapter]:
    """Load provider integrations only when requested."""
    if name == "EmbeddingAdapter":
        from agent.models.embedding_adapter import EmbeddingAdapter
        return EmbeddingAdapter
    if name == "ImageAdapter":
        from agent.models.image_adapter import ImageAdapter
        return ImageAdapter
    raise AttributeError(name)
