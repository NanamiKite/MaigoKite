"""FileReadTool - Read file contents using LangChain StructuredTool."""

from __future__ import annotations

import asyncio
import codecs
import os
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent.errors import UserFacingError
from agent.tools.base import BaseTool, ExecutionContext, ToolResult


class FileReadParams(BaseModel):
    """Parameters for reading a file."""

    path: str = Field(description="Absolute or relative file path to read.")
    encoding: str = Field(default="utf-8", description="File encoding (default: utf-8).")
    max_bytes: int = Field(
        default=16_000,
        ge=1,
        le=5_000_000,
        description="Requested page size in bytes (default 16000; effective per-call ceiling 64000).",
    )
    offset: int = Field(default=0, ge=0, description="Byte offset from a previous next_offset. Paging requires UTF-8.")


async def _file_read_impl(
    path: str, encoding: str = "utf-8", max_bytes: int = 16_000, offset: int = 0
) -> dict[str, Any]:
    """Read file contents with a configurable size limit.

    Args:
        path: The file path to read.
        encoding: Character encoding of the file.
        max_bytes: Maximum number of bytes to read.
        offset: Byte cursor returned by the preceding UTF-8 page.

    Returns:
        Content, byte counts, a truncation flag and an optional next_offset.

    Raises:
        FileNotFoundError: If the file does not exist.
        PermissionError: If the process lacks read permission.
        ValueError: If the file is binary or cannot be decoded with the requested encoding.
    """
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    try:
        codec_name = codecs.lookup(encoding).name
    except LookupError as exc:
        raise ValueError(f"Unknown file encoding: {encoding}") from exc
    if offset and codec_name != "utf-8":
        raise ValueError("Non-zero offset requires UTF-8 encoding")
    limit = min(max_bytes, 64_000)
    await asyncio.sleep(0)
    with open(abs_path, "rb") as file:
        file.seek(offset)
        chunks = bytearray()
        while len(chunks) < limit + 1:
            chunk = file.read(min(8192, limit + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
            # Cooperative cancellation between bounded synchronous reads; no
            # worker thread whose read could outlive task.cancelled.
            await asyncio.sleep(0)
        file_size = os.fstat(file.fileno()).st_size
    truncated = len(chunks) > limit
    payload = bytes(chunks[:limit])
    if b"\x00" in payload:
        # Keep this authored diagnostic actionable without exposing a local path
        # or allowing arbitrary ValueError text through the shared error boundary.
        raise UserFacingError("不支持读取二进制文件，请选择文本文件或使用支持该格式的工具。")
    try:
        decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
        content = decoder.decode(payload, final=not truncated)
        buffered = decoder.getstate()[0]
        consumed = len(payload) - len(buffered)
        if truncated and consumed == 0:
            raise ValueError("max_bytes is too small for one character; increase the page size")
    except (LookupError, UnicodeDecodeError) as exc:
        raise ValueError(f"File is not valid {encoding} text: {path}") from exc
    return {
        "path": path,
        "content": content,
        "size_bytes": consumed,
        "file_size_bytes": file_size,
        "offset": offset,
        "next_offset": offset + consumed if truncated and codec_name == "utf-8" else None,
        "truncated": truncated,
    }


FILE_READ_DESCRIPTION = (
    "Read the contents of a file at the given path. "
    "Reads one bounded page. Use next_offset to continue a UTF-8 file; "
    "prefer targeted search before reading large files."
)


class FileReadTool(BaseTool):
    """Reads file contents from the filesystem via LangChain StructuredTool."""

    def __init__(self) -> None:
        self._structured = StructuredTool.from_function(
            coroutine=_file_read_impl,
            name="file_read",
            description=FILE_READ_DESCRIPTION,
            args_schema=FileReadParams,
        )

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return self._structured.description

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return FileReadParams.model_json_schema()

    async def execute(
        self, context: ExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        """Execute the file read operation and return structured results."""
        result = await _file_read_impl(**FileReadParams.model_validate(arguments).model_dump())
        content = result.get("content", "")
        size = result.get("size_bytes", len(content))
        read_path = result.get("path", arguments.get("path", ""))
        return ToolResult(
            data=result,
            summary=f"Read {size} bytes from {read_path}" + (" (truncated)" if result["truncated"] else ""),
            raw=content,
            metadata={"tool": "file_read", "type": "text"},
        )
