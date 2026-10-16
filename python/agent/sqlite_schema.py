"""SQLite table creation and additive upgrades, using Repository's connection.

Schema setup runs before product operations. The caller owns connection lifetime
and commits; business transactions remain in SQLiteRepository.
"""

from __future__ import annotations

import sqlite3


def initialize_schema(connection: sqlite3.Connection) -> None:
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
    message_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(messages)").fetchall()}
    if "attachments_json" not in message_columns:
        connection.execute("ALTER TABLE messages ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]'")
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
