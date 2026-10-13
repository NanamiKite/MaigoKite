"""Workspace-scoped TODO tool."""
from __future__ import annotations

from typing import Any

from agent.repository import SQLiteRepository
from agent.tools.base import BaseTool, ExecutionContext, ToolResult


class TodoWriteTool(BaseTool):
    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    @property
    def name(self) -> str:
        return "todo_write"

    @property
    def description(self) -> str:
        return "Manage a TODO list scoped to the current workspace."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["add", "update", "list", "clear"]},
                "text": {"type": "string"},
                "id": {"type": "string"},
                "index": {"type": "integer"},
                "done": {"type": "boolean"},
            },
            "required": ["operation"],
        }

    async def execute(self, context: ExecutionContext, arguments: dict[str, Any]) -> ToolResult:
        workspace = str(context.metadata.get("workspace_id", "personal"))
        operation = arguments["operation"]
        if operation == "add":
            text = str(arguments.get("text", "")).strip()
            if not text:
                raise ValueError("text is required for add")
            snapshot = await self._repository.add_todo(workspace, text)
        elif operation == "update":
            todo_id = str(arguments.get("id", "")).strip()
            if not todo_id and isinstance(arguments.get("index"), int):
                items = await self._repository.list_todos(workspace)
                index = int(arguments["index"])
                if not 0 <= index < len(items):
                    raise IndexError("todo index is out of range")
                todo_id = str(items[index]["id"])
            if not todo_id:
                raise ValueError("id or index is required for update")
            update_text = arguments.get("text")
            normalized_text = str(update_text).strip() if update_text is not None else None
            if normalized_text == "":
                raise ValueError("text must not be empty")
            snapshot = await self._repository.update_todo(
                workspace, todo_id, normalized_text, arguments.get("done")
            )
        elif operation == "clear":
            snapshot = await self._repository.clear_todos(workspace)
        else:
            snapshot = await self._repository.list_todos(workspace)
        return ToolResult(
            data={"operation": operation, "items": snapshot, "count": len(snapshot)},
            summary=f"{operation}: {len(snapshot)} todo item(s)",
            raw=str(snapshot),
            metadata={"tool": self.name, "workspace_id": workspace},
        )
