"""Experimental OpenAI image adapter; returns remote URLs or inline image data.

Local image persistence is not implemented and is rejected before a request.
The adapter is not connected to the Core tool catalog.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from openai import AsyncOpenAI

from agent.models.image_provider import IImageProvider, ImageResult


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

_SIZE_MAP: dict[str, str] = {
    "256x256": "256x256",
    "512x512": "512x512",
    "1024x1024": "1024x1024",
    "1792x1024": "1792x1024",
    "1024x1792": "1024x1792",
}


def _resolve_size(size: str) -> str:
    """Normalise *size* through the known mapping.

    Falls back to ``"1024x1024"`` when the value is unrecognised.
    """
    return _SIZE_MAP.get(size, "1024x1024")


_DALL_E_MODEL_MAP: dict[str, str] = {
    "dall-e-2": "dall-e-2",
    "dall-e-3": "dall-e-3",
    "dall-e-3-hd": "dall-e-3",
}


def _resolve_dalle_model(model: str) -> str:
    """Normalise a DALL-E model name."""
    return _DALL_E_MODEL_MAP.get(model.lower(), "dall-e-3")


# ------------------------------------------------------------------
# Public adapter
# ------------------------------------------------------------------

class ImageAdapter(IImageProvider):
    """SDK-backed image-generation adapter.

    Currently delegates directly to the ``openai`` Python SDK which
    is the same library LangChain uses under the hood.  This avoids
    an extra LangChain layer while keeping the clean interface contract.

    Args:
        provider: Provider identifier (``"openai"`` or ``"dall-e"``).
        model: Model name such as ``"dall-e-3"`` or ``"dall-e-2"``.
        size: Output image size string.
        quality: Image quality preset (``"standard"`` or ``"hd"``).
        output_dir: Reserved for local saving; non-None values are currently rejected.

    Example::

        adapter = ImageAdapter(provider="dall-e", model="dall-e-3")
        result = await adapter.generate("a sunset over mountains")
    """

    def __init__(
        self,
        provider: str = "openai",
        model: str = "dall-e-3",
        size: str = "1024x1024",
        quality: str = "standard",
        output_dir: Optional[Path] = None,
        **extra: Any,
    ) -> None:
        if output_dir is not None:
            # Reject before a billable call instead of returning a path that was never written.
            raise NotImplementedError("Saving generated images is not implemented; omit output_dir")
        if provider.lower() not in {"openai", "dall-e"}:
            raise ValueError("Unsupported image provider; expected openai or dall-e")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Provider 'openai' requires the environment variable "
                "'OPENAI_API_KEY' to be set."
            )

        self.provider = provider.lower()
        self.model_name_str = model
        self.size = size
        self.quality = quality
        self.output_dir = output_dir
        self._extra = extra

        self._client = AsyncOpenAI(api_key=api_key)

    # ------------------------------------------------------------------
    # IImageProvider implementation
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        **kwargs: Any,
    ) -> ImageResult:
        """Generate an image from *prompt*.

        Args:
            prompt: Text description of the desired image.
            size: Output dimensions string (e.g. ``"1024x1024"``).
            quality: Quality preset (``"standard"`` or ``"hd"``).
            **kwargs: Extra parameters forwarded to the API call
                (e.g. ``n``, ``response_format``).

        Returns:
            An ``ImageResult`` containing the image data URL, optional
            local file path, and API response metadata.
        """
        resolved_size = _resolve_size(size or self.size)
        resolved_quality = kwargs.get("quality", quality or self.quality)
        n = int(kwargs.get("n", 1))

        # Resolve the actual DALL-E model.
        model = _resolve_dalle_model(self.model_name_str)

        api_kwargs: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": resolved_size,
            "n": n,
            "response_format": kwargs.get("response_format", "url"),
        }

        if model == "dall-e-3" and api_kwargs["response_format"] != "b64_json":
            api_kwargs["quality"] = resolved_quality
            api_kwargs["style"] = kwargs.get("style", "vivid")

        result_data = await self._client.images.generate(**api_kwargs)

        urls: list[str] = []
        metadata: dict[str, Any] = {}

        if hasattr(result_data, "data") and result_data.data:
            for item in result_data.data:
                url = getattr(item, "url", "") or ""
                b64 = getattr(item, "b64_json", "") or ""
                if url:
                    urls.append(url)
                if b64:
                    urls.append(f"data:image/png;base64,{b64}")

        # Metadata is always a single dict on the response object.
        if hasattr(result_data, "created"):
            metadata["created"] = result_data.created
        if hasattr(result_data, "model"):
            metadata["model"] = result_data.model

        if not urls:
            raise ValueError("Image provider returned no image data or URL")

        return ImageResult(
            data_url=urls[0],
            file_path=None,
            metadata=metadata,
        )

    @property
    def model_name(self) -> str:
        """Return the currently-selected image-generation model."""
        return self.model_name_str

    async def aclose(self) -> None:
        """Release the HTTP client owned by this adapter after generation finishes."""
        await self._client.close()
