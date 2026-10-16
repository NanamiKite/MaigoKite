"""Build bounded GUI views from already-read rows, without a database connection."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from agent.data_contracts import APPROVAL_RECORD, SNAPSHOT_RECORD, ApprovalRecord, ConversationSnapshotRecord
from agent.models.usage import aggregate_usage_events


def approval_value(row: sqlite3.Row) -> ApprovalRecord:
    item = dict(row)
    item["arguments"] = json.loads(item.pop("arguments_json"))
    item["reuse_scope"] = json.loads(item.pop("reuse_scope_json", "{}"))
    return APPROVAL_RECORD.validate_python(item)


def task_inspection(
    task: sqlite3.Row,
    calls: list[sqlite3.Row],
    approvals: list[sqlite3.Row],
    events: list[sqlite3.Row],
    questions: list[sqlite3.Row],
    usage_events: list[sqlite3.Row],
    last_sequence: int,
) -> dict[str, Any]:
    """Preserve event order and truncation rules for one locked repository read."""
    projection: dict[str, Any] = {
        "plan": None,
        "context": None,
        "changes": [],
        "summary": None,
        "usage": aggregate_usage_events([dict(row) for row in usage_events]),
        # sqlite3.Row iterates values; convert to a mapping before selecting columns.
        "questions": [
            {
                **{key: value for key, value in dict(row).items() if key != "options_json"},
                "options": json.loads(row["options_json"]),
            }
            for row in questions
        ],
        "truncated": len(calls) > 50 or len(approvals) > 50 or len(events) > 100,
    }
    for event in reversed(events[:100]):
        payload = json.loads(event["payload_json"])
        if event["event_type"] == "plan.updated":
            projection["plan"] = payload.get("items")
        elif event["event_type"] == "context.updated":
            projection["context"] = payload
        elif event["event_type"] == "file.changed":
            projection["changes"].append(payload)
        elif event["event_type"] == "message.completed":
            projection["summary"] = payload.get("content")
        elif event["event_type"] in {"task.succeeded", "task.completed"} and payload.get("summary") is not None:
            projection["summary"] = payload.get("summary")
    # Bound the wire snapshot independently of the number of rows. Large
    # evidence remains in event history; truncation is explicit to callers.
    remaining = 900_000

    def bounded[T: Mapping[str, object]](items: list[T]) -> list[T]:
        nonlocal remaining
        accepted: list[T] = []
        for item in items:
            size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if size > remaining:
                projection["truncated"] = True
                continue
            remaining -= size
            accepted.append(item)
        return accepted

    tool_calls = bounded([dict(row) for row in calls[:50]])
    approval_values = bounded([approval_value(row) for row in approvals[:50]])
    projection["changes"] = bounded(projection["changes"])
    projection["questions"] = bounded(projection["questions"])
    return {
        "task": dict(task),
        "tool_calls": tool_calls,
        "approvals": approval_values,
        "projection": projection,
        "last_sequence": int(last_sequence),
    }


def conversation_snapshot(rows: dict[str, list[sqlite3.Row]], sequence: int) -> ConversationSnapshotRecord:
    """Attach the captured cursor to the same bounded set of rows it describes."""
    result: dict[str, Any] = {"last_sequence": int(sequence), "truncated": False}
    remaining = 900_000
    for name, values in rows.items():
        result[name] = []
        result["truncated"] |= len(values) > 200
        for row in values[:200]:
            value = approval_value(row) if name == "approvals" else dict(row)
            size = len(json.dumps(value, ensure_ascii=False).encode())
            if size > remaining:
                result["truncated"] = True
                continue
            remaining -= size
            result[name].append(value)
        result[name].reverse()
    return SNAPSHOT_RECORD.validate_python(result, strict=True)
