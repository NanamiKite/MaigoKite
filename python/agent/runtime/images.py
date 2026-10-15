"""Build LangChain image input blocks from explicitly attached workspace files."""

from __future__ import annotations

from pathlib import Path

from anyio import to_thread
from langchain_core.messages import HumanMessage

from agent.media import ImageReference, image_data_url


async def image_message(text: str, images: tuple[ImageReference, ...], root: Path) -> HumanMessage:
    if not images:
        return HumanMessage(content=text)
    # The SDK converts these public multimodal blocks for each provider. Keeping
    # the image snapshot in graph messages preserves follow-up/resume semantics;
    # product messages and UI events retain only file references, never Base64.
    message = HumanMessage(
        content=[{"type": "text", "text": text}],
        additional_kwargs={"image_folder": images[0].folder_id, "image_bytes": sum(image.size for image in images)},
    )
    for reference in images:
        url = await to_thread.run_sync(image_data_url, root, reference)
        assert isinstance(message.content, list)
        message.content.append({"type": "image_url", "image_url": {"url": url}})
    return message
