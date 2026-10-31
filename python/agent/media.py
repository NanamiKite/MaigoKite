"""Bound raster previews and recognize bytes without trusting a filename or remote MIME header."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from agent.documents import DocumentFormat, document_format
from agent.errors import UserFacingError
from agent.security.sensitive_paths import is_sensitive_path
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


class TextReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"] = "text"
    path: str = Field(min_length=1, max_length=4096)
    folder_id: str = Field(default="", max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: Literal["text/plain"] = "text/plain"
    size: int = Field(ge=0, le=IMAGE_LIMIT)
    name: str = Field(min_length=1, max_length=255)


class DocumentReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["document"] = "document"
    path: str = Field(min_length=1, max_length=4096)
    folder_id: str = Field(default="", max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=IMAGE_LIMIT)
    name: str = Field(min_length=1, max_length=255)
    format: DocumentFormat


type AttachmentReference = ImageReference | TextReference | DocumentReference
ATTACHMENT_REFERENCE: TypeAdapter[AttachmentReference] = TypeAdapter(AttachmentReference)


def attachment_bytes(root: Path, reference: AttachmentReference) -> bytes:
    relative = Path(reference.path)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise UserFacingError("附件必须位于当前工作区内。", code="ATTACHMENT_SCOPE_INVALID")
    if is_sensitive_path(root / relative):
        raise UserFacingError("此文件不能作为附件读取。", code="ATTACHMENT_ACCESS_DENIED")
    data, _ = read_file(root, reference.path, IMAGE_LIMIT)
    if hashlib.sha256(data).hexdigest() != reference.sha256 or len(data) != reference.size:
        raise UserFacingError("附件内容已改变，请重新添加后发送。", code="ATTACHMENT_CHANGED")
    if isinstance(reference, TextReference):
        _validate_text_attachment(data)
    if isinstance(reference, DocumentReference) and document_format(data) != reference.format:
        raise UserFacingError("请选择 PDF、DOCX、XLSX 或 PPTX 文档。", code="DOCUMENT_UNSUPPORTED")
    return data


def _validate_text_attachment(data: bytes) -> None:
    """Validate text at upload and reference boundaries, including caller-supplied references."""
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UserFacingError(
            "请选择 UTF-8 文本、PDF、DOCX、XLSX、PPTX，或 PNG、JPEG、WebP、GIF 图片。",
            code="ATTACHMENT_FORMAT_UNSUPPORTED",
        ) from exc
    if re.search(rb"[\x00-\x08\x0b\x0e-\x1f]", data):
        raise UserFacingError("暂不支持此文件格式。", code="ATTACHMENT_FORMAT_UNSUPPORTED")


def save_attachment(root: Path, name: str, data: bytes, folder_id: str, identifier: str) -> AttachmentReference:
    if len(data) > IMAGE_LIMIT:
        raise UserFacingError("文件不能超过 16 MiB。", code="ATTACHMENT_TOO_LARGE", status_code=413)
    if image_mime(data) in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return save_image(root, "chat-images", identifier, data, folder_id)
    format = document_format(data)
    if format is not None:
        filename = f"{identifier}.{format}"
        _save_bytes(root, "chat-files", filename, data)
        return DocumentReference(
            path=f"chat-files/{filename}", folder_id=folder_id, name=name, format=format,
            size=len(data), sha256=hashlib.sha256(data).hexdigest(),
        )
    _validate_text_attachment(data)
    # Keep the exact bytes and a separate display name; filenames never choose the storage path.
    suffix = Path(name).suffix.lower()
    filename = identifier + (suffix if re.fullmatch(r"\.[a-z0-9]{1,12}", suffix) else ".txt")
    _save_bytes(root, "chat-files", filename, data)
    return TextReference(path=f"chat-files/{filename}", folder_id=folder_id, name=name,
                         size=len(data), sha256=hashlib.sha256(data).hexdigest())


def prepare_image_directory(root: Path, directory: str) -> None:
    with contextlib.suppress(FileExistsError):
        create_entry(root, ".", directory, "directory")
    with parent_directory(root, directory):
        pass


def save_image(root: Path, directory: str, identifier: str, data: bytes, folder_id: str) -> ImageReference:
    mime = image_mime(data)
    if not mime or not data or len(data) > IMAGE_LIMIT:
        raise UserFacingError("图片格式不受支持，或超过 16 MiB。")
    name = identifier + IMAGE_EXTENSIONS[mime]
    _save_bytes(root, directory, name, data)
    return ImageReference(
        path=f"{directory}/{name}", folder_id=folder_id, mime_type=mime,
        size=len(data), sha256=hashlib.sha256(data).hexdigest(),
    )


def _save_bytes(root: Path, directory: str, name: str, data: bytes) -> None:
    prepare_image_directory(root, directory)
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


def image_data_url(root: Path, reference: ImageReference) -> str:
    data = attachment_bytes(root, reference)
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
