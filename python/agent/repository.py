"""SQLite repository for Agent domain state."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.errors import UserFacingError
from agent.models.usage import aggregate_usage_events
from agent.structured_output import validate_output_schema, validated_result_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def folder_memory_scope(folder_id: str, root_path: str) -> str:
    """Bind memories to a registered folder and its canonical path, including the default folder."""
    return hashlib.sha256(json.dumps([folder_id, root_path], ensure_ascii=False).encode()).hexdigest()


def validate_subagent_limits(calls: int, concurrent: int) -> None:
    if type(calls) is not int or not 0 <= calls <= 32:
        raise ValueError("每轮子 Agent 上限须为 0–32，0 表示关闭。")
    if type(concurrent) is not int or not 1 <= concurrent <= 8 or (calls and concurrent > calls):
        raise ValueError("子 Agent 并行上限须为 1–8，启用时不能超过每轮派发上限。")


def validate_subagent_model_ids(value: list[str]) -> list[str]:
    if (not isinstance(value, list) or len(value) > 32
            or any(not isinstance(item, str) or not item or len(item) > 100 for item in value)):
        raise ValueError("子 Agent 模型须为最多 32 个已配置模型 ID。")
    if len(set(value)) != len(value):
        raise ValueError("子 Agent 模型不能重复。")
    return value


class ConversationBusyError(UserFacingError):
    """A conversation with unfinished work cannot be moved or removed."""


class MessageConflictError(UserFacingError):
    """A submission key was reused for a different request."""


class MessageReplay(Exception):
    """Return an existing product task; never execute a submitted message twice."""

    def __init__(self, task: dict[str, Any]) -> None:
        self.task = task
        super().__init__(str(task["id"]))


class SQLiteRepository:
    TASK_STATUSES = {
        "created",
        "queued",
        "running",
        "waiting_approval",
        "retry_wait",
        "succeeded",
        "failed",
        "rejected",
        "expired",
        "cancelled",
        "timed_out",
        "interrupted",
    }
    TOOL_CALL_STATUSES = {
        "proposed",
        "waiting_approval",
        "running",
        "succeeded",
        "failed",
        "denied",
        "cancelled",
        "timed_out",
        "expired",
    }
    APPROVAL_STATUSES = {"pending", "consumed", "rejected", "expired"}

    def __init__(self, path: str | Path) -> None:
        raw = str(path)
        self.path = raw if raw == ":memory:" else str(Path(raw).expanduser().resolve())
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._connection is not None:
            return
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
              title TEXT NOT NULL, model_id TEXT NOT NULL DEFAULT '', permission_mode TEXT NOT NULL,
              response_mode TEXT NOT NULL DEFAULT 'balanced',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspace_folders (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
              name TEXT NOT NULL, root_path TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(workspace_id, root_path)
            );
            CREATE TABLE IF NOT EXISTS workspace_memberships (
              workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL, role TEXT NOT NULL,
              PRIMARY KEY(workspace_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_conversations_workspace_updated
              ON conversations(workspace_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
              role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
              ON messages(conversation_id, created_at);
            CREATE TABLE IF NOT EXISTS user_preferences (
              workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
              subject_user_id TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL,
              updated_at TEXT NOT NULL, PRIMARY KEY(workspace_id, subject_user_id, key)
            );
            CREATE TABLE IF NOT EXISTS workspace_settings (
              workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
              key TEXT NOT NULL, value_json TEXT NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY(workspace_id, key)
            );
            CREATE TABLE IF NOT EXISTS audit_events (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, actor TEXT NOT NULL,
              action TEXT NOT NULL, resource TEXT NOT NULL, result TEXT NOT NULL,
              details_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
              title TEXT NOT NULL, status TEXT NOT NULL, input_text TEXT NOT NULL DEFAULT '',
              last_error TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_workspace_updated
              ON tasks(workspace_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS task_events (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
              task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
              sequence INTEGER NOT NULL, event_type TEXT NOT NULL,
              payload_json TEXT NOT NULL, occurred_at TEXT NOT NULL,
              UNIQUE(conversation_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS idx_task_events_conversation_sequence
              ON task_events(conversation_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_task_events_usage
              ON task_events(workspace_id, event_type, occurred_at);
            CREATE TABLE IF NOT EXISTS tool_calls (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
              task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, tool_name TEXT NOT NULL,
              arguments_json TEXT NOT NULL, assistant_content TEXT NOT NULL DEFAULT '',
              continuation_json TEXT NOT NULL DEFAULT '{}',
              result_json TEXT, summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
              risk_level TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tool_calls_conversation_created
              ON tool_calls(conversation_id, created_at);
            CREATE TABLE IF NOT EXISTS approvals (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
              principal_id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
              tool_call_id TEXT NOT NULL UNIQUE REFERENCES tool_calls(id) ON DELETE CASCADE,
              arguments_hash TEXT NOT NULL, risk_level TEXT NOT NULL, status TEXT NOT NULL,
              version INTEGER NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
              decided_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_approvals_workspace_status
              ON approvals(workspace_id, status, created_at DESC);
            CREATE TABLE IF NOT EXISTS todos (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, text TEXT NOT NULL,
              done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS questions (
              id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
              task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
              tool_call_id TEXT NOT NULL UNIQUE REFERENCES tool_calls(id) ON DELETE CASCADE,
              principal_id TEXT NOT NULL, title TEXT NOT NULL, options_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', version INTEGER NOT NULL DEFAULT 1,
              answer TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_questions_workspace_status ON questions(workspace_id,status);
            CREATE TABLE IF NOT EXISTS task_inputs (
              id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
              content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL,
              idempotency_key TEXT NOT NULL, UNIQUE(task_id,idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS idx_todos_workspace_created
              ON todos(workspace_id, created_at);
            CREATE TABLE IF NOT EXISTS user_memories (
              id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
              subject_user_id TEXT NOT NULL,
              category TEXT NOT NULL,
              content TEXT NOT NULL,
              source_type TEXT NOT NULL,
              source_id TEXT NOT NULL,
              confidence REAL NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_user_memories_subject_updated
              ON user_memories(workspace_id, subject_user_id, updated_at DESC);
            """
        )
        conversation_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(conversations)").fetchall()
        }
        if "response_mode" not in conversation_columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN response_mode TEXT NOT NULL DEFAULT 'balanced'")
        for column, definition in (
            ("memory_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("thinking", "TEXT NOT NULL DEFAULT ''"),
            ("forked_from_conversation_id", "TEXT NOT NULL DEFAULT ''"),
            ("forked_from_message_id", "TEXT NOT NULL DEFAULT ''"),
            ("fork_request_hash", "TEXT NOT NULL DEFAULT ''"),
            ("folder_id", "TEXT NOT NULL DEFAULT ''"), ("archived", "INTEGER NOT NULL DEFAULT 0"),
            ("max_subagent_calls", "INTEGER NOT NULL DEFAULT 0"),
            ("max_concurrent_subagents", "INTEGER NOT NULL DEFAULT 1"),
            ("subagent_model_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            if column not in conversation_columns:
                connection.execute(f"ALTER TABLE conversations ADD COLUMN {column} {definition}")
        memory_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(user_memories)").fetchall()}
        if "folder_scope" not in memory_columns:
            # NULL preserves legacy records without guessing which project owns them.
            connection.execute("ALTER TABLE user_memories ADD COLUMN folder_scope TEXT")
        connection.execute("""CREATE INDEX IF NOT EXISTS idx_user_memories_folder
                              ON user_memories(workspace_id,subject_user_id,folder_scope,updated_at DESC)""")
        tool_call_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(tool_calls)").fetchall()}
        if "continuation_json" not in tool_call_columns:
            connection.execute("ALTER TABLE tool_calls ADD COLUMN continuation_json TEXT NOT NULL DEFAULT '{}'")
        if "subagent_admitted" not in tool_call_columns:
            connection.execute("ALTER TABLE tool_calls ADD COLUMN subagent_admitted INTEGER NOT NULL DEFAULT 0")
        approval_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(approvals)").fetchall()}
        if "note" not in approval_columns:
            connection.execute("ALTER TABLE approvals ADD COLUMN note TEXT NOT NULL DEFAULT ''")
        for column, definition in (
            ("reuse_scope_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("authorization_scope", "TEXT NOT NULL DEFAULT 'once'"),
            ("authorization_expires_at", "TEXT NOT NULL DEFAULT ''"),
            ("authorization_revoked_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in approval_columns:
                connection.execute(f"ALTER TABLE approvals ADD COLUMN {column} {definition}")
        task_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
        for column, definition in (
            ("input_text", "TEXT NOT NULL DEFAULT ''"),
            ("last_error", "TEXT NOT NULL DEFAULT ''"),
            ("version", "INTEGER NOT NULL DEFAULT 1"),
            ("request_hash", "TEXT NOT NULL DEFAULT ''"),
            ("output_schema_json", "TEXT NOT NULL DEFAULT ''"),
            ("output_result_json", "TEXT"),
            ("max_subagent_calls", "INTEGER NOT NULL DEFAULT 0"),
            ("max_concurrent_subagents", "INTEGER NOT NULL DEFAULT 1"),
            ("model_call_limit", "INTEGER NOT NULL DEFAULT 0"),
            ("model_calls_used", "INTEGER NOT NULL DEFAULT 0"),
            ("subagent_model_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            if column not in task_columns:
                connection.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")
        connection.execute(
            """UPDATE conversations SET permission_mode=CASE permission_mode
                 WHEN 'plan' THEN 'read_only'
                 WHEN 'manual' THEN 'read_only'
                 WHEN 'accept_edits' THEN 'workspace'
                 WHEN 'auto' THEN 'workspace_auto'
                 WHEN 'full_access' THEN 'workspace_auto'
                 ELSE permission_mode END
               WHERE permission_mode IN ('plan','manual','accept_edits','auto','full_access')"""
        )
        connection.commit()
        self._connection = connection
        await self.ensure_workspace("personal", "Personal", "personal")
        async with self._lock:
            connection.execute(
                "INSERT OR IGNORE INTO workspace_memberships(workspace_id,user_id,role) VALUES(?,?,?)",
                ("personal", "local-user", "owner"),
            )
            connection.commit()

    async def close(self) -> None:
        if self._connection:
            self._connection.close()
            self._connection = None

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("repository is not open")
        return self._connection

    async def ensure_workspace(self, workspace_id: str, name: str, kind: str) -> dict[str, Any]:
        if kind not in {"personal", "team"}:
            raise ValueError("workspace kind must be personal or team")
        now = _now()
        async with self._lock:
            self._db().execute(
                "INSERT OR IGNORE INTO workspaces(id,name,kind,created_at,updated_at) VALUES(?,?,?,?,?)",
                (workspace_id, name, kind, now, now),
            )
            self._db().commit()
        return {"id": workspace_id, "name": name, "kind": kind}

    async def create_conversation(
        self,
        workspace_id: str,
        title: str = "",
        model_id: str = "",
        permission_mode: str = "workspace",
        response_mode: str = "balanced",
        folder_id: str = "",
        max_subagent_calls: int = 0,
        max_concurrent_subagents: int = 1,
        subagent_model_ids: list[str] | None = None,
        thinking: str = "",
        memory_enabled: bool = False,
    ) -> dict[str, Any]:
        validate_subagent_limits(max_subagent_calls, max_concurrent_subagents)
        model_ids = validate_subagent_model_ids([] if subagent_model_ids is None else subagent_model_ids)
        conversation_id = f"conv_{uuid.uuid4().hex}"
        now = _now()
        title = title.strip() or "新对话"
        async with self._lock:
            self._require_folder(workspace_id, folder_id)
            self._db().execute(
                """INSERT INTO conversations(
                     id,workspace_id,title,model_id,permission_mode,response_mode,created_at,updated_at,folder_id,
                     max_subagent_calls,max_concurrent_subagents,subagent_model_ids_json,thinking,memory_enabled
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (conversation_id, workspace_id, title, model_id, permission_mode, response_mode, now, now, folder_id,
                 max_subagent_calls, max_concurrent_subagents, json.dumps(model_ids), thinking, memory_enabled),
            )
            self._db().commit()
        return await self.get_conversation(workspace_id, conversation_id)

    async def check_workspace_access(self, workspace_id: str, user_id: str) -> str | None:
        async with self._lock:
            row = (
                self._db()
                .execute(
                    "SELECT role FROM workspace_memberships WHERE workspace_id=? AND user_id=?",
                    (workspace_id, user_id),
                )
                .fetchone()
            )
        return None if row is None else str(row[0])

    async def fork_conversation(
        self, workspace_id: str, conversation_id: str, message_id: str, request_key: str,
    ) -> dict[str, Any]:
        """Copy a bounded, immutable transcript, never an execution continuation.

        Creation and messages commit together. The same submission key returns
        the same branch after an ambiguous network response, even if the source
        later changes. Source deletion does not delete its independent branches.
        """
        if (not request_key or len(request_key) > 128 or not request_key.isascii()
                or any(ord(char) < 33 or ord(char) > 126 for char in request_key)):
            raise ValueError("分岔请求需要 1–128 位可打印 ASCII Idempotency-Key。")
        branch_id = "conv_" + uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
            ["conversation-fork", workspace_id, conversation_id, request_key],
        )).hex
        request_hash = hashlib.sha256(json.dumps([conversation_id, message_id]).encode()).hexdigest()
        now = _now()
        async with self._lock:
            db = self._db()
            with db:
                existing = db.execute("SELECT * FROM conversations WHERE id=?", (branch_id,)).fetchone()
                if existing is not None:
                    if not hmac.compare_digest(existing["fork_request_hash"], request_hash):
                        raise MessageConflictError("此分岔请求键已用于另一个消息位置。")
                else:
                    source = db.execute(
                        "SELECT * FROM conversations WHERE workspace_id=? AND id=?", (workspace_id, conversation_id),
                    ).fetchone()
                    if source is None:
                        raise KeyError(conversation_id)
                    self._require_idle_conversation(workspace_id, conversation_id)
                    if message_id:
                        pivot = db.execute(
                            "SELECT rowid,role,id FROM messages WHERE conversation_id=? AND id=?",
                            (conversation_id, message_id),
                        ).fetchone()
                    else:
                        pivot = db.execute(
                            "SELECT rowid,role,id FROM messages WHERE conversation_id=? ORDER BY rowid DESC LIMIT 1",
                            (conversation_id,),
                        ).fetchone()
                    if pivot is None or pivot["role"] != "assistant":
                        raise ValueError("请从已保存的助手回复创建分岔；空对话或未完成的用户回合不能分岔。")
                    # Bound copies and reject instead of silently truncating the
                    # user's history. Runtime imports this same 500-message bound.
                    history = db.execute(
                        """SELECT role,content,created_at FROM messages WHERE conversation_id=? AND rowid<=?
                           AND role IN ('user','assistant') ORDER BY rowid LIMIT 501""",
                        (conversation_id, pivot["rowid"]),
                    ).fetchall()
                    if len(history) > 500 or sum(len(row["content"].encode()) for row in history) > 2 * 1024 * 1024:
                        raise ValueError("此位置的对话历史过长，请选择较早的回复，或在新对话中提供摘要。")
                    # No credentials, task state, approvals or reusable grants.
                    # Full host access must be explicitly opted into per branch.
                    mode = source["permission_mode"]
                    if mode not in {"read_only", "workspace"}:
                        mode = "workspace"
                    db.execute(
                        """INSERT INTO conversations(id,workspace_id,title,model_id,permission_mode,response_mode,
                           created_at,updated_at,folder_id,thinking,max_subagent_calls,max_concurrent_subagents,
                           subagent_model_ids_json,forked_from_conversation_id,forked_from_message_id,fork_request_hash)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (branch_id, workspace_id, source["title"][:190] + " · 分岔", source["model_id"], mode,
                         source["response_mode"], now, now, source["folder_id"], source["thinking"],
                         source["max_subagent_calls"], source["max_concurrent_subagents"], source["subagent_model_ids_json"],
                         conversation_id, pivot["id"], request_hash),
                    )
                    db.executemany(
                        "INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)",
                        [(f"msg_{uuid.uuid4().hex}", branch_id, row["role"], row["content"], row["created_at"])
                         for row in history],
                    )
        return await self.get_conversation(workspace_id, branch_id)

    async def list_conversations(self, workspace_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            rows = (
                self._db()
                .execute(
                    """SELECT c.*, COUNT(m.id) AS message_count FROM conversations c
                   LEFT JOIN messages m ON m.conversation_id=c.id
                   WHERE c.workspace_id=? GROUP BY c.id ORDER BY c.updated_at DESC""",
                    (workspace_id,),
                )
                .fetchall()
            )
        return [dict(row) for row in rows]

    async def get_conversation(self, workspace_id: str, conversation_id: str) -> dict[str, Any]:
        async with self._lock:
            row = (
                self._db()
                .execute(
                    """SELECT c.*, COUNT(m.id) AS message_count FROM conversations c
                   LEFT JOIN messages m ON m.conversation_id=c.id
                   WHERE c.workspace_id=? AND c.id=? GROUP BY c.id""",
                    (workspace_id, conversation_id),
                )
                .fetchone()
            )
        if row is None:
            raise KeyError(conversation_id)
        return dict(row)

    def _require_folder(self, workspace_id: str, folder_id: str) -> None:
        if folder_id and self._db().execute(
            "SELECT id FROM workspace_folders WHERE workspace_id=? AND id=?", (workspace_id, folder_id),
        ).fetchone() is None:
            raise KeyError(folder_id)

    def _require_idle_conversation(self, workspace_id: str, conversation_id: str) -> None:
        active = self._db().execute(
            """SELECT id FROM tasks WHERE workspace_id=? AND conversation_id=?
               AND status IN ('created','queued','running','waiting_approval','retry_wait') LIMIT 1""",
            (workspace_id, conversation_id),
        ).fetchone()
        if active:
            raise ConversationBusyError("请先停止或完成此对话中的任务，再移动、归档或删除。")

    async def list_workspace_folders(self, workspace_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            rows = self._db().execute(
                "SELECT id,name,root_path FROM workspace_folders WHERE workspace_id=? ORDER BY created_at",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    async def get_workspace_folder(self, workspace_id: str, folder_id: str) -> dict[str, Any]:
        async with self._lock:
            row = self._db().execute(
                "SELECT id,name,root_path FROM workspace_folders WHERE workspace_id=? AND id=?",
                (workspace_id, folder_id),
            ).fetchone()
        if row is None:
            raise KeyError(folder_id)
        return dict(row)

    async def create_workspace_folder(self, workspace_id: str, name: str, root_path: str) -> dict[str, Any]:
        async with self._lock:
            self._db().execute(
                "INSERT OR IGNORE INTO workspace_folders(id,workspace_id,name,root_path,created_at) VALUES(?,?,?,?,?)",
                (f"folder_{uuid.uuid4().hex}", workspace_id, name, root_path, _now()),
            )
            self._db().commit()
            row = self._db().execute(
                "SELECT id,name,root_path FROM workspace_folders WHERE workspace_id=? AND root_path=?",
                (workspace_id, root_path),
            ).fetchone()
        assert row is not None
        return dict(row)

    async def delete_workspace_folder(self, workspace_id: str, folder_id: str, actor: str) -> None:
        """Unregister a folder and retain its conversations in the default folder.

        Validate and write under the same lock/transaction as task admission.
        No filesystem operation or checkpoint deletion belongs to this action.
        """
        if not folder_id:
            raise ValueError("默认工作区不能删除。")
        async with self._lock:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                self._require_folder(workspace_id, folder_id)
                conversations = db.execute(
                    "SELECT id FROM conversations WHERE workspace_id=? AND folder_id=?",
                    (workspace_id, folder_id),
                ).fetchall()
                for row in conversations:
                    try:
                        self._require_idle_conversation(workspace_id, row["id"])
                    except ConversationBusyError:
                        raise ConversationBusyError("工作区中有未结束的任务，请完成或停止后再删除。") from None
                    latest = db.execute(
                        """SELECT status FROM tasks WHERE workspace_id=? AND conversation_id=?
                           ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                        (workspace_id, row["id"]),
                    ).fetchone()
                    if latest is not None and latest["status"] == "interrupted":
                        raise ConversationBusyError("工作区中有可恢复的任务，请完成或停止后再删除。")
                for row in conversations:
                    self._revoke_authorizations(workspace_id, row["id"])
                now = _now()
                folder = db.execute("SELECT root_path FROM workspace_folders WHERE workspace_id=? AND id=?",
                                    (workspace_id, folder_id)).fetchone()
                scope = folder_memory_scope(folder_id, str(folder["root_path"]))
                # Retain notes for explicit management, never adopt them into the default folder.
                db.execute("UPDATE user_memories SET folder_scope=NULL WHERE workspace_id=? AND folder_scope=?",
                           (workspace_id, scope))
                db.execute("DELETE FROM user_preferences WHERE workspace_id=? AND key=?",
                           (workspace_id, "memory.enabled." + scope))
                db.execute(
                    """UPDATE conversations SET folder_id='',memory_enabled=0,updated_at=?
                       WHERE workspace_id=? AND folder_id=?""", (now, workspace_id, folder_id),
                )
                db.execute("DELETE FROM workspace_folders WHERE workspace_id=? AND id=?", (workspace_id, folder_id))
                db.execute(
                    "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
                    (f"audit_{uuid.uuid4().hex}", workspace_id, actor, "workspace_folder.deleted", folder_id,
                     "success", json.dumps({"retained_conversations": len(conversations), "destination_folder_id": ""}), now),
                )

    async def delete_conversation(self, workspace_id: str, conversation_id: str) -> list[str]:
        async with self._lock:
            self._require_idle_conversation(workspace_id, conversation_id)
            task_ids = [str(row[0]) for row in self._db().execute(
                "SELECT id FROM tasks WHERE workspace_id=? AND conversation_id=?", (workspace_id, conversation_id),
            ).fetchall()]
            self._db().execute(
                "DELETE FROM tasks WHERE workspace_id=? AND conversation_id=?",
                (workspace_id, conversation_id),
            )
            cursor = self._db().execute(
                "DELETE FROM conversations WHERE workspace_id=? AND id=?",
                (workspace_id, conversation_id),
            )
            self._db().commit()
        if cursor.rowcount == 0:
            raise KeyError(conversation_id)
        return task_ids

    async def update_conversation_permission(
        self, workspace_id: str, conversation_id: str, permission_mode: str
    ) -> dict[str, Any]:
        async with self._lock:
            self._revoke_authorizations(workspace_id, conversation_id)
            cursor = self._db().execute(
                "UPDATE conversations SET permission_mode=?,updated_at=? WHERE workspace_id=? AND id=?",
                (permission_mode, _now(), workspace_id, conversation_id),
            )
            self._db().commit()
        if cursor.rowcount == 0:
            raise KeyError(conversation_id)
        return await self.get_conversation(workspace_id, conversation_id)

    async def update_conversation_settings(
        self,
        workspace_id: str,
        conversation_id: str,
        *,
        model_id: str | None = None,
        response_mode: str | None = None,
        thinking: str | None = None,
        permission_mode: str | None = None,
        title: str | None = None,
        folder_id: str | None = None,
        archived: bool | None = None,
        max_subagent_calls: int | None = None,
        max_concurrent_subagents: int | None = None,
        subagent_model_ids: list[str] | None = None,
        memory_enabled: bool | None = None,
    ) -> dict[str, Any]:
        assignments: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("model_id", model_id),
            ("response_mode", response_mode),
            ("thinking", thinking),
            ("permission_mode", permission_mode),
            ("title", title),
            ("folder_id", folder_id),
            ("archived", archived),
            ("memory_enabled", False if folder_id is not None else memory_enabled),
            ("max_subagent_calls", max_subagent_calls),
            ("max_concurrent_subagents", max_concurrent_subagents),
            ("subagent_model_ids_json", None if subagent_model_ids is None
             else json.dumps(validate_subagent_model_ids(subagent_model_ids))),
        ):
            if value is not None:
                assignments.append(f"{column}=?")
                values.append(value)
        if not assignments:
            return await self.get_conversation(workspace_id, conversation_id)
        assignments.append("updated_at=?")
        values.extend([_now(), workspace_id, conversation_id])
        async with self._lock:
            current = self._db().execute(
                "SELECT * FROM conversations WHERE workspace_id=? AND id=?", (workspace_id, conversation_id),
            ).fetchone()
            if current is None:
                raise KeyError(conversation_id)
            validate_subagent_limits(
                current["max_subagent_calls"] if max_subagent_calls is None else max_subagent_calls,
                current["max_concurrent_subagents"] if max_concurrent_subagents is None else max_concurrent_subagents,
            )
            if folder_id is not None:
                self._require_folder(workspace_id, folder_id)
            if folder_id is not None or archived is not None or model_id is not None or thinking is not None:
                self._require_idle_conversation(workspace_id, conversation_id)
            if any(value is not None for value in (permission_mode, folder_id, archived)):
                self._revoke_authorizations(workspace_id, conversation_id)
            cursor = self._db().execute(
                f"UPDATE conversations SET {','.join(assignments)} WHERE workspace_id=? AND id=?",
                values,
            )
            self._db().commit()
        if cursor.rowcount == 0:
            raise KeyError(conversation_id)
        return await self.get_conversation(workspace_id, conversation_id)

    async def replace_conversation_model(
        self, workspace_id: str, previous_model_id: str, replacement_model_id: str
    ) -> None:
        async with self._lock:
            self._db().execute(
                """UPDATE conversations SET model_id=?,updated_at=?
                   WHERE workspace_id=? AND model_id=?""",
                (replacement_model_id, _now(), workspace_id, previous_model_id),
            )
            self._db().commit()

    async def append_message(self, conversation_id: str, role: str, content: str) -> dict[str, Any]:
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("invalid message role")
        message = {
            "id": f"msg_{uuid.uuid4().hex}",
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "created_at": _now(),
        }
        async with self._lock:
            if role == "user":
                self._db().execute(
                    """UPDATE conversations SET title=? WHERE id=? AND title='新对话'
                       AND NOT EXISTS(SELECT 1 FROM messages WHERE conversation_id=?)""",
                    (content[:28], conversation_id, conversation_id),
                )
            self._db().execute(
                "INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(:id,:conversation_id,:role,:content,:created_at)",
                message,
            )
            self._db().execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conversation_id))
            self._db().commit()
        return message

    async def list_messages(self, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 500)
        async with self._lock:
            rows = (
                self._db()
                .execute(
                    "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?",
                    (conversation_id, limit),
                )
                .fetchall()
            )
        return [dict(row) for row in reversed(rows)]

    async def save_preference(self, workspace_id: str, user_id: str, key: str, value: Any) -> None:
        async with self._lock:
            self._db().execute(
                """INSERT INTO user_preferences VALUES(?,?,?,?,?)
                   ON CONFLICT(workspace_id,subject_user_id,key)
                   DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (workspace_id, user_id, key, json.dumps(value, ensure_ascii=False), _now()),
            )
            self._db().commit()

    async def get_preference(self, workspace_id: str, user_id: str, key: str) -> Any | None:
        async with self._lock:
            row = (
                self._db()
                .execute(
                    "SELECT value_json FROM user_preferences WHERE workspace_id=? AND subject_user_id=? AND key=?",
                    (workspace_id, user_id, key),
                )
                .fetchone()
            )
        return None if row is None else json.loads(row[0])

    async def save_workspace_setting(self, workspace_id: str, key: str, value: Any) -> None:
        async with self._lock:
            if key in {"permissions.policy", "tools.selection", "extensions.applied_config"}:
                previous = self._db().execute(
                    "SELECT value_json FROM workspace_settings WHERE workspace_id=? AND key=?", (workspace_id, key),
                ).fetchone()
                if previous is None or json.loads(previous[0]) != value:
                    self._revoke_authorizations(workspace_id)
            self._db().execute(
                """INSERT INTO workspace_settings VALUES(?,?,?,?)
                   ON CONFLICT(workspace_id,key)
                   DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (workspace_id, key, json.dumps(value, ensure_ascii=False), _now()),
            )
            self._db().commit()

    async def get_workspace_setting(self, workspace_id: str, key: str) -> Any | None:
        async with self._lock:
            row = (
                self._db()
                .execute(
                    "SELECT value_json FROM workspace_settings WHERE workspace_id=? AND key=?",
                    (workspace_id, key),
                )
                .fetchone()
            )
        return None if row is None else json.loads(row[0])

    async def record_audit(
        self, workspace_id: str, actor: str, action: str, resource: str, result: str, details: dict[str, Any]
    ) -> None:
        safe = {
            key: value for key, value in details.items() if "secret" not in key.lower() and "token" not in key.lower()
        }
        async with self._lock:
            self._db().execute(
                "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"audit_{uuid.uuid4().hex}",
                    workspace_id,
                    actor,
                    action,
                    resource,
                    result,
                    json.dumps(safe, ensure_ascii=False, default=str),
                    _now(),
                ),
            )
            self._db().commit()

    async def list_audit_events(self, workspace_id: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 500)
        async with self._lock:
            rows = (
                self._db()
                .execute(
                    "SELECT * FROM audit_events WHERE workspace_id=? ORDER BY created_at DESC LIMIT ?",
                    (workspace_id, limit),
                )
                .fetchall()
            )
        return [dict(row) for row in rows]

    async def create_task_state(
        self,
        task_id: str,
        workspace_id: str,
        conversation_id: str,
        title: str,
        status: str,
        input_text: str = "",
    ) -> None:
        self._require_status(status, self.TASK_STATUSES, "task")
        now = _now()
        async with self._lock:
            conversation = self._db().execute(
                "SELECT archived FROM conversations WHERE workspace_id=? AND id=?", (workspace_id, conversation_id),
            ).fetchone()
            if conversation is None:
                raise KeyError(conversation_id)
            if conversation["archived"]:
                raise ConversationBusyError("请先恢复归档对话，再发送消息。")
            self._db().execute(
                """INSERT INTO tasks(
                     id,workspace_id,conversation_id,title,status,input_text,last_error,
                     version,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (task_id, workspace_id, conversation_id, title, status, input_text, "", 1, now, now),
            )
            self._db().commit()

    async def check_message_replay(self, workspace_id: str, task_id: str, request_hash: str) -> None:
        if not request_hash:
            return
        async with self._lock:
            row = self._db().execute(
                "SELECT * FROM tasks WHERE workspace_id=? AND id=?", (workspace_id, task_id),
            ).fetchone()
        if row is not None:
            if not hmac.compare_digest(str(row["request_hash"]), request_hash):
                raise MessageConflictError("Idempotency-Key was already used for a different message")
            raise MessageReplay(dict(row))

    async def start_chat_turn(
        self, task_id: str, workspace_id: str, conversation_id: str, prompt: str, request_hash: str = "",
        *, model_call_limit: int = 8, output_schema: dict[str, Any] | None = None,
    ) -> None:
        """Atomically admit one turn and append its user message (not an execution checkpoint).

        Keyed task IDs are deterministic at the API boundary. A committed admission
        survives a crash; resubmitting that key only returns its task, including an
        interrupted task. A new execution always needs a new submission key.
        """
        output_schema = validate_output_schema(output_schema)
        schema_json = json.dumps(output_schema, ensure_ascii=False, sort_keys=True) if output_schema is not None else ""
        now = _now()
        async with self._lock:
            db = self._db()
            with db:
                existing = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if existing is not None:
                    if (existing["workspace_id"] != workspace_id or existing["conversation_id"] != conversation_id
                            or not request_hash or not hmac.compare_digest(existing["request_hash"], request_hash)):
                        raise MessageConflictError("submission conflicts with an existing task")
                    raise MessageReplay(dict(existing))
                conversation = db.execute(
                    "SELECT * FROM conversations WHERE workspace_id=? AND id=?", (workspace_id, conversation_id),
                ).fetchone()
                if conversation is None:
                    raise KeyError(conversation_id)
                if conversation["archived"]:
                    raise ConversationBusyError("请先恢复归档对话，再发送消息。")
                active = db.execute(
                    """SELECT id FROM tasks WHERE workspace_id=? AND conversation_id=?
                       AND status IN ('created','queued','running','waiting_approval','retry_wait') LIMIT 1""",
                    (workspace_id, conversation_id),
                ).fetchone()
                if active is not None:
                    raise ConversationBusyError("conversation has unfinished work; finish or cancel it first")
                db.execute(
                    """INSERT INTO tasks(id,workspace_id,conversation_id,title,status,input_text,request_hash,
                       created_at,updated_at,max_subagent_calls,max_concurrent_subagents,model_call_limit,subagent_model_ids_json,
                       output_schema_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (task_id, workspace_id, conversation_id, prompt[:80] or "Agent task", "running", prompt,
                     request_hash, now, now, conversation["max_subagent_calls"],
                     conversation["max_concurrent_subagents"], model_call_limit, conversation["subagent_model_ids_json"], schema_json),
                )
                db.execute(
                    "INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)",
                    (f"msg_{uuid.uuid4().hex}", conversation_id, "user", prompt, now),
                )
                db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))

    async def reserve_subagent_call(self, workspace_id: str, task_id: str, call_id: str) -> tuple[int, int]:
        """Product admission receipt only; LangGraph remains the execution authority.

        Failed calls keep their slot. Replaying the same tool call reuses its slot.
        """
        async with self._lock:
            db = self._db()
            with db:
                task = db.execute("SELECT * FROM tasks WHERE workspace_id=? AND id=?", (workspace_id, task_id)).fetchone()
                call = db.execute(
                    "SELECT * FROM tool_calls WHERE workspace_id=? AND task_id=? AND id=? AND tool_name='delegate'",
                    (workspace_id, task_id, call_id),
                ).fetchone()
                if task is None or call is None or task["status"] != "running":
                    raise UserFacingError("当前任务不可委派。")
                used = db.execute(
                    "SELECT COUNT(*) FROM tool_calls WHERE task_id=? AND subagent_admitted=1", (task_id,),
                ).fetchone()[0]
                limit = task["max_subagent_calls"]
                if not call["subagent_admitted"]:
                    if used >= limit:
                        raise UserFacingError("本轮子 Agent 派发额度已用完（或未启用），请由主 Agent 完成剩余工作。")
                    db.execute("UPDATE tool_calls SET subagent_admitted=1 WHERE id=?", (call_id,))
                    used += 1
                return used, limit

    async def reserve_model_call(self, workspace_id: str, task_id: str, *, child: bool = False) -> None:
        """Shared parent/child request budget, including failed attempts and summaries.

        Old tasks with no admission snapshot retain their native graph limit.
        One call is held back from children for the parent's final synthesis.
        """
        async with self._lock:
            db = self._db()
            with db:
                task = db.execute("SELECT * FROM tasks WHERE workspace_id=? AND id=?", (workspace_id, task_id)).fetchone()
                if task is None or task["status"] != "running":
                    raise UserFacingError("任务已停止，不能继续请求模型。")
                if not task["model_call_limit"]:
                    return
                if task["model_calls_used"] >= task["model_call_limit"] - int(child):
                    raise UserFacingError("本轮共享模型调用预算已用完。子 Agent 不会获得额外预算。")
                db.execute("UPDATE tasks SET model_calls_used=model_calls_used+1 WHERE id=?", (task_id,))

    async def update_task_status(self, task_id: str, status: str, last_error: str = "") -> None:
        self._require_status(status, self.TASK_STATUSES, "task")
        async with self._lock:
            cursor = self._db().execute(
                """UPDATE tasks SET status=?,last_error=?,version=version+1,updated_at=?
                   WHERE id=?""",
                (status, last_error, _now(), task_id),
            )
            self._db().commit()
        if cursor.rowcount == 0:
            raise KeyError(task_id)

    async def get_task(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        async with self._lock:
            row = (
                self._db()
                .execute("SELECT * FROM tasks WHERE workspace_id=? AND id=?", (workspace_id, task_id))
                .fetchone()
            )
        if row is None:
            raise KeyError(task_id)
        return dict(row)

    async def get_task_result(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        task = await self.get_task(workspace_id, task_id)
        ready = task["status"] == "succeeded" and task.get("output_result_json") is not None
        return {
            "task_id": task_id, "status": task["status"], "ready": ready,
            "output_mode": "json_schema" if task.get("output_schema_json") else "text",
            "schema": json.loads(task["output_schema_json"]) if task.get("output_schema_json") else None,
            "value": json.loads(task["output_result_json"]) if ready else None,
            "error": task["last_error"],
        }

    async def complete_structured_task(
        self, workspace_id: str, task_id: str, value: Any,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Commit a validated result, its visible message and success in one transaction.

        Checkpoint remains execution state. This row is the consumable product
        result; a concurrent stop must never expose it under a cancelled task.
        """
        async with self._lock:
            db = self._db()
            with db:
                row = db.execute("SELECT * FROM tasks WHERE workspace_id=? AND id=?", (workspace_id, task_id)).fetchone()
                if row is None:
                    raise KeyError(task_id)
                if row["status"] in {"cancelled", "failed", "succeeded", "timed_out", "expired", "rejected"}:
                    return dict(row), None
                if row["status"] != "running" or not row["output_schema_json"]:
                    raise ConversationBusyError("当前任务不处于结构化结果提交阶段。")
                encoded = validated_result_json(json.loads(row["output_schema_json"]), value)
                now = _now()
                message = {
                    "id": f"msg_{uuid.uuid4().hex}", "conversation_id": row["conversation_id"],
                    "role": "assistant", "content": encoded, "created_at": now,
                }
                db.execute(
                    "INSERT INTO messages(id,conversation_id,role,content,created_at) "
                    "VALUES(:id,:conversation_id,:role,:content,:created_at)", message,
                )
                db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, row["conversation_id"]))
                db.execute(
                    """UPDATE tasks SET output_result_json=?,status='succeeded',last_error='',
                       version=version+1,updated_at=? WHERE workspace_id=? AND id=?""",
                    (encoded, now, workspace_id, task_id),
                )
                task = dict(db.execute("SELECT * FROM tasks WHERE workspace_id=? AND id=?", (workspace_id, task_id)).fetchone())
            return task, message

    async def resume_interrupted_task(self, workspace_id: str, task_id: str, version: int) -> None:
        """Commit product admission, not execution state or a continuation.

        Recheck under the same lock as conversation archive/move operations so
        they cannot slip between runtime preparation and the running projection.
        """
        async with self._lock:
            db = self._db()
            task = db.execute(
                """SELECT t.*,c.archived FROM tasks t JOIN conversations c ON c.id=t.conversation_id
                   WHERE t.workspace_id=? AND t.id=?""", (workspace_id, task_id),
            ).fetchone()
            if task is None:
                raise KeyError(task_id)
            if task["status"] != "interrupted" or task["version"] != version:
                raise ValueError("task is not interrupted at this version; refresh its status")
            if task["archived"]:
                raise ValueError("archived conversations cannot resume execution")
            self._require_idle_conversation(workspace_id, task["conversation_id"])
            db.execute(
                "UPDATE tasks SET status='running',last_error='',version=version+1,updated_at=? WHERE id=?",
                (_now(), task_id),
            )
            db.commit()

    async def list_tasks(self, workspace_id: str, conversation_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 500)
        sql = "SELECT * FROM tasks WHERE workspace_id=?"
        params: list[Any] = [workspace_id]
        if conversation_id:
            sql += " AND conversation_id=?"
            params.append(conversation_id)
        sql += " ORDER BY updated_at DESC,id DESC LIMIT ?"
        params.append(limit)
        async with self._lock:
            rows = self._db().execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    async def has_unfinished_tasks(self, *, include_waiting: bool = True) -> bool:
        """Global Core resources cannot be replaced underneath any active turn."""
        async with self._lock:
            sql = "SELECT 1 FROM tasks WHERE status IN ('created','queued','running','retry_wait')"
            if include_waiting:
                sql += """ UNION ALL SELECT 1 FROM tasks WHERE status='waiting_approval'
                           UNION ALL SELECT 1 FROM approvals WHERE status='pending'
                           UNION ALL SELECT 1 FROM questions WHERE status='pending'"""
            return self._db().execute(sql + " LIMIT 1").fetchone() is not None

    async def latest_conversation_task(self, workspace_id: str, conversation_id: str) -> dict[str, Any] | None:
        """Creation order, not updated_at: a resumed old task is not a new turn."""
        async with self._lock:
            row = self._db().execute(
                "SELECT * FROM tasks WHERE workspace_id=? AND conversation_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (workspace_id, conversation_id),
            ).fetchone()
        return dict(row) if row is not None else None

    async def append_task_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = str(payload.get("task_id") or "")
        conversation_id = str(payload.get("conversation_id") or "")
        if not task_id or not conversation_id:
            return dict(payload)
        occurred_at = _now()
        event_id = f"event_{uuid.uuid4().hex}"
        async with self._lock:
            task = (
                self._db()
                .execute(
                    "SELECT workspace_id FROM tasks WHERE id=? AND conversation_id=?",
                    (task_id, conversation_id),
                )
                .fetchone()
            )
            if task is None:
                return dict(payload)
            sequence_row = (
                self._db()
                .execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM task_events WHERE conversation_id=?",
                    (conversation_id,),
                )
                .fetchone()
            )
            sequence = int(sequence_row[0])
            event_payload = dict(payload)
            enriched = {
                **payload,
                "event_id": event_id,
                "sequence": sequence,
                "occurred_at": occurred_at,
            }
            self._db().execute(
                """INSERT INTO task_events(
                     id,workspace_id,conversation_id,task_id,sequence,event_type,payload_json,occurred_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    str(task["workspace_id"]),
                    conversation_id,
                    task_id,
                    sequence,
                    event_type,
                    json.dumps(event_payload, ensure_ascii=False, default=str),
                    occurred_at,
                ),
            )
            self._db().commit()
        return enriched

    async def list_task_events(
        self,
        workspace_id: str,
        conversation_id: str,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 1000)
        async with self._lock:
            rows = (
                self._db()
                .execute(
                    """SELECT * FROM task_events
                   WHERE workspace_id=? AND conversation_id=? AND sequence>?
                   ORDER BY sequence LIMIT ?""",
                    (workspace_id, conversation_id, max(after_sequence, 0), limit),
                )
                .fetchall()
            )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(str(item.pop("payload_json")))
            except (TypeError, json.JSONDecodeError):
                item["payload"] = {}
            result.append(item)
        return result

    async def usage_statistics(self, workspace_id: str, since: str, until: str) -> dict[str, Any]:
        from agent.models.statistics import project_statistics

        sql = """SELECT id,task_id,event_type,payload_json,occurred_at FROM task_events
                 WHERE workspace_id=? AND occurred_at>=? AND occurred_at<=?
                 AND event_type IN ('context.updated','model.call.started','model.call.failed','model.usage')
                 ORDER BY occurred_at,conversation_id,sequence"""
        values = (workspace_id, since, until)

        def read() -> dict[str, Any]:
            # A separate WAL read snapshot keeps analytics off the execution connection.
            connection = sqlite3.connect(Path(self.path).as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                return project_statistics(connection.execute(sql, values))
            finally:
                connection.close()

        if self.path != ":memory:":
            return await asyncio.to_thread(read)
        async with self._lock:
            return await asyncio.to_thread(lambda: project_statistics(self._db().execute(sql, values)))

    async def recover_interrupted_tasks(self) -> list[dict[str, Any]]:
        """Mark work without a safe continuation checkpoint as explicitly interrupted."""
        interrupted: list[dict[str, Any]] = []
        now = _now()
        async with self._lock:
            rows = (
                self._db()
                .execute(
                    """SELECT * FROM tasks
                   WHERE status IN ('created','queued','running','retry_wait')"""
                )
                .fetchall()
            )
            for row in rows:
                item = dict(row)
                self._db().execute(
                    """UPDATE tasks SET status='interrupted',last_error=?,version=version+1,
                       updated_at=? WHERE id=?""",
                    ("Agent Core restarted before a safe continuation checkpoint", now, item["id"]),
                )
                item.update({"status": "interrupted", "updated_at": now})
                interrupted.append(item)
            self._db().commit()
        return interrupted

    async def interrupt_ephemeral_approvals(self, tool_names: set[str]) -> list[dict[str, Any]]:
        """Close approvals whose continuation depends on process-local resources."""
        if not tool_names:
            return []
        placeholders = ",".join("?" for _ in tool_names)
        now = _now()
        reason = "Process-local tool session was lost during Agent Core shutdown"
        interrupted: list[dict[str, Any]] = []
        async with self._lock:
            rows = (
                self._db()
                .execute(
                    f"""SELECT DISTINCT t.* FROM tasks t
                    JOIN approvals a ON a.task_id=t.id
                    JOIN tool_calls c ON c.id=a.tool_call_id
                    WHERE t.status='waiting_approval' AND a.status='pending'
                      AND c.tool_name IN ({placeholders})""",
                    tuple(sorted(tool_names)),
                )
                .fetchall()
            )
            for row in rows:
                item = dict(row)
                self._db().execute(
                    """UPDATE tasks SET status='interrupted',last_error=?,version=version+1,
                       updated_at=? WHERE id=?""",
                    (reason, now, item["id"]),
                )
                self._db().execute(
                    """UPDATE approvals SET status='rejected',decided_at=?,version=version+1
                       WHERE task_id=? AND status='pending'""",
                    (now, item["id"]),
                )
                self._db().execute(
                    """UPDATE tool_calls SET status='cancelled',updated_at=?
                       WHERE task_id=? AND status='waiting_approval'""",
                    (now, item["id"]),
                )
                item.update({"status": "interrupted", "last_error": reason, "updated_at": now})
                interrupted.append(item)
            self._db().commit()
        return interrupted

    async def cancel_task(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        now = _now()
        async with self._lock:
            row = (
                self._db()
                .execute("SELECT * FROM tasks WHERE workspace_id=? AND id=?", (workspace_id, task_id))
                .fetchone()
            )
            if row is None:
                raise KeyError(task_id)
            if str(row["status"]) in {"succeeded", "failed", "cancelled", "timed_out", "expired"}:
                return dict(row)
            self._db().execute(
                """UPDATE tasks SET status='cancelled',last_error='Cancelled by user',
                   version=version+1,updated_at=? WHERE id=?""",
                (now, task_id),
            )
            self._db().execute(
                """UPDATE approvals SET status='rejected',decided_at=?,version=version+1
                   WHERE task_id=? AND status='pending'""",
                (now, task_id),
            )
            self._db().execute(
                "UPDATE questions SET status='cancelled',version=version+1 WHERE task_id=? AND status='pending'",
                (task_id,),
            )
            self._db().execute(
                """UPDATE tool_calls SET status='cancelled',updated_at=?
                   WHERE task_id=? AND status IN ('proposed','waiting_approval','running')""",
                (now, task_id),
            )
            self._db().commit()
        return await self.get_task(workspace_id, task_id)

    async def create_tool_call(
        self,
        tool_call_id: str,
        workspace_id: str,
        conversation_id: str,
        task_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        assistant_content: str = "",
    ) -> dict[str, Any]:
        # continuation_json remains only for non-destructive compatibility with
        # existing databases. Execution never reads or writes that legacy payload.
        now = _now()
        async with self._lock:
            self._db().execute(
                """INSERT INTO tool_calls(
                     id,workspace_id,conversation_id,task_id,tool_name,arguments_json,
                     assistant_content,status,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    tool_call_id,
                    workspace_id,
                    conversation_id,
                    task_id,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False, sort_keys=True),
                    assistant_content,
                    "proposed",
                    now,
                    now,
                ),
            )
            self._db().commit()
        return await self.get_tool_call(tool_call_id)

    async def get_tool_call(self, tool_call_id: str) -> dict[str, Any]:
        async with self._lock:
            row = self._db().execute("SELECT * FROM tool_calls WHERE id=?", (tool_call_id,)).fetchone()
        if row is None:
            raise KeyError(tool_call_id)
        return dict(row)

    async def request_question(
        self, workspace_id: str, conversation_id: str, task_id: str, tool_call_id: str,
        principal_id: str, title: str, options: list[dict[str, Any]],
    ) -> dict[str, Any]:
        async with self._lock:
            db = self._db()
            with db:
                db.execute(
                    """INSERT OR IGNORE INTO questions(id,workspace_id,conversation_id,task_id,
                       tool_call_id,principal_id,title,options_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (f"question_{uuid.uuid4().hex}", workspace_id, conversation_id, task_id,
                     tool_call_id, principal_id, title, json.dumps(options, ensure_ascii=False), _now()),
                )
                # Keep the existing task-status DTO compatible with the GUI;
                # question.requested distinguishes this wait from an approval.
                db.execute("UPDATE tasks SET status='waiting_approval',updated_at=?,version=version+1 WHERE id=?",
                           (_now(), task_id))
                db.execute("UPDATE tool_calls SET status='waiting_approval',updated_at=? WHERE id=?",
                           (_now(), tool_call_id))
                row = db.execute("SELECT * FROM questions WHERE tool_call_id=?", (tool_call_id,)).fetchone()
        return dict(row)

    async def get_question(self, workspace_id: str, question_id: str) -> dict[str, Any]:
        async with self._lock:
            row = self._db().execute("SELECT * FROM questions WHERE workspace_id=? AND id=?",
                                     (workspace_id, question_id)).fetchone()
        if row is None:
            raise KeyError(question_id)
        return dict(row)

    async def list_questions(self, workspace_id: str, conversation_id: str = "") -> list[dict[str, Any]]:
        async with self._lock:
            rows = self._db().execute(
                """SELECT q.* FROM questions q JOIN tasks t ON t.id=q.task_id
                   WHERE q.workspace_id=? AND q.status='pending' AND t.status='waiting_approval'
                     AND (?='' OR q.conversation_id=?) ORDER BY q.created_at LIMIT 500""",
                (workspace_id, conversation_id, conversation_id),
            ).fetchall()
        return [dict(row) for row in rows]

    async def task_questions(self, workspace_id: str, task_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            rows = self._db().execute("SELECT * FROM questions WHERE workspace_id=? AND task_id=?",
                                      (workspace_id, task_id)).fetchall()
        return [dict(row) for row in rows]

    async def submit_task_input(self, workspace_id: str, task_id: str, content: str,
                                idempotency_key: str) -> dict[str, Any]:
        if not content.strip() or len(content) > 10000 or not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("content and an idempotency key (up to 128 characters) are required")
        async with self._lock:
            db = self._db()
            with db:
                task = db.execute("SELECT status FROM tasks WHERE workspace_id=? AND id=?",
                                  (workspace_id, task_id)).fetchone()
                if task is None:
                    raise KeyError(task_id)
                existing = db.execute("SELECT * FROM task_inputs WHERE task_id=? AND idempotency_key=?",
                                      (task_id, idempotency_key)).fetchone()
                if existing is not None:
                    if existing["content"] != content.strip():
                        raise ValueError("idempotency key was already used for another message")
                    return dict(existing)
                if task["status"] != "running":
                    raise ValueError("task is not running; resolve its pending interaction or send a new turn")
                count = db.execute("SELECT COUNT(*) FROM task_inputs WHERE task_id=? AND status='queued'", (task_id,)).fetchone()[0]
                if count >= 20:
                    raise ValueError("too many pending messages")
                item = {"id": f"input_{uuid.uuid4().hex}", "task_id": task_id, "content": content.strip(),
                        "status": "queued", "created_at": _now()}
                db.execute(
                    "INSERT INTO task_inputs(id,task_id,content,status,created_at,idempotency_key) VALUES(?,?,?,?,?,?)",
                    (item["id"], task_id, item["content"], item["status"], item["created_at"], idempotency_key),
                )
        return item

    async def task_inputs(self, workspace_id: str, task_id: str, *, pending: bool = False) -> list[dict[str, Any]]:
        await self.get_task(workspace_id, task_id)
        async with self._lock:
            rows = self._db().execute(
                """SELECT i.*,t.status AS task_status FROM task_inputs i JOIN tasks t ON t.id=i.task_id
                   WHERE t.workspace_id=? AND i.task_id=? AND (?=0 OR i.status='queued')
                   ORDER BY i.created_at,i.id LIMIT 500""", (workspace_id, task_id, int(pending)),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            task_status = item.pop("task_status")
            if item["status"] == "queued" and task_status not in {"running", "waiting_approval"}:
                item["status"] = "not_consumed"
            if not pending or item["status"] == "queued":
                result.append(item)
        return result

    async def consume_task_inputs(self, workspace_id: str, task_id: str, message_ids: set[str]) -> list[dict[str, Any]]:
        consumed = []
        async with self._lock:
            db = self._db()
            with db:
                rows = db.execute(
                    """SELECT i.*,t.conversation_id FROM task_inputs i JOIN tasks t ON t.id=i.task_id
                       WHERE t.workspace_id=? AND i.task_id=? AND i.status='queued'""",
                    (workspace_id, task_id),
                ).fetchall()
                for row in rows:
                    if row["id"] not in message_ids:
                        continue
                    db.execute("UPDATE task_inputs SET status='consumed' WHERE id=?", (row["id"],))
                    db.execute("INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?)",
                               (row["id"], row["conversation_id"], "user", row["content"], row["created_at"]))
                    consumed.append({**dict(row), "status": "consumed"})
        return consumed

    async def answer_question(self, workspace_id: str, question_id: str, principal_id: str,
                              answer: str, version: int) -> dict[str, Any]:
        if not answer.strip() or len(answer) > 10000:
            raise ValueError("answer must contain 1 to 10000 characters")
        async with self._lock:
            db = self._db()
            with db:
                row = db.execute(
                    """SELECT q.*,t.status AS task_status FROM questions q JOIN tasks t ON t.id=q.task_id
                       WHERE q.workspace_id=? AND q.id=?""", (workspace_id, question_id),
                ).fetchone()
                if row is None:
                    raise KeyError(question_id)
                if row["principal_id"] != principal_id:
                    raise PermissionError("question belongs to another principal")
                if row["status"] != "pending" or row["version"] != version or row["task_status"] != "waiting_approval":
                    raise ValueError("question is no longer pending or its version changed")
                db.execute("UPDATE questions SET answer=?,status='answered',version=version+1 WHERE id=?",
                           (answer.strip(), question_id))
                db.execute("UPDATE tasks SET status='running',updated_at=?,version=version+1 WHERE id=?",
                           (_now(), row["task_id"]))
                db.execute(
                    "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
                    (f"audit_{uuid.uuid4().hex}", workspace_id, principal_id, "question.answered",
                     question_id, "answered", json.dumps({"version": version}, ensure_ascii=False), _now()),
                )
                # Visible history must also retain answers if a later run imports
                # product messages instead of a successfully completed checkpoint.
                db.execute("INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)",
                           (f"answer_{question_id}", row["conversation_id"], "user",
                            f"{row['title']}\n{answer.strip()}", _now()))
        return await self.get_question(workspace_id, question_id)

    async def inspect_task(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        """Read one bounded GUI snapshot, scoped to the authenticated workspace.

        No runtime continuation or checkpoint is exposed. All queries run under
        the repository lock, so the projection cursor belongs to this snapshot.
        """
        async with self._lock:
            db = self._db()
            task = db.execute("SELECT * FROM tasks WHERE workspace_id=? AND id=?",
                              (workspace_id, task_id)).fetchone()
            if task is None:
                raise KeyError(task_id)
            calls = db.execute(
                """SELECT id,task_id,tool_name,arguments_json,result_json,summary,status,
                          risk_level,created_at,updated_at FROM tool_calls
                   WHERE workspace_id=? AND task_id=? ORDER BY created_at,id LIMIT 51""",
                (workspace_id, task_id),
            ).fetchall()
            approvals = db.execute(
                """SELECT a.*,t.tool_name,t.arguments_json FROM approvals a
                   JOIN tool_calls t ON t.id=a.tool_call_id
                   WHERE a.workspace_id=? AND a.task_id=? ORDER BY a.created_at LIMIT 51""",
                (workspace_id, task_id),
            ).fetchall()
            events = db.execute(
                """SELECT event_type,payload_json,sequence FROM task_events
                   WHERE workspace_id=? AND task_id=? AND event_type IN
                     ('plan.updated','context.updated','file.changed','message.completed','task.completed','task.succeeded')
                   ORDER BY sequence DESC LIMIT 101""",
                (workspace_id, task_id),
            ).fetchall()
            last_sequence = db.execute("SELECT COALESCE(MAX(sequence),0) FROM task_events WHERE task_id=?",
                                       (task_id,)).fetchone()[0]
            questions = db.execute("SELECT id,tool_call_id,title,options_json,status,version,answer FROM questions WHERE task_id=?",
                                   (task_id,)).fetchall()
            usage_events = db.execute("""SELECT id,event_type,payload_json FROM task_events WHERE task_id=?
                AND event_type IN ('model.usage','model.call.started','model.call.failed','context.compacted')
                ORDER BY sequence""", (task_id,)).fetchall()
        projection: dict[str, Any] = {
            "plan": None, "context": None, "changes": [], "summary": None,
            "usage": aggregate_usage_events([dict(row) for row in usage_events]),
            "questions": [{**{key: row[key] for key in row.keys() if key != "options_json"},
                           "options": json.loads(row["options_json"])} for row in questions],
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
        def bounded(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal remaining
            accepted = []
            for item in items:
                size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
                if size > remaining:
                    projection["truncated"] = True
                    continue
                remaining -= size
                accepted.append(item)
            return accepted
        tool_calls = bounded([dict(row) for row in calls[:50]])
        approval_values = bounded([self._approval_value(row) for row in approvals[:50]])
        projection["changes"] = bounded(projection["changes"])
        projection["questions"] = bounded(projection["questions"])
        return {"task": dict(task), "tool_calls": tool_calls, "approvals": approval_values,
                "projection": projection, "last_sequence": int(last_sequence)}

    async def conversation_snapshot(self, workspace_id: str, conversation_id: str) -> dict[str, Any]:
        """One bounded product snapshot and cursor, not runtime/event sourcing state.

        Clients must subscribe/buffer live events before reading this snapshot.
        The cursor covers product facts only; activity/text deltas need event history.
        """
        async with self._lock:
            db = self._db()
            if db.execute("SELECT id FROM conversations WHERE workspace_id=? AND id=?",
                          (workspace_id, conversation_id)).fetchone() is None:
                raise KeyError(conversation_id)
            rows = {
                "messages": db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC,rowid DESC LIMIT 201",
                                       (conversation_id,)).fetchall(),
                "tasks": db.execute("SELECT * FROM tasks WHERE workspace_id=? AND conversation_id=? ORDER BY created_at DESC,rowid DESC LIMIT 201",
                                    (workspace_id, conversation_id)).fetchall(),
                "approvals": db.execute("""SELECT a.*,t.tool_name,t.arguments_json FROM approvals a
                    JOIN tool_calls t ON t.id=a.tool_call_id WHERE a.workspace_id=? AND a.conversation_id=?
                    AND a.status='pending' ORDER BY a.created_at DESC LIMIT 201""", (workspace_id, conversation_id)).fetchall(),
                "questions": db.execute("SELECT * FROM questions WHERE workspace_id=? AND conversation_id=? AND status='pending' ORDER BY created_at DESC LIMIT 201",
                                        (workspace_id, conversation_id)).fetchall(),
            }
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0) FROM task_events WHERE workspace_id=? AND conversation_id=?",
                                  (workspace_id, conversation_id)).fetchone()[0]
        result: dict[str, Any] = {"last_sequence": int(sequence), "truncated": False}
        remaining = 900_000
        for name, values in rows.items():
            result[name] = []
            result["truncated"] |= len(values) > 200
            for row in values[:200]:
                value = self._approval_value(row) if name == "approvals" else dict(row)
                value.pop("request_hash", None)
                size = len(json.dumps(value, ensure_ascii=False).encode())
                if size > remaining:
                    result["truncated"] = True
                    continue
                remaining -= size
                result[name].append(value)
            result[name].reverse()
        return result

    async def list_command_calls(self, workspace_id: str, conversation_id: str = "") -> list[dict[str, Any]]:
        async with self._lock:
            rows = self._db().execute(
                """SELECT c.*,t.status AS task_status FROM tool_calls c JOIN tasks t ON t.id=c.task_id
                   WHERE c.workspace_id=? AND c.tool_name IN ('exec_command','bash')
                     AND (?='' OR c.conversation_id=?) ORDER BY c.created_at DESC LIMIT 100""",
                (workspace_id, conversation_id, conversation_id),
            ).fetchall()
        return [dict(row) for row in rows]

    async def update_tool_call(
        self,
        tool_call_id: str,
        status: str,
        *,
        risk_level: str = "",
        arguments: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        summary: str = "",
    ) -> None:
        self._require_status(status, self.TOOL_CALL_STATUSES, "tool call")
        async with self._lock:
            self._db().execute(
                """UPDATE tool_calls SET status=?,risk_level=CASE WHEN ?='' THEN risk_level ELSE ? END,
                   arguments_json=COALESCE(?,arguments_json),
                   result_json=COALESCE(?,result_json),summary=CASE WHEN ?='' THEN summary ELSE ? END,
                   updated_at=? WHERE id=?""",
                (
                    status,
                    risk_level,
                    risk_level,
                    None
                    if arguments is None
                    else json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    None if result is None else json.dumps(result, ensure_ascii=False, default=str),
                    summary,
                    summary,
                    _now(),
                    tool_call_id,
                ),
            )
            self._db().commit()

    async def create_approval(
        self,
        workspace_id: str,
        conversation_id: str,
        principal_id: str,
        task_id: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        risk_level: str,
        ttl_minutes: int = 15,
        reuse_scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        approval_id = f"approval_{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)
        normalized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        arguments_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        async with self._lock:
            self._db().execute(
                """INSERT INTO approvals(
                   id,workspace_id,conversation_id,principal_id,task_id,tool_call_id,
                   arguments_hash,risk_level,status,version,expires_at,created_at,decided_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    approval_id,
                    workspace_id,
                    conversation_id,
                    principal_id,
                    task_id,
                    tool_call_id,
                    arguments_hash,
                    risk_level,
                    "pending",
                    1,
                    (now + timedelta(minutes=ttl_minutes)).isoformat(),
                    now.isoformat(),
                    None,
                ),
            )
            self._db().execute(
                "UPDATE approvals SET reuse_scope_json=? WHERE id=?",
                (json.dumps(reuse_scope or {}, ensure_ascii=False, sort_keys=True), approval_id),
            )
            self._db().execute(
                """UPDATE tool_calls SET status='waiting_approval',risk_level=?,arguments_json=?,updated_at=?
                   WHERE id=?""",
                (risk_level, normalized, now.isoformat(), tool_call_id),
            )
            self._db().execute(
                """UPDATE tasks SET status='waiting_approval',version=version+1,updated_at=?
                   WHERE id=?""",
                (now.isoformat(), task_id),
            )
            self._db().commit()
        return await self.get_approval(workspace_id, approval_id)

    @staticmethod
    def _approval_value(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["arguments"] = json.loads(item.pop("arguments_json"))
        item["reuse_scope"] = json.loads(item.pop("reuse_scope_json", "{}"))
        return item

    def _revoke_authorizations(self, workspace_id: str, conversation_id: str = "") -> None:
        """Called under the Repository lock, committed with the policy change."""
        self._db().execute(
            """UPDATE approvals SET authorization_revoked_at=? WHERE workspace_id=?
               AND (?='' OR conversation_id=?) AND authorization_scope!='once'
               AND authorization_revoked_at=''""",
            (_now(), workspace_id, conversation_id, conversation_id),
        )

    async def list_authorizations(
        self, workspace_id: str, conversation_id: str, principal_id: str, task_id: str = "",
    ) -> list[dict[str, Any]]:
        """List unexpired scoped approvals for the user-facing authorization list."""
        async with self._lock:
            return self._list_authorizations(workspace_id, conversation_id, principal_id, task_id)

    async def execution_authority(
        self, workspace_id: str, conversation_id: str, principal_id: str, task_id: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Read the mode and its valid grants at one tool-admission boundary.

        Mode changes revoke reusable grants under this same lock. Reading them
        separately could combine a new read-only mode with a revoked write grant.
        The snapshot authorizes one admission, not later calls in the tool batch.
        """
        async with self._lock:
            row = self._db().execute(
                "SELECT permission_mode,archived FROM conversations WHERE workspace_id=? AND id=?",
                (workspace_id, conversation_id),
            ).fetchone()
            if row is None:
                raise KeyError(conversation_id)
            if row["archived"]:
                raise PermissionError("archived conversations cannot execute tools")
            return str(row["permission_mode"]), self._list_authorizations(
                workspace_id, conversation_id, principal_id, task_id,
            )

    def _list_authorizations(
        self, workspace_id: str, conversation_id: str, principal_id: str, task_id: str,
    ) -> list[dict[str, Any]]:
        """Query grants while the caller holds the Repository lock."""
        rows = self._db().execute(
            """SELECT a.* FROM approvals a JOIN tasks t ON t.id=a.task_id
                   JOIN conversations c ON c.id=a.conversation_id
                   WHERE a.workspace_id=? AND a.conversation_id=? AND a.principal_id=?
                   AND a.status='consumed' AND a.authorization_revoked_at=''
                   AND a.authorization_expires_at>? AND c.archived=0
                   AND (a.authorization_scope='session' OR
                        (a.authorization_scope='task' AND (?='' OR a.task_id=?)
                         AND t.status IN ('running','waiting_approval','interrupted')))
                   ORDER BY a.created_at DESC LIMIT 200""",
            (workspace_id, conversation_id, principal_id, _now(), task_id, task_id),
        ).fetchall()
        return [{"approval_id": row["id"], "conversation_id": row["conversation_id"],
                 "task_id": row["task_id"], "scope": row["authorization_scope"],
                 "expires_at": row["authorization_expires_at"],
                 "resource": json.loads(row["reuse_scope_json"])} for row in rows]

    async def revoke_authorization(self, workspace_id: str, approval_id: str, principal_id: str) -> None:
        async with self._lock:
            row = self._db().execute(
                "SELECT * FROM approvals WHERE workspace_id=? AND id=?", (workspace_id, approval_id),
            ).fetchone()
            if row is None:
                raise KeyError(approval_id)
            if row["principal_id"] != principal_id:
                raise PermissionError("authorization belongs to another principal")
            if row["authorization_scope"] == "once":
                raise ValueError("approval has no reusable authorization")
            if row["authorization_revoked_at"]:
                return
            now = _now()
            with self._db() as db:
                db.execute("UPDATE approvals SET authorization_revoked_at=? WHERE id=?", (now, approval_id))
                db.execute("INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
                           (f"audit_{uuid.uuid4().hex}", workspace_id, principal_id,
                            "authorization.revoked", approval_id, "success", "{}", now))

    @staticmethod
    def approval_matches_arguments(approval: dict[str, Any], arguments: dict[str, Any]) -> bool:
        """Bind execution to the audit hash, not the mutable tool-call projection."""
        normalized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hmac.compare_digest(
            str(approval["arguments_hash"]), hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        )

    async def get_approval(self, workspace_id: str, approval_id: str) -> dict[str, Any]:
        async with self._lock:
            row = (
                self._db()
                .execute(
                    """SELECT a.*,t.tool_name,t.arguments_json,t.assistant_content
                   FROM approvals a JOIN tool_calls t ON t.id=a.tool_call_id
                   WHERE a.workspace_id=? AND a.id=?""",
                    (workspace_id, approval_id),
                )
                .fetchone()
            )
        if row is None:
            raise KeyError(approval_id)
        return self._approval_value(row)

    async def list_approvals(
        self, workspace_id: str, conversation_id: str | None = None, status: str = "pending"
    ) -> list[dict[str, Any]]:
        query = """SELECT a.*,t.tool_name,t.arguments_json,t.assistant_content
                   FROM approvals a JOIN tool_calls t ON t.id=a.tool_call_id
                   WHERE a.workspace_id=? AND a.status=?"""
        params: list[Any] = [workspace_id, status]
        if conversation_id:
            query += " AND a.conversation_id=?"
            params.append(conversation_id)
        query += " ORDER BY a.created_at"
        async with self._lock:
            now = _now()
            expired = (
                self._db()
                .execute(
                    """SELECT id,task_id,tool_call_id FROM approvals
                   WHERE workspace_id=? AND status='pending' AND expires_at<=?""",
                    (workspace_id, now),
                )
                .fetchall()
            )
            for item in expired:
                self._db().execute(
                    "UPDATE approvals SET status='expired',decided_at=?,version=version+1 WHERE id=?",
                    (now, item["id"]),
                )
                self._db().execute(
                    "UPDATE tool_calls SET status='expired',updated_at=? WHERE id=?",
                    (now, item["tool_call_id"]),
                )
                self._db().execute(
                    """UPDATE tasks SET status='timed_out',last_error='Approval expired',
                       version=version+1,updated_at=? WHERE id=?""",
                    (now, item["task_id"]),
                )
                self._db().execute(
                    "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
                    (
                        f"audit_{uuid.uuid4().hex}",
                        workspace_id,
                        "system",
                        "approval.expired",
                        item["id"],
                        "expired",
                        "{}",
                        now,
                    ),
                )
            if expired:
                self._db().commit()
            rows = self._db().execute(query, params).fetchall()
        return [self._approval_value(row) for row in rows]

    async def decide_approval(
        self, workspace_id: str, approval_id: str, principal_id: str, decision: str, version: int,
        note: str = "",
        scope: str = "once",
    ) -> dict[str, Any]:
        if decision not in {"allow", "deny"}:
            raise ValueError("decision must be allow or deny")
        if scope not in {"once", "task", "session"} or (decision == "deny" and scope != "once"):
            raise ValueError("scope must be once, task or session; denial is always once")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("approval note must not exceed 2000 characters")
        note = note.strip()
        now = datetime.now(timezone.utc)
        async with self._lock:
            row = (
                self._db()
                .execute(
                    "SELECT * FROM approvals WHERE workspace_id=? AND id=?",
                    (workspace_id, approval_id),
                )
                .fetchone()
            )
            if row is None:
                raise KeyError(approval_id)
            item = dict(row)
            if item["principal_id"] != principal_id:
                raise PermissionError("approval belongs to another principal")
            if item["status"] != "pending":
                raise ValueError("approval has already been decided")
            if int(item["version"]) != version:
                raise ValueError("approval version conflict")
            reuse = json.loads(item["reuse_scope_json"])
            if scope != "once":
                if not reuse or item["risk_level"] == "L5":
                    raise PermissionError("this operation only supports one-time approval")
                conversation = self._db().execute(
                    "SELECT permission_mode,archived FROM conversations WHERE workspace_id=? AND id=?",
                    (workspace_id, item["conversation_id"]),
                ).fetchone()
                if conversation is None or conversation["archived"] or conversation["permission_mode"] != reuse["mode"]:
                    raise PermissionError("conversation policy changed; refresh the approval")
            task = self._db().execute(
                "SELECT status FROM tasks WHERE workspace_id=? AND id=?", (workspace_id, item["task_id"]),
            ).fetchone()
            if task is None or task["status"] != "waiting_approval":
                raise ValueError("task is no longer waiting for approval")
            call = (
                self._db()
                .execute("SELECT arguments_json FROM tool_calls WHERE id=?", (item["tool_call_id"],))
                .fetchone()
            )
            current_hash = (
                "" if call is None else hashlib.sha256(str(call["arguments_json"]).encode("utf-8")).hexdigest()
            )
            if not hmac.compare_digest(str(item["arguments_hash"]), current_hash):
                self._db().execute(
                    "UPDATE approvals SET status='rejected',decided_at=?,version=version+1 WHERE id=?",
                    (now.isoformat(), approval_id),
                )
                self._db().execute(
                    "UPDATE tool_calls SET status='denied',updated_at=? WHERE id=?",
                    (now.isoformat(), item["tool_call_id"]),
                )
                self._db().execute(
                    """UPDATE tasks SET status='failed',last_error='Approval arguments changed',
                       version=version+1,updated_at=? WHERE id=?""",
                    (now.isoformat(), item["task_id"]),
                )
                self._db().commit()
                raise PermissionError("approval arguments no longer match the proposed tool call")
            if datetime.fromisoformat(item["expires_at"]) <= now:
                self._db().execute(
                    "UPDATE approvals SET status='expired',decided_at=?,version=version+1 WHERE id=?",
                    (now.isoformat(), approval_id),
                )
                self._db().execute(
                    "UPDATE tool_calls SET status='expired',updated_at=? WHERE id=?",
                    (now.isoformat(), item["tool_call_id"]),
                )
                self._db().execute(
                    """UPDATE tasks SET status='timed_out',last_error='Approval expired',
                       version=version+1,updated_at=? WHERE id=?""",
                    (now.isoformat(), item["task_id"]),
                )
                self._db().commit()
                raise ValueError("approval has expired")
            approval_status = "consumed" if decision == "allow" else "rejected"
            call_status = "running" if decision == "allow" else "denied"
            # A denial rejects this operation, not the user's whole turn.
            # LangGraph consumes the decision and either asks for the next
            # review or returns a rejected ToolMessage to the model.
            task_status = "running"
            with self._db() as db:
                db.execute(
                    """UPDATE approvals SET status=?,decided_at=?,note=?,version=version+1,
                       authorization_scope=?,authorization_expires_at=? WHERE id=?""",
                    (approval_status, now.isoformat(), note, scope,
                     (now + timedelta(hours=8)).isoformat() if scope != "once" else "", approval_id),
                )
                db.execute(
                    "UPDATE tool_calls SET status=?,updated_at=? WHERE id=?",
                    (call_status, now.isoformat(), item["tool_call_id"]),
                )
                db.execute(
                    "UPDATE tasks SET status=?,version=version+1,updated_at=? WHERE id=?",
                    (task_status, now.isoformat(), item["task_id"]),
                )
                # Decision and audit commit (or roll back) together.
                db.execute(
                    "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
                    (f"audit_{uuid.uuid4().hex}", workspace_id, principal_id, "approval.decided",
                     approval_id, "approved" if decision == "allow" else "denied",
                     json.dumps({"tool_call_id": item["tool_call_id"], "note": note,
                                 "version": version, "arguments_hash": item["arguments_hash"],
                                 "authorization_scope": scope, "reuse_scope": reuse if scope != "once" else {}},
                                ensure_ascii=False), now.isoformat()),
                )
        return await self.get_approval(workspace_id, approval_id)

    @staticmethod
    def _require_status(status: str, allowed: set[str], resource: str) -> None:
        if status not in allowed:
            raise ValueError(f"invalid {resource} status: {status}")

    async def list_todos(self, workspace_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            rows = (
                self._db()
                .execute("SELECT * FROM todos WHERE workspace_id=? ORDER BY created_at,id", (workspace_id,))
                .fetchall()
            )
        return [{**dict(row), "done": bool(row["done"])} for row in rows]

    async def add_todo(self, workspace_id: str, text: str) -> list[dict[str, Any]]:
        now = _now()
        async with self._lock:
            self._db().execute(
                "INSERT INTO todos VALUES(?,?,?,?,?,?)",
                (f"todo_{uuid.uuid4().hex}", workspace_id, text, 0, now, now),
            )
            self._db().commit()
        return await self.list_todos(workspace_id)

    async def update_todo(
        self, workspace_id: str, todo_id: str, text: str | None = None, done: bool | None = None
    ) -> list[dict[str, Any]]:
        updates: list[str] = []
        params: list[Any] = []
        if text is not None:
            updates.append("text=?")
            params.append(text)
        if done is not None:
            updates.append("done=?")
            params.append(1 if done else 0)
        if not updates:
            raise ValueError("text or done is required for update")
        updates.append("updated_at=?")
        params.extend([_now(), workspace_id, todo_id])
        async with self._lock:
            cursor = self._db().execute(f"UPDATE todos SET {','.join(updates)} WHERE workspace_id=? AND id=?", params)
            self._db().commit()
        if cursor.rowcount == 0:
            raise KeyError(todo_id)
        return await self.list_todos(workspace_id)

    async def clear_todos(self, workspace_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            self._db().execute("DELETE FROM todos WHERE workspace_id=?", (workspace_id,))
            self._db().commit()
        return []

    async def get_user_memory(self, workspace_id: str, subject_user_id: str, memory_id: str) -> dict[str, Any]:
        async with self._lock:
            row = (
                self._db()
                .execute(
                    """SELECT * FROM user_memories
                   WHERE workspace_id=? AND subject_user_id=? AND id=?""",
                    (workspace_id, subject_user_id, memory_id),
                )
                .fetchone()
            )
        if row is None:
            raise KeyError(memory_id)
        return dict(row)

    async def list_user_memories(
        self, workspace_id: str, subject_user_id: str, query: str = "", limit: int = 100,
        *, folder_scope: str | None = None,
    ) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 500)
        sql = """SELECT * FROM user_memories
                 WHERE workspace_id=? AND subject_user_id=? AND folder_scope IS ?"""
        params: list[Any] = [workspace_id, subject_user_id, folder_scope]
        normalized_query = query.strip().lower()
        if normalized_query:
            escaped = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            sql += " AND lower(content) LIKE ? ESCAPE '\\'"
            params.append(f"%{escaped}%")
        sql += " ORDER BY updated_at DESC,id LIMIT ?"
        params.append(limit)
        async with self._lock:
            rows = self._db().execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    async def prompt_memories(
        self, workspace_id: str, subject_user_id: str, conversation_id: str, root_path: str,
    ) -> list[dict[str, Any]]:
        """Read both opt-ins and current folder facts under one product snapshot lock."""
        async with self._lock:
            db = self._db()
            conversation = db.execute(
                "SELECT folder_id,memory_enabled FROM conversations WHERE workspace_id=? AND id=?",
                (workspace_id, conversation_id),
            ).fetchone()
            if conversation is None or not conversation["memory_enabled"]:
                return []
            folder_id = str(conversation["folder_id"])
            if folder_id:
                folder = db.execute("SELECT root_path FROM workspace_folders WHERE workspace_id=? AND id=?",
                                    (workspace_id, folder_id)).fetchone()
                if folder is None or folder["root_path"] != root_path:
                    return []
            scope = folder_memory_scope(folder_id, root_path)
            setting = db.execute(
                "SELECT value_json FROM user_preferences WHERE workspace_id=? AND subject_user_id=? AND key=?",
                (workspace_id, subject_user_id, "memory.enabled." + scope),
            ).fetchone()
            if setting is None or json.loads(setting[0]) is not True:
                return []
            rows = db.execute(
                """SELECT id,category,content FROM user_memories
                   WHERE workspace_id=? AND subject_user_id=? AND folder_scope=? AND source_type='user'
                   ORDER BY CASE WHEN category IN ('preference','constraint') THEN 0 ELSE 1 END,
                            updated_at DESC,id LIMIT 100""",
                (workspace_id, subject_user_id, scope),
            ).fetchall()
            return [dict(row) for row in rows]

    async def set_memory_enabled(self, workspace_id: str, user_id: str, scope: str, enabled: bool) -> None:
        async with self._lock:
            with self._db() as db:
                now = _now()
                db.execute(
                    """INSERT INTO user_preferences VALUES(?,?,?,?,?)
                       ON CONFLICT(workspace_id,subject_user_id,key)
                       DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                    (workspace_id, user_id, "memory.enabled." + scope, json.dumps(enabled), now),
                )
                db.execute("INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
                           (f"audit_{uuid.uuid4().hex}", workspace_id, user_id, "memory.scope.updated", scope,
                            "success", json.dumps({"enabled": enabled}), now))

    async def upsert_user_memory(
        self,
        workspace_id: str,
        subject_user_id: str,
        *,
        memory_id: str = "",
        category: str,
        content: str,
        source_type: str = "user",
        source_id: str = "",
        confidence: float = 1.0,
        folder_scope: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        creating = not memory_id
        if creating:
            memory_id = f"memory_{uuid.uuid4().hex}"
        async with self._lock:
            try:
                if creating:
                    self._db().execute(
                        """INSERT INTO user_memories(
                             id,workspace_id,subject_user_id,category,content,source_type,
                             source_id,confidence,created_at,updated_at,folder_scope
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            memory_id,
                            workspace_id,
                            subject_user_id,
                            category,
                            content,
                            source_type,
                            source_id,
                            confidence,
                            now,
                            now,
                            folder_scope,
                        ),
                    )
                else:
                    cursor = self._db().execute(
                        """UPDATE user_memories
                           SET category=?,content=?,confidence=?,updated_at=?,folder_scope=COALESCE(?,folder_scope)
                           WHERE workspace_id=? AND subject_user_id=? AND id=?""",
                        (
                            category,
                            content,
                            confidence,
                            now,
                            folder_scope,
                            workspace_id,
                            subject_user_id,
                            memory_id,
                        ),
                    )
                    if cursor.rowcount == 0:
                        raise KeyError(memory_id)
                self._db().execute(
                    "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
                    (
                        f"audit_{uuid.uuid4().hex}",
                        workspace_id,
                        subject_user_id,
                        "memory.created" if creating else "memory.updated",
                        memory_id,
                        "success",
                        json.dumps({"category": category, "folder_scope": folder_scope}, ensure_ascii=False),
                        now,
                    ),
                )
                self._db().commit()
            except Exception:
                self._db().rollback()
                raise
        return await self.get_user_memory(workspace_id, subject_user_id, memory_id)

    async def delete_user_memory(self, workspace_id: str, subject_user_id: str, memory_id: str) -> None:
        async with self._lock:
            try:
                cursor = self._db().execute(
                    """DELETE FROM user_memories
                       WHERE workspace_id=? AND subject_user_id=? AND id=?""",
                    (workspace_id, subject_user_id, memory_id),
                )
                if cursor.rowcount == 0:
                    raise KeyError(memory_id)
                self._db().execute(
                    "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",
                    (
                        f"audit_{uuid.uuid4().hex}",
                        workspace_id,
                        subject_user_id,
                        "memory.deleted",
                        memory_id,
                        "success",
                        "{}",
                        _now(),
                    ),
                )
                self._db().commit()
            except Exception:
                self._db().rollback()
                raise

    async def get_workspace_data_summary(self, workspace_id: str) -> dict[str, Any]:
        queries = {
            "conversation_count": "SELECT COUNT(*) FROM conversations WHERE workspace_id=?",
            "message_count": """SELECT COUNT(*) FROM messages m
                                JOIN conversations c ON c.id=m.conversation_id
                                WHERE c.workspace_id=?""",
            "memory_count": "SELECT COUNT(*) FROM user_memories WHERE workspace_id=?",
            "task_count": "SELECT COUNT(*) FROM tasks WHERE workspace_id=?",
            "audit_event_count": "SELECT COUNT(*) FROM audit_events WHERE workspace_id=?",
        }
        async with self._lock:
            result = {
                name: int(self._db().execute(query, (workspace_id,)).fetchone()[0]) for name, query in queries.items()
            }
        return {"storage_backend": "sqlite", **result}
