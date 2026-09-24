"""Bridge database core: SQLite schema, migrations, CRUD, and single-instance locking.

Design reference: docs/bridge-design/09-database-events-and-config.md
"""

import os
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

SCHEMA_VERSION = 2

# ── SQL DDL ───────────────────────────────────────────────

SCHEMA_SQL = """
-- Projects table
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    root_path       TEXT NOT NULL,
    default_branch  TEXT NOT NULL DEFAULT 'main',
    workspace_mode  TEXT NOT NULL DEFAULT 'per_task_worktree',
    progression_policy TEXT NOT NULL DEFAULT 'hybrid',
    confirmation_policy TEXT NOT NULL DEFAULT 'balanced',
    language        TEXT NOT NULL DEFAULT 'zh-CN',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Agent profiles table (dynamic count, 2-8 recommended)
CREATE TABLE IF NOT EXISTS agent_profiles (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    display_name    TEXT NOT NULL,
    provider        TEXT NOT NULL DEFAULT 'local-manual',
    model           TEXT NOT NULL DEFAULT '',
    adapter         TEXT NOT NULL DEFAULT 'GenericFileAgentAdapter',
    capability_tier TEXT NOT NULL DEFAULT 'standard',
    cost_tier       TEXT NOT NULL DEFAULT 'low',
    roles_json      TEXT NOT NULL DEFAULT '[]',
    strengths_json  TEXT NOT NULL DEFAULT '[]',
    permissions_json TEXT NOT NULL DEFAULT '{}',
    limits_json     TEXT NOT NULL DEFAULT '{}',
    workspace_settings_json TEXT NOT NULL DEFAULT '{}',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Goals table
CREATE TABLE IF NOT EXISTS goals (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'draft',
    planner_agent_id TEXT,
    approved_plan_version INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Tasks table (core entity with optimistic concurrency version)
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    task_number     INTEGER NOT NULL,
    goal_id         TEXT REFERENCES goals(id) ON DELETE SET NULL,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    goal            TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL DEFAULT 'draft',
    risk            TEXT NOT NULL DEFAULT 'low',
    complexity      TEXT NOT NULL DEFAULT 'low',
    cost_tier       TEXT NOT NULL DEFAULT 'low',
    owner_agent_id  TEXT REFERENCES agent_profiles(id),
    reviewer_agent_id TEXT REFERENCES agent_profiles(id),
    progression_policy TEXT DEFAULT NULL,
    approval_gate   TEXT DEFAULT NULL,
    allowed_paths_json TEXT NOT NULL DEFAULT '[]',
    forbidden_paths_json TEXT NOT NULL DEFAULT '[]',
    acceptance_criteria_json TEXT NOT NULL DEFAULT '[]',
    required_checks_json TEXT NOT NULL DEFAULT '[]',
    token_budget    INTEGER DEFAULT 50000,
    time_budget_seconds INTEGER DEFAULT 7200,
    retry_budget    INTEGER DEFAULT 2,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1
);

-- Task dependencies table
CREATE TABLE IF NOT EXISTS task_dependencies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    dependency_type TEXT NOT NULL DEFAULT 'blocks',
    UNIQUE(task_id, depends_on_task_id)
);

-- Attempts table
CREATE TABLE IF NOT EXISTS attempts (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt_number  INTEGER NOT NULL,
    agent_id        TEXT NOT NULL REFERENCES agent_profiles(id),
    lease_id        TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    context_summary TEXT DEFAULT '',
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT NOT NULL
);

-- Leases table
CREATE TABLE IF NOT EXISTS leases (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    agent_id        TEXT NOT NULL REFERENCES agent_profiles(id),
    attempt_id      TEXT REFERENCES attempts(id),
    resource_type   TEXT NOT NULL DEFAULT 'task',
    resource_path   TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    expires_at      TEXT NOT NULL,
    heartbeat_at    TEXT,
    revoked_reason  TEXT,
    created_at      TEXT NOT NULL
);

-- Workspaces table
CREATE TABLE IF NOT EXISTS workspaces (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    agent_id        TEXT NOT NULL REFERENCES agent_profiles(id),
    worktree_path   TEXT,
    branch          TEXT,
    base_commit     TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    cleaned_at      TEXT
);

-- Receipts table
CREATE TABLE IF NOT EXISTS receipts (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt_id      TEXT REFERENCES attempts(id),
    agent_id        TEXT NOT NULL REFERENCES agent_profiles(id),
    receipt_path    TEXT,
    content_hash    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'imported',
    parsed_json     TEXT DEFAULT '{}',
    import_result   TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);

-- Validations table
CREATE TABLE IF NOT EXISTS validations (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt_id      TEXT REFERENCES attempts(id),
    check_id        TEXT NOT NULL,
    command_json    TEXT NOT NULL DEFAULT '{}',
    exit_code       INTEGER,
    status          TEXT NOT NULL DEFAULT 'pending',
    output_summary  TEXT DEFAULT '',
    evidence_path   TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT NOT NULL
);

-- Reviews table
CREATE TABLE IF NOT EXISTS reviews (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt_id      TEXT REFERENCES attempts(id),
    reviewer_agent_id TEXT NOT NULL REFERENCES agent_profiles(id),
    verdict         TEXT NOT NULL DEFAULT 'pending',
    issues_json     TEXT DEFAULT '[]',
    fix_task_id     TEXT REFERENCES tasks(id),
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS safety_overrides (
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    action_id       TEXT NOT NULL,
    policy          TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY(project_id, action_id)
);

CREATE TABLE IF NOT EXISTS validation_commands (
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    check_id        TEXT NOT NULL,
    executable      TEXT NOT NULL,
    args_json       TEXT NOT NULL DEFAULT '[]',
    updated_at      TEXT NOT NULL,
    PRIMARY KEY(project_id, check_id)
);

-- Merge queue table
CREATE TABLE IF NOT EXISTS merge_queue (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    target_branch   TEXT NOT NULL,
    candidate_commit TEXT,
    queue_position  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'queued',
    result          TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    merged_at       TEXT
);

-- Cost records table
CREATE TABLE IF NOT EXISTS cost_records (
    id              TEXT PRIMARY KEY,
    task_id         TEXT REFERENCES tasks(id),
    agent_id        TEXT REFERENCES agent_profiles(id),
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    estimated_cost  REAL DEFAULT 0.0,
    is_estimated    INTEGER NOT NULL DEFAULT 1,
    source          TEXT DEFAULT 'manual',
    created_at      TEXT NOT NULL
);

-- Audit events table (immutable: INSERT only, no UPDATE/DELETE)
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,
    event_type      TEXT NOT NULL,
    actor_type      TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    project_id      TEXT,
    task_id         TEXT,
    payload_json    TEXT NOT NULL DEFAULT '{}',
    correlation_id  TEXT,
    created_at_utc  TEXT NOT NULL
);

-- Operations log table (Git operation consistency)
CREATE TABLE IF NOT EXISTS operations (
    id              TEXT PRIMARY KEY,
    op_type         TEXT NOT NULL,
    task_id         TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL DEFAULT '',
    target          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'prepared',
    result          TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);

-- Schema version table
CREATE TABLE IF NOT EXISTS schema_version (
    version         INTEGER PRIMARY KEY,
    applied_at      TEXT NOT NULL
);

-- Atomic task counter table
CREATE TABLE IF NOT EXISTS task_counter (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    next_number     INTEGER NOT NULL DEFAULT 1
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_goal ON tasks(goal_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_number ON tasks(task_number);
CREATE INDEX IF NOT EXISTS idx_attempts_task ON attempts(task_id);
CREATE INDEX IF NOT EXISTS idx_leases_task ON leases(task_id);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_receipts_task ON receipts(task_id);
CREATE INDEX IF NOT EXISTS idx_receipts_hash ON receipts(content_hash);
"""


