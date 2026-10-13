"""IImageProvider - Abstract interface for image generation providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ImageResult:
    """Result from an image generation call."""

    data_url: str
    file_path: Path | None
    metadata: dict[str, Any]

    def __init__(
        self,
        data_url: str,
        file_path: Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.data_url = data_url
        self.file_path = file_path
        self.metadata = dict(metadata) if metadata is not None else {}


class IImageProvider(ABC):
    """Interface for image generation providers."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        **kwargs: Any,
    ) -> ImageResult:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...
