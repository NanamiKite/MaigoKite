"""Generate one image through the configured service and save a workspace artifact."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Any

from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field

from agent.errors import UserFacingError, public_error
from agent.media import prepare_image_directory, save_image
from agent.models.image_generation import IMAGE_SETTING_KEY, ImageSettings, generate_image
from agent.repository import SQLiteRepository
from agent.tools.base import BaseTool, ExecutionContext, ToolResult

logger = logging.getLogger(__name__)


class ImageGenerateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=16000, description="Description of the single image to generate.")
    configuration: str = Field(default="", description="Managed by the application; leave empty.")


class ImageGenerateTool(BaseTool):
    name = "image_generate"
    description = (
        "Generate one image from a text prompt using the user's configured image service. "
        "May incur service charges and requires approval. Saves a new file in generated-images/ "
        "inside the current workspace and returns its path; the UI displays the image automatically. "
        "Do not use this to inspect existing images. Never retry an uncertain or cancelled generation automatically."
    )

    def __init__(
        self,
        repository: SQLiteRepository,
        credential: Callable[[str, str], Awaitable[str]],
    ) -> None:
        self.repository = repository
        self.credential = credential

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return ImageGenerateArgs.model_json_schema()

    async def execute(self, context: ExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            args = ImageGenerateArgs.model_validate(arguments)
            workspace_id = context.metadata["workspace_id"]
            settings = ImageSettings.model_validate(
                await self.repository.get_workspace_setting(workspace_id, IMAGE_SETTING_KEY) or {}
            )
            if not settings.enabled or not settings.model:
                raise UserFacingError("请先在“设置 → 工具 → 图片生成”中配置并启用生图服务。")
            if args.configuration != settings.fingerprint():
                raise UserFacingError("生图配置已改变，请重新发起生图请求；尚未调用服务。")
            key = (
                await self.credential(workspace_id, settings.credential_reference())
                if settings.credential_required
                else ""
            )
            if settings.credential_required and not key:
                raise UserFacingError("生图服务密钥不可用，请重新保存密钥。")
            current = ImageSettings.model_validate(
                await self.repository.get_workspace_setting(workspace_id, IMAGE_SETTING_KEY) or {}
            )
            if current.fingerprint() != args.configuration:
                raise UserFacingError("生图配置已改变，请重新发起请求；尚未调用服务。")
            root = Path(context.metadata["workspace_root"])
            conversation = await self.repository.get_conversation(workspace_id, context.metadata["conversation_id"])
            folder_id = conversation["folder_id"]
            # Check the output directory before a billable request; exclusive
            # creation below still checks it again against path replacement.
            await to_thread.run_sync(prepare_image_directory, root, "generated-images")
            if not await self.repository.claim_image_request(
                workspace_id,
                context.metadata["principal_id"],
                context.step_id,
            ):
                raise UserFacingError(
                    "此生图调用已经提交过或结果不确定，未再次调用服务。"
                    "请先检查 generated-images 目录；如仍需生成，请明确发起新的生图请求。"
                )
            data, _mime = await generate_image(settings, args.prompt, key)
            identifier = hashlib.sha256(f"{context.task_id}:{context.step_id}".encode()).hexdigest()[:32]
            worker = asyncio.create_task(
                to_thread.run_sync(
                    partial(save_image, root, "generated-images", identifier, data, folder_id),
                )
            )
            try:
                image = await asyncio.shield(worker)
            except asyncio.CancelledError:
                # A bounded local write cannot be killed midway; drain it before
                # Core confirms cancellation. Repeated cancellation and write
                # errors must not replace the caller's original cancel signal.
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not worker.cancelled() and (error := worker.exception()) is not None:
                    logger.warning("Image write failed during cancellation: %s", type(error).__name__)
                raise
            return ToolResult(
                data={"images": [image.model_dump()], "path": image.path, "model": settings.model},
                summary=f"图片已保存：{image.path}",
                metadata={
                    "file_change": {
                        "path": image.path,
                        "diff": "",
                        "diff_truncated": True,
                        "added": None,
                        "removed": None,
                        "before_hash": hashlib.sha256(b"").hexdigest(),
                        "after_hash": image.sha256,
                    }
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = public_error(exc)
            return ToolResult.failure(
                {},
                message,
                error_code="IMAGE_GENERATION_FAILED",
                user_hint=message + " 请求发出后的失败不保证服务端已停止或未计费，请勿自动重试。",
                action={"label": "配置生图服务", "to": "/settings?tab=tools&field=image-generation"},
            )
