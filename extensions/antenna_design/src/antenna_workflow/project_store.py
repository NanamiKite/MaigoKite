"""Persist solver admission charges independently of chat turns and worker lifetimes."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field

from .contracts import Contract, EngineeringRequest
from .mcp_resources import RUN_ID_PATTERN, ArtifactUnavailableError, run_path
from .workflow import WorkflowState

DEFAULT_SOLVE_LIMIT = 3
MAX_SOLVE_LIMIT = 1_000_000


class AdmissionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RunReservation(Contract):
    sequence: int
    run_id: str
    operation: Literal["solve", "render", "imported"]
    input_sha256: str | None
    source_run_id: str | None
    charged_attempts: Literal[0, 1]
    created_at: str


class ProjectSnapshot(Contract):
    project_id: str
    solve_limit: int
    reserved_attempts: int
    remaining_attempts: int
    total_runs: int
    next_before_sequence: int | None
    reservations: tuple[RunReservation, ...]


class StatusInput(Contract):
    before_sequence: int | None = Field(default=None, ge=1)
    limit: int = Field(default=10, ge=1, le=20)


class ProjectStore:
    """One operator-owned result root is one budget; artifacts own completion, not this ledger."""

    def __init__(self, root: Path, solve_limit: int | None = None) -> None:
        if solve_limit is not None and not 1 <= solve_limit <= MAX_SOLVE_LIMIT:
            raise ValueError("Invalid solver attempt limit")
        self.root = root
        self.path = root / ".antenna-project.sqlite3"
        self._initialize(solve_limit)

    def _connect(self) -> sqlite3.Connection:
        # SQLite opens sidecars itself. Refuse existing links/special files in this
        # trusted local directory; this is not isolation from another host process.
        for suffix in ("", "-journal", "-wal", "-shm"):
            path = Path(f"{self.path}{suffix}")
            if path.is_symlink() or path.is_junction():
                raise ArtifactUnavailableError("Linked project database")
            if path.exists():
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ArtifactUnavailableError("Project database must be an unlinked regular file")
        connection = sqlite3.connect(self.path, timeout=1)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self, solve_limit: int | None) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if schema_version not in (0, 1, 2):
                raise ValueError("Unsupported antenna project database version")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS project ("
                "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                "project_id TEXT NOT NULL, solve_limit INTEGER NOT NULL CHECK (solve_limit > 0))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS reservations ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL UNIQUE, "
                "operation TEXT NOT NULL CHECK (operation IN ('solve', 'render', 'imported')), "
                "input_sha256 TEXT, source_run_id TEXT, "
                "charged_attempts INTEGER NOT NULL CHECK (charged_attempts IN (0, 1)), "
                "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS budget_changes ("
                "sequence INTEGER PRIMARY KEY, previous_limit INTEGER, new_limit INTEGER NOT NULL, "
                "source TEXT NOT NULL CHECK (source IN ('initial_default', 'operator_startup')), "
                "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"
            )
            project = connection.execute("SELECT solve_limit FROM project WHERE singleton = 1").fetchone()
            previous = int(project[0]) if project is not None else None
            chosen = solve_limit if solve_limit is not None else previous or DEFAULT_SOLVE_LIMIT
            if previous is None:
                connection.execute("INSERT INTO project VALUES (1, ?, ?)", (uuid4().hex, chosen))
            elif chosen != previous:
                connection.execute("UPDATE project SET solve_limit = ? WHERE singleton = 1", (chosen,))
            if chosen != previous:
                connection.execute(
                    "INSERT INTO budget_changes (previous_limit, new_limit, source) VALUES (?, ?, ?)",
                    (previous, chosen, "operator_startup" if solve_limit is not None else "initial_default"),
                )
            # Older bundles have no admission ledger. Charge each existing directory
            # conservatively, including old renders/partial evidence, rather than reset usage.
            for child in _legacy_runs(self.root):
                connection.execute(
                    "INSERT OR IGNORE INTO reservations (run_id, operation, charged_attempts) "
                    "VALUES (?, 'imported', 1)",
                    (child.name,),
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS workflows ("
                "run_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, state_json TEXT NOT NULL)"
            )
            connection.execute("PRAGMA user_version = 2")

    def get_workflow(self, run_id: str) -> WorkflowState | None:
        run_path(self.root, run_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT state_json, revision FROM workflows WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        state = WorkflowState.model_validate_json(row[0])
        if state.run_id != run_id or state.revision != row[1]:
            raise ValueError("Workflow identity mismatch")
        return state

    def save_workflow(self, state: WorkflowState, *, expected_revision: int | None) -> None:
        """Commit an evidence milestone once; concurrent writers cannot replace another revision."""
        state = WorkflowState.model_validate(state.model_dump())
        if state.revision != (0 if expected_revision is None else expected_revision + 1):
            raise ValueError("Invalid workflow revision")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state_json, revision FROM workflows WHERE run_id = ?", (state.run_id,)
            ).fetchone()
            if expected_revision is None:
                if row is not None:
                    raise AdmissionError("RUN_RESERVED", "Design already exists; inspect its state, do not replay it.")
                connection.execute(
                    "INSERT INTO workflows VALUES (?, ?, ?)",
                    (state.run_id, state.revision, state.model_dump_json()),
                )
            else:
                previous = WorkflowState.model_validate_json(row[0]) if row is not None else None
                if previous is not None and previous.revision != row[1]:
                    raise ValueError("Workflow revision mismatch")
                # Publication and recovery can observe the same completion concurrently.
                # Accept that identical commit, but never overwrite a different milestone.
                if previous == state:
                    return
                if (
                    previous is None
                    or previous.revision != expected_revision
                    or previous.input_sha256 != state.input_sha256
                    or previous.criteria_sha256 != state.criteria_sha256
                ):
                    raise ValueError("Workflow changed; reload its committed state")
                if expected_revision >= 1 and (
                    previous.physical_design_sha256 != state.physical_design_sha256
                    or previous.selected_run_ids != state.selected_run_ids
                ):
                    raise ValueError("Validation and delivery must preserve the selected design")
                if expected_revision >= 2 and previous.verification_sha256 != state.verification_sha256:
                    raise ValueError("Delivery must preserve the recorded validation evidence")
                updated = connection.execute(
                    "UPDATE workflows SET revision = ?, state_json = ? WHERE run_id = ? AND revision = ?",
                    (state.revision, state.model_dump_json(), state.run_id, expected_revision),
                )
                if updated.rowcount != 1:
                    raise ValueError("Workflow revision was not committed")

    def reserve_solve(self, run_id: str, model: EngineeringRequest) -> None:
        digest = hashlib.sha256(model.model_dump_json().encode()).hexdigest()
        self._reserve(run_id, "solve", digest=digest)

    def reserve_render(self, run_id: str, source_run_id: str) -> None:
        self._reserve(run_id, "render", source_run_id=source_run_id)

    def _reserve(
        self,
        run_id: str,
        operation: Literal["solve", "render"],
        *,
        digest: str | None = None,
        source_run_id: str | None = None,
    ) -> None:
        run_path(self.root, run_id)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT 1 FROM reservations WHERE run_id = ?", (run_id,)).fetchone()
            if existing is not None:
                raise AdmissionError("RUN_RESERVED", "This identifier was admitted before; do not replay it.")
            charged = int(operation == "solve")
            used = int(connection.execute("SELECT COALESCE(SUM(charged_attempts), 0) FROM reservations").fetchone()[0])
            limit = int(connection.execute("SELECT solve_limit FROM project WHERE singleton = 1").fetchone()[0])
            if charged and used >= limit:
                raise AdmissionError(
                    "SOLVE_BUDGET_EXHAUSTED",
                    "Project attempt budget is exhausted. Query project_status; no solve started.",
                )
            connection.execute(
                "INSERT INTO reservations (run_id, operation, input_sha256, source_run_id, charged_attempts) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, operation, digest, source_run_id, charged),
            )
        # Commit precedes directory creation and process launch. A crash at either
        # boundary keeps the charge, even if a solver never actually ran.

    def charged_search_attempts(self, search_id: str) -> int:
        """Read committed admissions even if a worker died before saving its progress receipt."""
        run_path(self.root, search_id)
        pattern = f"{search_id}-c[0-9][0-9][0-9][0-9]-f[1-6]"
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(charged_attempts), 0) FROM reservations WHERE run_id GLOB ?",
                (pattern,),
            ).fetchone()
        return int(row[0])

    def snapshot(self, query: StatusInput) -> ProjectSnapshot:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            project = connection.execute("SELECT project_id, solve_limit FROM project WHERE singleton = 1").fetchone()
            totals = connection.execute(
                "SELECT COALESCE(SUM(charged_attempts), 0), COUNT(*) FROM reservations"
            ).fetchone()
            rows = connection.execute(
                "SELECT * FROM reservations WHERE (? IS NULL OR sequence < ?) ORDER BY sequence DESC LIMIT ?",
                (query.before_sequence, query.before_sequence, query.limit + 1),
            ).fetchall()
        records = tuple(RunReservation.model_validate(dict(row)) for row in rows[: query.limit])
        limit, used = int(project["solve_limit"]), int(totals[0])
        return ProjectSnapshot(
            project_id=str(project["project_id"]),
            solve_limit=limit,
            reserved_attempts=used,
            remaining_attempts=max(0, limit - used),
            total_runs=int(totals[1]),
            next_before_sequence=records[-1].sequence if len(rows) > query.limit else None,
            reservations=records,
        )


def _legacy_runs(root: Path) -> list[Path]:
    """Only run-shaped directories belong to the legacy bundle migration."""
    children = []
    for child in root.iterdir():
        if re.fullmatch(RUN_ID_PATTERN, child.name) is not None:
            checked = run_path(root, child.name)
            if checked.is_dir():
                children.append(checked)
    return sorted(children)
