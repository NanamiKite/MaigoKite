"""Append-only JSONL audit sink with secret redaction."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class AuditEntry:
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    actor: str = ""
    action: str = ""
    resource: str = ""
    result: str = "success"
    details: dict[str, Any] = field(default_factory=dict)
    ip_address: str | None = None


class AuditLog:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path).expanduser() if path else None
        self._entries: list[AuditEntry] = []

    @staticmethod
    def _redact(value:Any)->Any:
        if isinstance(value, dict):
            return {
                k: (
                    "<redacted>"
                    if any(x in k.lower() for x in ("secret", "token", "password", "authorization"))
                    else AuditLog._redact(v)
                )
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [AuditLog._redact(v) for v in value]
        return value
    def record(self,entry:AuditEntry)->None:
        entry.details = self._redact(entry.details)
        self._entries.append(entry)
        if self._path:
            self._path.parent.mkdir(parents=True,exist_ok=True)
            with self._path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(asdict(entry), ensure_ascii=False, default=str) + "\n")

    def query(
        self,
        actor: str | None = None,
        action: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[AuditEntry]:
        return [
            e
            for e in self._entries
            if (actor is None or e.actor == actor)
            and (action is None or e.action == action)
            and (start_time is None or e.timestamp >= start_time)
            and (end_time is None or e.timestamp <= end_time)
        ]

    def export(self,format:str="json")->str:
        if format != "json":
            raise ValueError("only json export is supported")
        return json.dumps([asdict(e) for e in self._entries],ensure_ascii=False,default=str)
