"""FileReadTool - Read file contents using LangChain StructuredTool."""

from __future__ import annotations

import asyncio
import base64
import codecs
import hashlib
import os
from pathlib import Path
from typing import Any, Literal

from anyio import to_thread
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent.document_visuals import read_document_ocr, recognize_image, render_document_page
from agent.documents import navigate_document, read_document
from agent.errors import UserFacingError
from agent.media import IMAGE_LIMIT, DocumentReference, ImageReference, image_mime
from agent.security.workspace_fs import read_file
from agent.tools.base import BaseTool, ExecutionContext, ToolResult


class FileReadParams(BaseModel):
    """Parameters for reading a file."""

    path: str = Field(description="Absolute or relative file path to read.")
    view: Literal["text", "image", "document", "ocr"] = Field(
        default="text", description="image for vision; ocr for image text; document for PDF/DOCX/XLSX/PPTX.",
    )
    document_action: Literal["read", "outline", "search", "render", "ocr"] = Field(
        default="read", description="Document mode: read, outline, search; render/ocr inspect one PDF page.",
    )
    query: str = Field(default="", max_length=200, description="Literal search text; document search only.")
    lookup_offset: int = Field(default=0, ge=0, le=1_000_000, description="Outline/search next_offset cursor.")
    ignore_case: bool = Field(default=True, description="Ignore case in document search.")
    unit: int = Field(
        default=1, ge=1,
        description="1-based PDF page, DOCX paragraph/table, XLSX row block or PPTX slide in document mode.",
    )
    unit_count: int = Field(default=5, ge=1, le=10, description="Document units per read (default 5).")
    text_offset: int = Field(default=0, ge=0, description="Document text cursor from the preceding next_offset.")
    sha256: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$",
                        description="Expected snapshot hash from an attachment; refuse changed contents.")
    encoding: str = Field(default="utf-8", description="File encoding (default: utf-8).")
    max_bytes: int = Field(
        default=16_000,
        ge=1,
        le=5_000_000,
        description="Requested page size in bytes (default 16000; effective per-call ceiling 64000).",
    )
    offset: int = Field(default=0, ge=0, description="Byte offset from a previous next_offset. Paging requires UTF-8.")
    start_line: int | None = Field(
        default=None, ge=1, description="Optional 1-based starting line in a UTF-8 text file.",
    )
    line_count: int = Field(default=100, ge=1, le=1000, description="Maximum lines when start_line is set.")


