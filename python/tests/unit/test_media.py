from __future__ import annotations

import asyncio
import threading

import pytest
from agent.errors import UserFacingError
from agent.media import image_data_url, save_image
from agent.models.image_generation import ImageSettings, _download
from agent.repository import SQLiteRepository
from agent.tools.base import ExecutionContext
from agent.tools.native import image_generate
from agent.tools.native.image_generate import ImageGenerateTool
from pydantic import ValidationError

from scripts.acceptance.test_media_features import B64, PNG


@pytest.mark.parametrize("url", ["http://u:p@example.com/v1", "file:///tmp/x", "http://a/v1?key=secret",
                               "http://a/v1#fragment", "http://a/v1/images/generations"])
def test_image_settings_reject_non_root_or_credential_bearing_urls(url):
    with pytest.raises(ValidationError):
        ImageSettings(base_url=url)


def test_image_save_is_exclusive_and_input_reference_verifies_actual_bytes(tmp_path):
    image = save_image(tmp_path, "chat-images", "one", PNG, "")
    assert image_data_url(tmp_path, image) == f"data:image/png;base64,{B64}"
    with pytest.raises(FileExistsError):
        save_image(tmp_path, "chat-images", "one", PNG, "")
    (tmp_path / image.path).write_bytes(PNG + b"modified")
    with pytest.raises(UserFacingError, match="改变"):
        image_data_url(tmp_path, image)


@pytest.mark.asyncio
async def test_external_image_url_cannot_target_loopback_or_link_local():
    for url in ("http://127.0.0.1:9/x", "http://169.254.169.254/metadata", "file:///etc/passwd"):
        with pytest.raises(UserFacingError):
            await _download(url, "https://public-image-service.example/v1")


async def configured_tool(tmp_path):
    repository = SQLiteRepository(tmp_path / "image.sqlite")
    await repository.open()
    await repository.ensure_workspace("personal", "Personal", "personal")
    conversation = await repository.create_conversation("personal", "image")
    settings = ImageSettings(enabled=True, base_url="http://127.0.0.1:9/v1", model="fixture")
    await repository.save_image_settings("personal", settings.model_dump(mode="json"), tool_enabled=True,
                                         actor="local-user")

    async def no_secret(_workspace, _reference):
        raise AssertionError("a keyless local fixture must not query credentials")

    tool = ImageGenerateTool(repository, no_secret)
    context = ExecutionContext("trace", "task", "call", metadata={
        "workspace_id": "personal", "conversation_id": conversation["id"],
        "principal_id": "local-user", "workspace_root": str(tmp_path),
    })
    return repository, tool, context, {"prompt": "fixture", "configuration": settings.fingerprint()}


@pytest.mark.asyncio
async def test_dispatch_intent_survives_repository_reopen_without_second_request(tmp_path, monkeypatch):
    calls = []

    async def remote(*_args):
        calls.append("request")
        return PNG, "image/png"

    monkeypatch.setattr(image_generate, "generate_image", remote)
    repository, tool, context, arguments = await configured_tool(tmp_path)
    try:
        first = await tool.execute(context, arguments)
        assert first.error_code is None and len(calls) == 1
        await repository.close()
        await repository.open()
        second = await tool.execute(context, arguments)
        assert second.error_code == "IMAGE_GENERATION_FAILED" and len(calls) == 1
        assert "已经提交过" in second.summary
        assert len(list((tmp_path / "generated-images").glob("*.png"))) == 1
    finally:
        await repository.close()


@pytest.mark.asyncio
async def test_repeated_cancel_during_disk_write_drains_real_worker(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real_save = image_generate.save_image

    async def remote(*_args):
        return PNG, "image/png"

    def delayed_save(*args, **kwargs):
        entered.set()
        assert release.wait(10), "test must release the owned file writer"
        return real_save(*args, **kwargs)

    monkeypatch.setattr(image_generate, "generate_image", remote)
    monkeypatch.setattr(image_generate, "save_image", delayed_save)
    repository, tool, context, arguments = await configured_tool(tmp_path)
    task = asyncio.create_task(tool.execute(context, arguments))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done(), "cancellation must not confirm while the disk worker is still running"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        image = next((tmp_path / "generated-images").glob("*.png"))
        assert image.read_bytes() == PNG
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await repository.close()
