"""Bridge 数据库核心 — SQLite schema、迁移、CRUD、单实例锁。

设计参考：docs/bridge-design/09-database-events-and-config.md
"""

import os
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

# ── SQL DDL ───────────────────────────────────────────────

SCHEMA_SQL = """
-- 项目表
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

-- Agent 档案表（动态数量，2-8 推荐）
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

-- 目标表
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

-- 任务表（核心实体，含乐观并发 version）
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

-- 任务依赖表
CREATE TABLE IF NOT EXISTS task_dependencies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    dependency_type TEXT NOT NULL DEFAULT 'blocks',
    UNIQUE(task_id, depends_on_task_id)
);

-- Attempt 表
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

-- 租约表
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

-- 工作区表
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

-- 回执表
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

-- 验证表
CREATE TABLE IF NOT EXISTS validations (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
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

-- 审查表
CREATE TABLE IF NOT EXISTS reviews (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    reviewer_agent_id TEXT NOT NULL REFERENCES agent_profiles(id),
    verdict         TEXT NOT NULL DEFAULT 'pending',
    issues_json     TEXT DEFAULT '[]',
    fix_task_id     TEXT REFERENCES tasks(id),
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);

-- 合并队列表
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

-- 成本记录表
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

-- 审计事件表（不可变 — 只 INSERT，不 UPDATE/DELETE）
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

-- 操作日志表（Git 操作一致性）
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

-- Schema 版本表
CREATE TABLE IF NOT EXISTS schema_version (
    version         INTEGER PRIMARY KEY,
    applied_at      TEXT NOT NULL
);

-- 原子任务计数器表
CREATE TABLE IF NOT EXISTS task_counter (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    next_number     INTEGER NOT NULL DEFAULT 1
);

-- 索引
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
    """数据库操作错误"""
    pass


class Database:
    """SQLite 数据库封装 — 权威状态源。"""

    def __init__(self, path: str):
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._lock_fd = None

    # ── Connection Management ─────────────────────────────

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._connect()
        return self._conn

    def _connect(self):
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

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
        """初始化数据库：创建表 → 检查版本兼容性 → 记录 schema 版本。"""
        with self._lock:
            # 先创建表（幂等），确保 schema_version 表存在
            self.conn.executescript(SCHEMA_SQL)

            # 检查是否已有更高版本的 schema
            existing = self.conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if existing and existing["version"] > SCHEMA_VERSION:
                if not allow_future_schema:
                    raise DatabaseError(
                        f"Database schema version {existing['version']} is newer than "
                        f"supported version {SCHEMA_VERSION}. Please upgrade Bridge."
                    )

            # 初始化原子任务计数器
            self.conn.execute(
                "INSERT OR IGNORE INTO task_counter (id, next_number) VALUES (1, 1)"
            )

            if existing is None:
                self.conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, _utcnow()),
                )
            self.conn.commit()

    def get_schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] if row and row[0] else 0

    def list_tables(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    # ── Single-Instance Lock ──────────────────────────────

    def acquire_lock(self) -> bool:
        """尝试获取单实例锁（排他文件锁）。"""
        lock_dir = os.path.dirname(os.path.abspath(self.path))
        lock_path = os.path.join(lock_dir, "bridge.lock")
        try:
            # 使用 O_CREAT | O_EXCL 确保原子排他创建
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
        # 从 goal 推断 project_id
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
        """更新任务状态（乐观并发控制）。不自动 commit，由调用方控制事务。"""
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
    ):
        """原子：状态更新 + 事件写入在同一事务中。"""
        try:
            self.update_task_state(task_id, from_state, to_state, expected_version)
            self._write_event_in_tx(
                event_type=event_type, actor_type=actor_type, actor_id=actor_id,
                project_id=project_id, task_id=task_id,
                payload={"from": from_state, "to": to_state},
            )
            self.conn.commit()
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
        """获取指定项目的所有任务"""
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY task_number",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Events ────────────────────────────────────────────

    def _write_event_in_tx(self, **kwargs):
        """在已有事务中写入事件（不 commit）。"""
        eid = _new_id("evt")
        now = _utcnow()
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
                kwargs.get("project_id"),
                kwargs.get("task_id"),
                json.dumps(kwargs.get("payload", {})),
                kwargs.get("correlation_id"),
                now,
            ),
        )
        return eid

    def write_event(self, **kwargs):
        """写入事件并提交。"""
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

    # ═══════════════════════════════════════════════════════
    # 运行时表持久化 (leases, reviews, merge_queue, workspaces, operations)
    # ═══════════════════════════════════════════════════════

    # ── Leases ────────────────────────────────────────────

    def create_lease(self, lease_id: str, task_id: str, agent_id: str,
                     attempt_id: str = "", resource_type: str = "task",
                     resource_path: str = "", ttl_seconds: int = 900) -> int:
        now = _utcnow()
        from datetime import timedelta
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        # 空 attempt_id 使用 NULL 避免外键约束失败
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

    def create_review(self, review_id: str, task_id: str, reviewer_agent_id: str) -> int:
        now = _utcnow()
        self.conn.execute(
            """INSERT INTO reviews (id, task_id, reviewer_agent_id, verdict, created_at)
               VALUES (?,?,?,'pending',?)""",
            (review_id, task_id, reviewer_agent_id, now),
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
    """生成唯一 ID：{prefix}-{random_hex}"""
    import secrets
    return f"{prefix}-{secrets.token_hex(6)}"


def _new_task_id(conn: sqlite3.Connection) -> str:
    """生成任务 ID：TASK-{递增编号}（原子递增，使用 task_counter 表）"""
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
    """将数据库迁移到最新版本。Phase 1 仅创建新库。"""
    db = Database(path)
    db.initialize()
    db.close()


def init_database(path: str) -> Database:
    """初始化并返回数据库实例。"""
    db = Database(path)
    db.initialize()
    return db