class DatabaseError(Exception):
    """Database operation error."""
    pass


class Database:
    """SQLite database wrapper and authoritative state source."""

    def __init__(self, path: str):
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._lock_fd: TextIO | None = None

    # ── Connection Management ─────────────────────────────

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._connect()
        assert self._conn is not None
        return self._conn

    def _connect(self):
        # check_same_thread=False permits cross-thread use, such as by FileWatcher.
        # The caller is responsible for managing concurrent access.
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def get_thread_safe_connection(self) -> "Database":
        """Return a separate database instance for use by a background thread.

        P0 fix: The FileWatcher background thread needs a separate SQLite
        connection and cannot reuse the main thread's connection because
        check_same_thread=True would raise an error.
        """
        return Database(self.path)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def execute(self, sql: str, params=()):
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()

    # ── Initialization & Migration ────────────────────────

    def initialize(self, allow_future_schema: bool = False):
        """Initialize the database: create tables, migrate, check compatibility, and record the schema version."""
        with self._lock:
            # Create tables first (idempotently) to ensure schema_version exists.
            self.conn.executescript(SCHEMA_SQL)

            # ── Incremental migrations: add new columns to older databases ──
            try:
                self._migrate_add_attempt_id_to_validations()
                self._migrate_add_attempt_id_to_reviews()
            except DatabaseError:
                self.conn.rollback()
                raise
            except Exception as exc:
                self.conn.rollback()
                raise DatabaseError(f"Database migration failed: {exc}") from exc

            # Check whether a newer schema version already exists.
            existing = self.conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if existing and existing["version"] > SCHEMA_VERSION:
                if not allow_future_schema:
                    raise DatabaseError(
                        f"Database schema version {existing['version']} is newer than "
                        f"supported version {SCHEMA_VERSION}. Please upgrade Bridge."
                    )

            # Initialize the atomic task counter.
            self.conn.execute(
                "INSERT OR IGNORE INTO task_counter (id, next_number) VALUES (1, 1)"
            )

            if existing is None:
                self.conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, _utcnow()),
                )
            elif existing["version"] < SCHEMA_VERSION:
                self.conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, _utcnow()),
                )
            self.conn.commit()

    def get_schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] if row and row[0] else 0

    def _migrate_add_attempt_id_to_validations(self):
        """Incrementally add the attempt_id column to older validations tables."""
        try:
            cols = self.conn.execute("PRAGMA table_info(validations)").fetchall()
            col_names = {c["name"] for c in cols}
            if "attempt_id" not in col_names:
                self.conn.execute(
                    "ALTER TABLE validations ADD COLUMN attempt_id TEXT REFERENCES attempts(id)"
                )
                self.conn.commit()
        except Exception as exc:
            self.conn.rollback()
            raise DatabaseError(
                f"Failed to migrate validations.attempt_id: {exc}"
            ) from exc

    def _migrate_add_attempt_id_to_reviews(self):
        """Add attempt binding to databases created by older Bridge versions."""
        try:
            cols = self.conn.execute("PRAGMA table_info(reviews)").fetchall()
            col_names = {c["name"] for c in cols}
            if "attempt_id" not in col_names:
                self.conn.execute(
                    "ALTER TABLE reviews ADD COLUMN attempt_id TEXT REFERENCES attempts(id)"
                )
                self.conn.commit()
        except Exception as exc:
            self.conn.rollback()
            raise DatabaseError(
                f"Failed to migrate reviews.attempt_id: {exc}"
            ) from exc

    def list_tables(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    # ── Single-Instance Lock ──────────────────────────────

    def acquire_lock(self) -> bool:
        """Try to acquire the single-instance lock (an exclusive file lock)."""
        lock_dir = os.path.dirname(os.path.abspath(self.path))
        lock_path = os.path.join(lock_dir, "bridge.lock")
        try:
            # Use O_CREAT | O_EXCL to ensure atomic, exclusive creation.
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            self._lock_fd = os.fdopen(fd, "w")
            self._lock_fd.write(str(os.getpid()))
            self._lock_fd.flush()
            return True
        except FileExistsError:
            return False
        except Exception:
            return False

    def release_lock(self) -> bool:
        if self._lock_fd:
            self._lock_fd.close()
            self._lock_fd = None
            lock_path = os.path.join(
                os.path.dirname(os.path.abspath(self.path)), "bridge.lock"
            )
            try:
                os.remove(lock_path)
            except OSError:
                pass
            return True
        return False

    # ── Projects ──────────────────────────────────────────

    def create_project(self, **kwargs) -> str:
        pid = kwargs.get("id") or _new_id("proj")
        now = _utcnow()
        self.conn.execute(
            """INSERT INTO projects (id, name, root_path, default_branch,
               workspace_mode, progression_policy, confirmation_policy,
               language, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                kwargs.get("name", ""),
                kwargs.get("root_path", ""),
                kwargs.get("default_branch", "main"),
                kwargs.get("workspace_mode", "per_task_worktree"),
                kwargs.get("progression_policy", "hybrid"),
                kwargs.get("confirmation_policy", "balanced"),
                kwargs.get("language", "zh-CN"),
                now,
                now,
            ),
        )
        self.conn.commit()
        return pid

    def get_project(self, project_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_project(self, project_id: str, **kwargs):
        allowed = [
            "name", "root_path", "default_branch", "workspace_mode",
            "progression_policy", "confirmation_policy", "language",
        ]
        sets = [f"{k} = ?" for k in kwargs if k in allowed]
        if not sets:
            return
        sets.append("updated_at = ?")
        values = [kwargs[k] for k in kwargs if k in allowed] + [_utcnow(), project_id]
        self.conn.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", values
        )
        self.conn.commit()

    def list_projects(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def set_safety_override(self, project_id: str, action_id: str, policy: str):
        """Persist a project-scoped safety override."""
        self.conn.execute(
            """INSERT INTO safety_overrides (project_id, action_id, policy, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(project_id, action_id)
               DO UPDATE SET policy = excluded.policy, updated_at = excluded.updated_at""",
            (project_id, action_id, policy, _utcnow()),
        )
        self.conn.commit()

    def list_safety_overrides(self, project_id: str) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT action_id, policy FROM safety_overrides WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        return {r["action_id"]: r["policy"] for r in rows}

    def set_validation_command(self, project_id: str, check_id: str,
                               executable: str, args: list[str]):
        """Persist an immutable-by-id validation command for one project."""
        self.conn.execute(
            """INSERT INTO validation_commands
               (project_id, check_id, executable, args_json, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(project_id, check_id)
               DO UPDATE SET executable = excluded.executable,
                             args_json = excluded.args_json,
                             updated_at = excluded.updated_at""",
            (project_id, check_id, executable, json.dumps(args), _utcnow()),
        )
        self.conn.commit()

    def list_validation_commands(self, project_id: str) -> dict[str, dict]:
        rows = self.conn.execute(
            """SELECT check_id, executable, args_json
               FROM validation_commands WHERE project_id = ?""",
            (project_id,),
        ).fetchall()
        return {
            r["check_id"]: {
                "executable": r["executable"],
                "args": json.loads(r["args_json"] or "[]"),
            }
            for r in rows
        }

    # ── Agent Profiles ────────────────────────────────────

    def create_agent(self, **kwargs) -> str:
        aid = kwargs.get("id") or _new_id("agent")
        now = _utcnow()
        self.conn.execute(
            """INSERT INTO agent_profiles
               (id, project_id, display_name, provider, model, adapter,
                capability_tier, cost_tier, roles_json, strengths_json,
                permissions_json, limits_json, workspace_settings_json,
                enabled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                aid,
                kwargs.get("project_id", ""),
                kwargs.get("display_name", ""),
                kwargs.get("provider", "local-manual"),
                kwargs.get("model", ""),
                kwargs.get("adapter", "GenericFileAgentAdapter"),
                kwargs.get("capability_tier", "standard"),
                kwargs.get("cost_tier", "low"),
                json.dumps(kwargs.get("roles", [])),
                json.dumps(kwargs.get("strengths", [])),
                json.dumps(_permissions_json(kwargs)),
                json.dumps(kwargs.get("limits", {})),
                json.dumps(kwargs.get("workspace_settings", {})),
                1 if kwargs.get("enabled", True) else 0,
                now,
                now,
            ),
        )
        self.conn.commit()
        return aid

    def get_agent(self, agent_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM agent_profiles WHERE id = ?", (agent_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_agents(self, project_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM agent_profiles WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_agent(self, agent_id: str, **kwargs):
        allowed = [
            "display_name", "provider", "model", "adapter",
            "capability_tier", "cost_tier", "roles_json",
            "strengths_json", "permissions_json", "limits_json",
            "workspace_settings_json", "enabled",
        ]
        sets = [f"{k} = ?" for k in kwargs if k in allowed]
        if not sets:
            return
        sets.append("updated_at = ?")
        values = [kwargs[k] for k in kwargs if k in allowed] + [_utcnow(), agent_id]
        self.conn.execute(
            f"UPDATE agent_profiles SET {', '.join(sets)} WHERE id = ?", values
        )
        self.conn.commit()

    # ── Goals ─────────────────────────────────────────────

    def create_goal(self, **kwargs) -> str:
        gid = kwargs.get("id") or _new_id("goal")
        now = _utcnow()
        self.conn.execute(
            """INSERT INTO goals (id, project_id, title, description, status,
               planner_agent_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                gid,
                kwargs.get("project_id", ""),
                kwargs.get("title", ""),
                kwargs.get("description", ""),
                kwargs.get("status", "draft"),
                kwargs.get("planner_agent_id"),
                now,
                now,
            ),
        )
        self.conn.commit()
        return gid

    def get_goal(self, goal_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM goals WHERE id = ?", (goal_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── Tasks ─────────────────────────────────────────────

    def create_task(self, **kwargs) -> str:
        tid = kwargs.get("id") or _new_task_id(self.conn)
        now = _utcnow()

        project_id = kwargs.get("project_id", "")
        # Infer project_id from the goal.
        goal_id = kwargs.get("goal_id")
        if not project_id and goal_id:
            goal = self.get_goal(goal_id)
            if goal:
                project_id = goal["project_id"]

        self.conn.execute(
            """INSERT INTO tasks
               (id, task_number, goal_id, project_id, title, goal, state,
                risk, complexity, cost_tier, owner_agent_id, reviewer_agent_id,
                progression_policy, approval_gate,
                allowed_paths_json, forbidden_paths_json,
                acceptance_criteria_json, required_checks_json,
                token_budget, time_budget_seconds, retry_budget,
                created_at, updated_at, version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tid,
                _task_number_from_id(tid),
                goal_id,
                project_id,
                kwargs.get("title", ""),
                kwargs.get("goal", ""),
                kwargs.get("state", "draft"),
                kwargs.get("risk", "low"),
                kwargs.get("complexity", "low"),
                kwargs.get("cost_tier", "low"),
                kwargs.get("owner_agent_id"),
                kwargs.get("reviewer_agent_id"),
                kwargs.get("progression_policy"),
                kwargs.get("approval_gate"),
                json.dumps(kwargs.get("allowed_paths", [])),
                json.dumps(kwargs.get("forbidden_paths", [])),
                json.dumps(kwargs.get("acceptance_criteria", [])),
                json.dumps(kwargs.get("required_checks", [])),
                kwargs.get("token_budget", 50000),
                kwargs.get("time_budget_seconds", 7200),
                kwargs.get("retry_budget", 2),
                now,
                now,
                1,
            ),
        )
        self.conn.commit()
        return tid

    def get_task(self, task_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_task_state(
        self, task_id: str, from_state: str, to_state: str, expected_version: int
    ) -> bool:
        """Update task state with optimistic concurrency; the caller controls the transaction."""
        now = _utcnow()
        cur = self.conn.execute(
            "UPDATE tasks SET state = ?, updated_at = ?, version = version + 1 "
            "WHERE id = ? AND state = ? AND version = ?",
            (to_state, now, task_id, from_state, expected_version),
        )
        if cur.rowcount == 0:
            task = self.get_task(task_id)
            if task is None:
                raise DatabaseError(f"Task {task_id} not found")
            if task["state"] != from_state:
                raise DatabaseError(
                    f"State conflict: expected {from_state}, actual {task['state']}"
                )
            raise DatabaseError(
                f"Version conflict: expected {expected_version}, actual {task['version']}"
            )
        return True

    def update_task_state_with_event(
        self, task_id: str, from_state: str, to_state: str, expected_version: int,
        event_type: str, actor_type: str, actor_id: str, project_id: str = "",
        close_active_resources: bool = False, resource_reason: str = "",
    ):
        """Atomically update state/event and, optionally, close task resources.

        A terminal task transition must not be observable with an active attempt
        or lease.  Keeping the cleanup in this transaction prevents a crash
        between the state update and resource cleanup from leaking resources.
        """
        closed_attempt_ids: list[str] = []
        revoked_lease_ids: list[str] = []
        try:
            self.update_task_state(task_id, from_state, to_state, expected_version)

            if close_active_resources:
                now = _utcnow()
                closed_attempt_ids = [
                    row["id"]
                    for row in self.conn.execute(
                        "SELECT id FROM attempts "
                        "WHERE task_id = ? AND status = 'in_progress'",
                        (task_id,),
                    ).fetchall()
                ]
                if closed_attempt_ids:
                    self.conn.execute(
                        "UPDATE attempts SET status = 'completed', completed_at = ? "
                        "WHERE task_id = ? AND status = 'in_progress'",
                        (now, task_id),
                    )

                revoked_lease_ids = [
                    row["id"]
                    for row in self.conn.execute(
                        "SELECT id FROM leases "
                        "WHERE task_id = ? AND status = 'active'",
                        (task_id,),
                    ).fetchall()
                ]
                if revoked_lease_ids:
                    self.conn.execute(
                        "UPDATE leases SET status = 'revoked', revoked_reason = ?, "
                        "heartbeat_at = ? WHERE task_id = ? AND status = 'active'",
                        (resource_reason or "task completed", now, task_id),
                    )

            self._write_event_in_tx(
                event_type=event_type, actor_type=actor_type, actor_id=actor_id,
                project_id=project_id, task_id=task_id,
                payload={"from": from_state, "to": to_state},
            )
            self.conn.commit()
            return {
                "attempt_ids": closed_attempt_ids,
                "lease_ids": revoked_lease_ids,
            }
        except Exception:
            self.conn.rollback()
            raise

    def list_tasks(self, state: str | None = None, goal_id: str | None = None) -> list[dict]:
        if state:
            rows = self.conn.execute(
                "SELECT * FROM tasks WHERE state = ? ORDER BY task_number",
                (state,),
            ).fetchall()
        elif goal_id:
            rows = self.conn.execute(
                "SELECT * FROM tasks WHERE goal_id = ? ORDER BY task_number",
                (goal_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tasks ORDER BY task_number"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_project_tasks(self, project_id: str) -> list[dict]:
        """Return all tasks for the specified project."""
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY task_number",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Task Dependencies ─────────────────────────────────

    def add_task_dependency(self, task_id: str, depends_on_task_id: str,
                            dependency_type: str = "blocks") -> bool:
        """Add a dependency relationship: task_id depends on depends_on_task_id."""
        if task_id == depends_on_task_id:
            raise DatabaseError("A task cannot depend on itself")
        self.conn.execute(
            """INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id, dependency_type)
               VALUES (?, ?, ?)""",
            (task_id, depends_on_task_id, dependency_type),
        )
        self.conn.commit()
        return True

    def remove_task_dependency(self, task_id: str, depends_on_task_id: str) -> bool:
        """Remove a dependency relationship."""
        cur = self.conn.execute(
            "DELETE FROM task_dependencies WHERE task_id = ? AND depends_on_task_id = ?",
            (task_id, depends_on_task_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_task_dependencies(self, task_id: str) -> list[str]:
        """Return the IDs of tasks that task_id depends on."""
        rows = self.conn.execute(
            "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        return [r["depends_on_task_id"] for r in rows]

    def get_dependent_tasks(self, task_id: str) -> list[str]:
        """Return the IDs of tasks that depend on task_id."""
        rows = self.conn.execute(
            "SELECT task_id FROM task_dependencies WHERE depends_on_task_id = ?",
            (task_id,),
        ).fetchall()
        return [r["task_id"] for r in rows]

    # ── Events ────────────────────────────────────────────


    def _write_event_in_tx(self, **kwargs):
        """Write an event in the existing transaction without committing."""
        eid = _new_id("evt")
        now = _utcnow()
        task_id = kwargs.get("task_id")
        project_id = kwargs.get("project_id")
        # Task-scoped events must carry the task's project boundary even when
        # callers omit project_id.  This keeps audit/resource reads isolated
        # across projects and also repairs older call sites centrally.
        if not project_id and task_id:
            task_row = self.conn.execute(
                "SELECT project_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task_row:
                project_id = task_row["project_id"]
        self.conn.execute(
            """INSERT INTO events
               (event_id, event_type, actor_type, actor_id,
                project_id, task_id, payload_json, correlation_id, created_at_utc)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                eid,
                kwargs.get("event_type", ""),
                kwargs.get("actor_type", "system"),
                kwargs.get("actor_id", ""),
                project_id,
                task_id,
                json.dumps(kwargs.get("payload", {})),
                kwargs.get("correlation_id"),
                now,
            ),
        )
        return eid

    def write_event(self, **kwargs):
        """Write an event and commit it."""
        eid = self._write_event_in_tx(**kwargs)
        self.conn.commit()
        return eid

    def list_events(self, task_id: str | None = None, limit: int = 100) -> list[dict]:
        if task_id:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE task_id = ? ORDER BY id DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Validations ───────────────────────────────────────

    def list_validations_by_task(self, task_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM validations WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def record_validation(
        self,
        task_id: str,
        attempt_id: str = "",
        check_id: str = "validation",
        status: str = "passed",
        output_summary: str = "",
        exit_code: int = 0,
        validator: str = "",
        details: str = "",
    ) -> str:
        import uuid
        vid = f"val-{uuid.uuid4().hex[:12]}"
        now = _utcnow()
        summary = details or output_summary
        cid = validator or check_id
        self.conn.execute(
            """INSERT INTO validations (id, task_id, attempt_id, check_id, command_json, status, exit_code, output_summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (vid, task_id, attempt_id or None, cid, "[]", status, exit_code, summary, now),
        )
        self.conn.commit()
        return vid

    # ── Receipts ─────────────────────────────────────────

    def insert_receipt_if_absent(
        self,
        receipt_id: str,
        task_id: str,
        attempt_id: str | None,
        agent_id: str,
        receipt_path: str,
        content_hash: str,
        status: str,
        import_result: str,
        created_at: str,
    ) -> bool:
        """Insert one receipt atomically and return False for an existing ID/hash.

        Receipt files can be observed by multiple Bridge processes.  ``INSERT OR
        IGNORE`` makes the deterministic content-derived receipt ID idempotent;
        rollback guarantees a failed write never leaves a connection poisoned.
        """
        with self._lock:
            try:
                cur = self.conn.execute(
                    """INSERT OR IGNORE INTO receipts
                       (id, task_id, attempt_id, agent_id, receipt_path,
                        content_hash, status, import_result, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        receipt_id,
                        task_id,
                        attempt_id,
                        agent_id,
                        receipt_path,
                        content_hash,
                        status,
                        import_result,
                        created_at,
                    ),
                )
                self.conn.commit()
                return cur.rowcount == 1
            except Exception:
                self.conn.rollback()
                raise

    def list_receipts(self, task_id: str | None = None) -> list[dict]:
        if task_id:
            rows = self.conn.execute(
                "SELECT * FROM receipts WHERE task_id = ? ORDER BY created_at DESC",
                (task_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM receipts ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    # ═══════════════════════════════════════════════════════
    # Runtime table persistence (leases, reviews, merge_queue, workspaces, operations)
    # ═══════════════════════════════════════════════════════

    # ── Leases ────────────────────────────────────────────

    def create_lease(self, lease_id: str, task_id: str, agent_id: str,
                     attempt_id: str = "", resource_type: str = "task",
                     resource_path: str = "", ttl_seconds: int = 900) -> int:
        now = _utcnow()
        from datetime import timedelta
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        # Use NULL for an empty attempt_id to avoid a foreign key constraint failure.
        aid = attempt_id if attempt_id else None
        self.conn.execute(
            """INSERT INTO leases (id, task_id, agent_id, attempt_id,
               resource_type, resource_path, status, expires_at, created_at)
               VALUES (?,?,?,?,?,?,'active',?,?)""",
            (lease_id, task_id, agent_id, aid,
             resource_type, resource_path, expires, now),
        )
        self.conn.commit()
        return 1

    def update_lease_status(self, lease_id: str, status: str, reason: str = "") -> bool:
        now = _utcnow()
        cur = self.conn.execute(
            "UPDATE leases SET status = ?, revoked_reason = ?, heartbeat_at = ? WHERE id = ?",
            (status, reason, now, lease_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def renew_lease(self, lease_id: str, ttl_seconds: int = 900) -> bool:
        from datetime import timedelta
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        now = _utcnow()
        cur = self.conn.execute(
            "UPDATE leases SET expires_at = ?, heartbeat_at = ? WHERE id = ? AND status = 'active'",
            (expires, now, lease_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_lease(self, lease_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM leases WHERE id = ?", (lease_id,)).fetchone()
        return dict(row) if row else None

    def list_active_leases(self, project_id: str | None = None) -> list[dict]:
        now_utc = _utcnow()
        if project_id:
            rows = self.conn.execute(
                """SELECT l.* FROM leases l
                   JOIN tasks t ON l.task_id = t.id
                   WHERE l.status = 'active' AND l.expires_at > ?
                   AND t.project_id = ?""",
                (now_utc, project_id),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM leases WHERE status = 'active' AND expires_at > ?",
                (now_utc,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_leases_by_task(self, task_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM leases WHERE task_id = ?", (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_leases_by_agent(self, agent_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM leases WHERE agent_id = ? AND status = 'active'", (agent_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Reviews ───────────────────────────────────────────

    def create_review(self, review_id: str, task_id: str, reviewer_agent_id: str, attempt_id: str = "") -> int:
        now = _utcnow()
        aid = attempt_id if attempt_id else None
        self.conn.execute(
            """INSERT INTO reviews (id, task_id, attempt_id, reviewer_agent_id, verdict, created_at)
               VALUES (?,?,?,?,'pending',?)""",
            (review_id, task_id, aid, reviewer_agent_id, now),
        )
        self.conn.commit()
        return 1

    def update_review(self, review_id: str, verdict: str, issues_json: str = "[]",
                      fix_task_id: str | None = None) -> bool:
        now = _utcnow()
        cur = self.conn.execute(
            """UPDATE reviews SET verdict = ?, issues_json = ?, fix_task_id = ?,
               completed_at = ? WHERE id = ?""",
            (verdict, issues_json, fix_task_id, now, review_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_review(self, review_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
        return dict(row) if row else None

    def list_pending_reviews(self, project_id: str | None = None) -> list[dict]:
        if project_id:
            rows = self.conn.execute(
                """SELECT r.* FROM reviews r
                   JOIN tasks t ON r.task_id = t.id
                   WHERE r.verdict = 'pending' AND t.project_id = ?""",
                (project_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM reviews WHERE verdict = 'pending'"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_reviews_by_task(self, task_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM reviews WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Merge Queue ───────────────────────────────────────

    def create_merge_entry(self, entry_id: str, task_id: str, target_branch: str = "main",
                           candidate_commit: str = "", queue_position: int = 0) -> int:
        now = _utcnow()
        self.conn.execute(
            """INSERT INTO merge_queue (id, task_id, target_branch, candidate_commit,
               queue_position, status, created_at)
               VALUES (?,?,?,?,?,'queued',?)""",
            (entry_id, task_id, target_branch, candidate_commit, queue_position, now),
        )
        self.conn.commit()
        return 1

    def update_merge_entry(self, entry_id: str, status: str, result: str = "") -> bool:
        now = _utcnow()
        merged_at = now if status == 'merged' else None
        cur = self.conn.execute(
            """UPDATE merge_queue SET status = ?, result = ?,
               merged_at = COALESCE(?, merged_at) WHERE id = ?""",
            (status, result, merged_at, entry_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_merge_entry(self, entry_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM merge_queue WHERE id = ?", (entry_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_merge_entries(self, task_id: str | None = None,
                           project_id: str | None = None) -> list[dict]:
        if task_id:
            rows = self.conn.execute(
                "SELECT * FROM merge_queue WHERE task_id = ? ORDER BY queue_position",
                (task_id,),
            ).fetchall()
        elif project_id:
            rows = self.conn.execute(
                """SELECT mq.* FROM merge_queue mq
                   JOIN tasks t ON mq.task_id = t.id
                   WHERE t.project_id = ? ORDER BY mq.queue_position""",
                (project_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM merge_queue ORDER BY queue_position"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_queued_merges(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM merge_queue WHERE status = 'queued' ORDER BY queue_position"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Workspaces ────────────────────────────────────────

    def create_workspace(self, workspace_id: str, task_id: str, agent_id: str,
                         worktree_path: str = "", branch: str = "",
                         base_commit: str = "") -> int:
        now = _utcnow()
        self.conn.execute(
            """INSERT INTO workspaces (id, task_id, agent_id, worktree_path, branch,
               base_commit, status, created_at)
               VALUES (?,?,?,?,?,?,'active',?)""",
            (workspace_id, task_id, agent_id, worktree_path, branch, base_commit, now),
        )
        self.conn.commit()
        return 1

    def list_active_workspaces(self, project_id: str | None = None) -> list[dict]:
        if project_id:
            rows = self.conn.execute(
                """SELECT w.* FROM workspaces w
                   JOIN tasks t ON w.task_id = t.id
                   WHERE w.status = 'active' AND t.project_id = ?""",
                (project_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM workspaces WHERE status = 'active'"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Operations ────────────────────────────────────────

    def create_operation(self, op_id: str, op_type: str, task_id: str = "",
                         idempotency_key: str = "", target: str = "",
                         status: str = "prepared") -> int:
        now = _utcnow()
        self.conn.execute(
            """INSERT INTO operations (id, op_type, task_id, idempotency_key,
               target, status, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (op_id, op_type, task_id, idempotency_key, target, status, now),
        )
        self.conn.commit()
        return 1

    def update_operation(self, op_id: str, status: str, result: str = "") -> bool:
        now = _utcnow()
        cur = self.conn.execute(
            "UPDATE operations SET status = ?, result = ?, updated_at = ? WHERE id = ?",
            (status, result, now, op_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_incomplete_operations(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM operations WHERE status NOT IN ('completed', 'failed')"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Helpers ───────────────────────────────────────────────

_counter_lock = threading.Lock()
_counter_state: dict[str, int] = {}


def _new_id(prefix: str) -> str:
    """Generate a unique ID in the form {prefix}-{random_hex}."""
    import secrets
    return f"{prefix}-{secrets.token_hex(6)}"


def _new_task_id(conn: sqlite3.Connection) -> str:
    """Generate an atomically incremented TASK-{number} ID using task_counter."""
    cur = conn.execute("UPDATE task_counter SET next_number = next_number + 1 WHERE id = 1")
    if cur.rowcount == 0:
        conn.execute("INSERT OR IGNORE INTO task_counter (id, next_number) VALUES (1, 1)")
        conn.execute("UPDATE task_counter SET next_number = next_number + 1 WHERE id = 1")
    row = conn.execute("SELECT next_number FROM task_counter WHERE id = 1").fetchone()
    num = row[0] - 1
    return f"TASK-{num:03d}"


def _task_number_from_id(task_id: str) -> int:
    try:
        return int(task_id.split("-")[1])
    except (IndexError, ValueError):
        return 0


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _permissions_json(kwargs: dict) -> dict:
    return {
        "can_plan": kwargs.get("can_plan", False),
        "can_approve_plan": kwargs.get("can_approve_plan", False),
        "can_review": kwargs.get("can_review", False),
        "can_merge": kwargs.get("can_merge", False),
        "can_run_validation": kwargs.get("can_run_validation", False),
        "can_request_escalation": kwargs.get("can_request_escalation", False),
    }


# ── Module-Level Helpers ─────────────────────────────────

def migrate_database(path: str) -> None:
    """Migrate the database to the latest version; Phase 1 only creates new databases."""
    db = Database(path)
    db.initialize()
    db.close()


def init_database(path: str) -> Database:
    """Initialize and return a database instance."""
    db = Database(path)
    db.initialize()
    return db
