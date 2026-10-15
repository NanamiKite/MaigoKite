"""Bound raster previews and recognize bytes without trusting a filename or remote MIME header."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.errors import UserFacingError
from agent.security.workspace_fs import create_entry, parent_directory, read_file

IMAGE_LIMIT = 16 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}


class ImageReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["image"] = "image"
    path: str = Field(min_length=1, max_length=4096)
    folder_id: str = Field(default="", max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"]
    size: int = Field(gt=0, le=IMAGE_LIMIT)


def prepare_image_directory(root: Path, directory: str) -> None:
    with contextlib.suppress(FileExistsError):
        create_entry(root, ".", directory, "directory")
    with parent_directory(root, directory):
        pass


def save_image(root: Path, directory: str, identifier: str, data: bytes, folder_id: str) -> ImageReference:
    mime = image_mime(data)
    if not mime or not data or len(data) > IMAGE_LIMIT:
        raise UserFacingError("图片格式不受支持，或超过 16 MiB。")
    prepare_image_directory(root, directory)
    name = identifier + IMAGE_EXTENSIONS[mime]
    with parent_directory(root, directory) as parent:
        if isinstance(parent, Path):
            descriptor = os.open(
                parent / name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
        else:
            descriptor = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent
            )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            if isinstance(parent, Path):
                (parent / name).unlink(missing_ok=True)
            else:
                os.unlink(name, dir_fd=parent)
            raise
    return ImageReference(
        path=f"{directory}/{name}",
        folder_id=folder_id,
        mime_type=mime,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def image_data_url(root: Path, reference: ImageReference) -> str:
    relative = Path(reference.path)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise UserFacingError("图片必须位于当前工作区内。")
    data, _ = read_file(root, reference.path, IMAGE_LIMIT)
    if hashlib.sha256(data).hexdigest() != reference.sha256 or len(data) != reference.size:
        raise UserFacingError("对话中的图片已改变，请重新添加图片后发送。")
    mime = image_mime(data)
    if not mime or mime != reference.mime_type or mime == "image/bmp":
        raise UserFacingError("模型图片输入支持 PNG、JPEG、WebP 和 GIF。")
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def image_mime(data: bytes) -> Literal["", "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return ""
