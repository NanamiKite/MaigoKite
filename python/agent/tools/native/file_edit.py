"""FileEditTool - Apply surgical find-and-replace edits using LangChain StructuredTool."""

from __future__ import annotations

import os
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent.tools.base import BaseTool, ExecutionContext, ToolResult
from agent.tools.native.file_change import file_change, read_before_write


class FileEditParams(BaseModel):
    """Parameters for performing a surgical edit on a file."""

    path: str = Field(description="The absolute or relative file path to edit.")
    old_string: str = Field(
        min_length=1,
        description=(
            "The exact substring to search for and replace. "
            "This must match the file content precisely, including whitespace and indentation."
        ),
    )
    new_string: str = Field(
        description="The replacement string that will substitute old_string.",
    )
    count: int = Field(
        default=1,
        ge=0,
        description="Maximum number of occurrences to replace (default: 1). Use 0 to replace all.",
    )
    encoding: str = Field(default="utf-8", description="File encoding (default: utf-8).")


def _file_edit_impl(
    path: str,
    old_string: str,
    new_string: str,
    count: int = 1,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Apply an exact find-and-replace edit to a file.

    Args:
        path: Target file path.
        old_string: Exact substring to find.
        new_string: Replacement string.
        count: Number of replacements (1 by default, 0 for all).
        encoding: File character encoding.

    Returns:
        A dict with keys replaced_count and file_size after edit.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If old_string does not appear in the file exactly once (or the requested count).
    """
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"File not found: {path}")

    with open(abs_path, encoding=encoding) as f:
        content = f.read()

    if count == 0:
        replaced_count = content.count(old_string)
        new_content = content.replace(old_string, new_string)
    else:
        replaced_count = content.count(old_string)
        if replaced_count == 0:
            raise ValueError(f"'{old_string}' not found in file {path}")
        if replaced_count > count:
            raise ValueError(
                f"Expected exactly {count} occurrence(s) of '{old_string}', "
                f"but found {replaced_count}. Use count=0 to replace all or narrow the search text."
            )
        new_content = content.replace(old_string, new_string, count)

    with open(abs_path, "w", encoding=encoding) as f:
        f.write(new_content)

    return {"path": path, "replaced_count": replaced_count, "file_size": len(new_content)}


FILE_EDIT_DESCRIPTION = (
    "Apply a precise, surgical find-and-replace edit to a file. "
    "The old_string must match exactly (including whitespace). "
    "Replaces up to `count` occurrences (default 1, use 0 for all)."
)


class FileEditTool(BaseTool):
    """Applies targeted edits to files using LangChain StructuredTool."""

    def __init__(self) -> None:
        self._structured = StructuredTool.from_function(
            func=_file_edit_impl,
            name="file_edit",
            description=FILE_EDIT_DESCRIPTION,
            args_schema=FileEditParams,
        )

    @property
    def name(self) -> str:
        return "file_edit"

    @property
    def description(self) -> str:
        return self._structured.description

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return FileEditParams.model_json_schema()

    async def execute(
        self, context: ExecutionContext, arguments: dict[str, Any]
    ) -> ToolResult:
        """Execute the surgical edit and report what was changed."""
        before = read_before_write(str(arguments["path"]))
        result = _file_edit_impl(**arguments)
        edit_path = result.get("path", arguments.get("path", ""))
        count = result.get("replaced_count", 0)
        return ToolResult(
            data={
                "path": edit_path,
                "replacements": count,
                "file_size": result.get("file_size", 0),
            },
            summary=f"Replaced {count} occurrence(s) in {edit_path}",
            raw=f"{edit_path}: {count} replacement(s)",
            metadata={"tool": "file_edit", "type": "edit", "file_change": file_change(
                str(edit_path), before, str(arguments.get("encoding", "utf-8")),
                str(context.metadata.get("workspace_root") or ""),
            )},
        )