async def _file_read_impl(
    path: str, encoding: str = "utf-8", max_bytes: int = 16_000, offset: int = 0,
    view: Literal["text", "image", "document", "ocr"] = "text", sha256: str = "",
    unit: int = 1, text_offset: int = 0, unit_count: int = 5,
    document_action: Literal["read", "outline", "search", "render", "ocr"] = "read", query: str = "",
    lookup_offset: int = 0, ignore_case: bool = True,
    start_line: int | None = None, line_count: int = 100,
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
    if view != "document" and (document_action != "read" or query or lookup_offset):
        raise UserFacingError("目录和搜索仅支持文档模式。", code="DOCUMENT_ACTION_INVALID")
    if document_action not in {"outline", "search"} and (query or lookup_offset):
        raise UserFacingError("目录和搜索仅支持文档模式。", code="DOCUMENT_ACTION_INVALID")
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")
    try:
        codec_name = codecs.lookup(encoding).name
    except LookupError as exc:
        raise ValueError(f"Unknown file encoding: {encoding}") from exc
    if start_line is not None and (view != "text" or offset or codec_name != "utf-8"):
        raise UserFacingError("按行读取仅支持 UTF-8 文本，不能同时指定字节偏移或图片模式。")
    if offset and codec_name != "utf-8":
        raise ValueError("Non-zero offset requires UTF-8 encoding")
    limit = min(max_bytes, 64_000)
    if view != "text" and offset:
        raise UserFacingError("图片或文档模式不支持字节偏移。")
    if view != "text" or sha256 or start_line is not None:
        source = Path(abs_path)
        snapshot, _ = await to_thread.run_sync(read_file, source.parent, source.name, IMAGE_LIMIT)
        if len(snapshot) > IMAGE_LIMIT:
            raise UserFacingError("文件不能超过 16 MiB。", code="ATTACHMENT_TOO_LARGE")
        content_hash = hashlib.sha256(snapshot).hexdigest()
        if sha256 and content_hash != sha256:
            raise UserFacingError("附件内容已改变，请重新添加后发送。", code="ATTACHMENT_CHANGED")
        if view == "document" and document_action in {"outline", "search"}:
            navigation = await navigate_document(
                snapshot, action="outline" if document_action == "outline" else "search", query=query,
                offset=lookup_offset, ignore_case=ignore_case,
            )
            return {"path": path, "file_size_bytes": len(snapshot), **navigation.model_dump()}
        if view == "document" and document_action == "render":
            rendered = await render_document_page(snapshot, unit)
            return {"path": path, "format": "pdf", "parser_version": "pdfium-page-v1", "unit": unit,
                    "size_bytes": len(rendered), "file_size_bytes": len(snapshot), "sha256": content_hash,
                    "image_url": "data:image/png;base64," + base64.b64encode(rendered).decode("ascii")}
        if view == "document" and document_action == "ocr":
            document = await read_document_ocr(snapshot, unit, text_offset, limit)
            return {"path": path, "file_size_bytes": len(snapshot), **document.model_dump()}
        if view == "document":
            document = await read_document(
                snapshot, unit=unit, text_offset=text_offset, max_bytes=limit, unit_count=unit_count,
            )
            return {"path": path, "file_size_bytes": len(snapshot), **document.model_dump()}
        if view == "ocr":
            mime = image_mime(snapshot)
            if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                raise UserFacingError("模型图片输入支持 PNG、JPEG、WebP 和 GIF。")
            text = await recognize_image(snapshot)
            if text_offset > len(text):
                raise UserFacingError("页码或段落编号超出文档范围。", code="DOCUMENT_RANGE_INVALID")
            content = text[text_offset:].encode("utf-8")[:limit].decode("utf-8", errors="ignore")
            end = text_offset + len(content)
            return {"path": path, "content": content, "mode": "ocr", "sha256": content_hash,
                    "mime_type": mime, "file_size_bytes": len(snapshot),
                    "size_bytes": len(content.encode("utf-8")), "text_offset": text_offset,
                    "next_offset": end if end < len(text) else None, "truncated": end < len(text)}
        if view == "image":
            mime = image_mime(snapshot)
            if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                raise UserFacingError("模型图片输入支持 PNG、JPEG、WebP 和 GIF。")
            return {"path": path, "size_bytes": len(snapshot), "mime_type": mime, "sha256": content_hash,
                    "image_url": f"data:{mime};base64," + base64.b64encode(snapshot).decode("ascii")}
        file_size = len(snapshot)
        end = min(file_size, offset + limit)
        if start_line is not None:
            # Scan bounded bytes instead of splitting a potentially huge single line.
            for line in range(1, start_line):
                newline = snapshot.find(b"\n", offset)
                if newline < 0:
                    offset = file_size
                    break
                offset = newline + 1
                if line % 4096 == 0:
                    await asyncio.sleep(0)
            end = offset
            for _ in range(line_count):
                newline = snapshot.find(b"\n", end)
                if newline < 0:
                    end = file_size
                    break
                end = newline + 1
            end = min(end, offset + limit)
        # Hash and page refer to the same bytes, even if the path changes during the call.
        payload = snapshot[offset:end]
        truncated = end < file_size
    else:
        await asyncio.sleep(0)
        with open(abs_path, "rb") as file:
            file.seek(offset)
            chunks = bytearray()
            while len(chunks) < limit + 1:
                chunk = file.read(min(8192, limit + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
                # Yield between bounded reads so cancellation does not leave a reader running.
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
        **({"start_line": start_line} if start_line is not None else {}),
    }


FILE_READ_DESCRIPTION = (
    "Read the contents of a file at the given path. "
    "Reads one bounded page. Set start_line and line_count for selected UTF-8 lines (files up to 16 MiB), "
    "or use next_offset to continue by bytes, including a single long line; "
    "prefer targeted search before reading large files. "
    "For attachments, pass their sha256 to check the saved version. "
    "Use view=image to inspect a PNG/JPEG/WebP/GIF with a vision model; view=ocr reads image text locally "
    "when Tesseract is installed (continue via text_offset). OCR cannot describe non-text visual details. "
    "Use view=document for PDF pages, DOCX paragraphs/tables, XLSX row blocks or PPTX slides (unit starts at 1). "
    "Start with document_action=outline for real bookmarks/headings; document_action=search finds literal query "
    "in extracted text without reading every page. Both return up to 20 locations; paginate via lookup_offset. "
    "Open a location with document_action=read, unit and text_offset from that item, and unit_count=1. "
    "Preserve sha256 from each result to avoid mixing file versions. "
    "Continue a read with text_offset=next_offset using the same unit and unit_count; then advance to next_unit. "
    "For a PDF page with missing text, use document_action=render with a vision model, or ocr for local "
    "text recognition. Both inspect only the selected unit, not the entire document. "
    "Empty extracted text is not evidence about scanned images, drawings or omitted Office features."
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
        if "parser_version" in result:
            document_metadata: dict[str, Any] = {"image_folder": context.metadata.get("folder_id", "")}
            document_root = context.metadata.get("workspace_root")
            document_path = Path(result["path"]).absolute()
            if document_root and document_path.is_relative_to(Path(document_root)):
                document_reference = DocumentReference(
                    path=document_path.relative_to(document_root).as_posix(),
                    folder_id=context.metadata.get("folder_id", ""), name=document_path.name,
                    format=result["format"], sha256=result["sha256"], size=result["file_size_bytes"],
                )
                document_metadata["attachment_refs"] = [document_reference.model_dump()]
            if "image_url" in result:
                url = result.pop("image_url")
                document_metadata["image_bytes"] = result["size_bytes"]
                return ToolResult(data=result, summary=f"Rendered PDF page {result['unit']}",
                                  metadata=document_metadata, model_content=[{"type": "image", "url": url}])
            summary = (
                f"Found {len(result['items'])} document {result['action']} locations"
                if "items" in result else f"Read document unit {result['unit']} of {result['total_units']}"
            )
            return ToolResult(data=result, summary=summary, metadata=document_metadata)
        if "image_url" in result or result.get("mode") == "ocr":
            url = result.pop("image_url", None)
            metadata: dict[str, Any] = {
                "image_bytes": result["size_bytes"] if url else 0,
                "image_folder": context.metadata.get("folder_id", ""),
            }
            root = context.metadata.get("workspace_root")
            source = Path(result["path"]).absolute()
            if root and source.is_relative_to(Path(root)):
                # Preserve the exact bytes inspected, so compaction can retain a versioned reread.
                reference = ImageReference(
                    path=source.relative_to(root).as_posix(), folder_id=metadata["image_folder"],
                    sha256=result["sha256"], mime_type=result["mime_type"],
                    size=result.get("file_size_bytes", result["size_bytes"]),
                )
                metadata["attachment_refs"] = [reference.model_dump()]
            return ToolResult(
                data=result, summary=f"Read image from {result['path']}",
                model_content=[{"type": "image", "url": url}] if url else None, metadata=metadata,
            )
        content = result.get("content", "")
        size = result.get("size_bytes", len(content))
        read_path = result.get("path", arguments.get("path", ""))
        return ToolResult(
            data=result,
            summary=f"Read {size} bytes from {read_path}" + (" (truncated)" if result["truncated"] else ""),
            raw=content,
            metadata={"tool": "file_read", "type": "text"},
        )
