"""FileWriteTool - Write or overwrite file contents using LangChain StructuredTool."""

from __future__ import annotations

import os
import stat
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent.tools.base import BaseTool, ExecutionContext, ToolResult
from agent.tools.native.file_change import file_change, read_before_write


class FileWriteParams(BaseModel):
    """Parameters for writing a file."""

    path: str = Field(description="Absolute or relative file path to write to.")
    content: str = Field(description="The string content to write to the file.")
    encoding: str = Field(default="utf-8", description="File encoding (default: utf-8).")
    create_dirs: bool = Field(
        default=True,
        description="Create parent directories if they do not exist (default: True).",
    )


def _has_content(path: str, expected: bytes) -> bool:
    """Compare bounded binary chunks; text-mode reads normalize line endings."""
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return False
        with open(path, "rb") as stream:
            if os.fstat(stream.fileno()).st_size != len(expected):
                return False
            for offset in range(0, len(expected), 65536):
                chunk = expected[offset:offset + 65536]
                if stream.read(len(chunk)) != chunk:
                    return False
            return not stream.read(1)
    except OSError:
        return False


def _file_write_impl(
    path: str,
    content: str,
    encoding: str = "utf-8",
    create_dirs: bool = True,
) -> dict[str, Any]:
    """Write content to a file, optionally creating parent directories.

    Args:
        path: The target file path.
        content: The string content to write.
        encoding: Character encoding for the output file.
        create_dirs: Whether to create parent directories if missing.

    Returns:
        A write receipt, or reconciled=True when the requested bytes already exist.
    """
    abs_path = os.path.abspath(path)
    # Match the previous text-mode newline behavior, but finish encoding before
    # opening/truncating the target. Invalid encodings must not destroy a file.
    encoded = content.replace("\n", os.linesep).encode(encoding)
    if _has_content(abs_path, encoded):
        return {
            "path": path, "bytes_written": 0, "size_bytes": len(encoded),
            "created_dirs": [], "reconciled": True,
        }
    if create_dirs:
        parent = os.path.dirname(abs_path)
        created_dirs: list[str] = []
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
            created_dirs.append(parent)
        else:
            created_dirs = []
    else:
        created_dirs = []

    with open(abs_path, "wb") as f:
        bytes_written = f.write(encoded)

    return {
        "path": path,
        "bytes_written": bytes_written,
        "created_dirs": created_dirs,
    }


FILE_WRITE_DESCRIPTION = (
    "Write or overwrite the contents of a file at the given path. "
    "Creates parent directories if needed and supports configurable encoding. "
    "Requires user approval in restricted permission modes."
)


class FileWriteTool(BaseTool):
    """Writes content to a file using LangChain StructuredTool."""

    def __init__(self) -> None:
        self._structured = StructuredTool.from_function(
            func=_file_write_impl,
            name="file_write",
            description=FILE_WRITE_DESCRIPTION,
            args_schema=FileWriteParams,
        )

    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return self._structured.description

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return FileWriteParams.model_json_schema()

    async def execute(
        self, context: ExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        """Execute the file write operation and return a summary."""
        before = read_before_write(str(arguments["path"]))
        result = _file_write_impl(**arguments)
        write_path = result.get("path", arguments.get("path", ""))
        created = result.get("created_dirs", [])
        reconciled = bool(result.get("reconciled"))
        parts = [
            f"File already contains the requested bytes; no write performed: {write_path}"
            if reconciled else f"Wrote {result['bytes_written']} bytes to {write_path}"
        ]
        if created:
            parts.append(f"Created directories: {', '.join(created)}")
        change = file_change(
            str(write_path), None if reconciled else before,
            str(arguments.get("encoding", "utf-8")),
            str(context.metadata.get("workspace_root") or ""),
        )
        if reconciled:
            # This can be the first UI receipt after a crash. The original
            # before-image is unknown; never manufacture an empty/successful diff.
            change["reconciled"] = True
        return ToolResult(
            data=result,
            summary="; ".join(parts),
            raw="",
            metadata={"tool": "file_write", "type": "file", "file_change": change},
        )
