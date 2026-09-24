"""Focused regression tests for reliability and security follow-up fixes."""

import os
import subprocess
import io
import json
from pathlib import Path

import pytest

from bridgelib.coordinator import BridgeCoordinator
from bridgelib.database import Database, DatabaseError, init_database
from bridgelib.mcp_server import BridgeMCPServer
from bridgelib.state_machine import TaskState
from bridgelib.workspace import workspace_branch_name, workspace_dir_name


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=cwd, text=True, stderr=subprocess.STDOUT,
    ).strip()


@pytest.fixture
def git_coord(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.name", "Bridge Test"], repo)
    _git(["config", "user.email", "bridge@test.local"], repo)
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    _git(["add", "README.md"], repo)
    _git(["commit", "-m", "initial"], repo)

    db = init_database(str(tmp_path / "bridge.db"))
    coordinator = BridgeCoordinator(db)
    project_id = coordinator.init_project("Test", str(repo))
    goal_id = coordinator.create_goal(project_id, "Goal")
    agent_id = coordinator.add_agent(project_id, "worker", id="worker")
    yield coordinator, project_id, goal_id, agent_id, repo
    coordinator.close()


def test_workspace_names_are_project_scoped():
    first = workspace_dir_name("agent", "task", project_id="project-one")
    second = workspace_dir_name("agent", "task", project_id="project-two")
    first_branch = workspace_branch_name("agent", "task", 1, project_id="project-one")
    second_branch = workspace_branch_name("agent", "task", 1, project_id="project-two")

    assert first != second
    assert first_branch != second_branch
    assert all(part not in first for part in ("..", "\\"))


def test_worktree_does_not_copy_sensitive_configs_by_default(git_coord):
    coordinator, _project_id, goal_id, agent_id, repo = git_coord
    (repo / ".env").write_text("SECRET=do-not-copy\n", encoding="utf-8")
    (repo / ".env.local").write_text("LOCAL_SECRET=do-not-copy\n", encoding="utf-8")
    (repo / ".npmrc").write_text("//registry.example/:_authToken=secret\n", encoding="utf-8")
    (repo / ".env.example").write_text("SECRET=replace-me\n", encoding="utf-8")

    task_id = coordinator.create_task(
        goal_id,
        "Secure worktree",
        allowed_paths=["src/**"],
        acceptance_criteria=["done"],
    )
    coordinator.transition_task(task_id, TaskState.PLANNING)
    coordinator.transition_task(task_id, TaskState.READY, confirmed=True)
    coordinator.assign_task(task_id, agent_id)
    worktree = coordinator.create_worktree(task_id, agent_id, confirmed=True)
    worktree_path = Path(worktree["worktree_path"])

    assert not (worktree_path / ".env").exists()
    assert not (worktree_path / ".env.local").exists()
    assert not (worktree_path / ".npmrc").exists()
    assert (worktree_path / ".env.example").exists()


def test_worktree_sensitive_config_requires_explicit_opt_in(git_coord):
    coordinator, _project_id, goal_id, agent_id, repo = git_coord
    (repo / ".env").write_text("SECRET=copy-only-with-opt-in\n", encoding="utf-8")

    task_id = coordinator.create_task(
        goal_id,
        "Opt-in worktree",
        allowed_paths=["src/**"],
        acceptance_criteria=["done"],
    )
    coordinator.transition_task(task_id, TaskState.PLANNING)
    coordinator.transition_task(task_id, TaskState.READY, confirmed=True)
    coordinator.assign_task(task_id, agent_id)
    worktree = coordinator.create_worktree(
        task_id, agent_id, confirmed=True, inherit_config=True,
    )

    assert (Path(worktree["worktree_path"]) / ".env").read_text(encoding="utf-8") == (
        "SECRET=copy-only-with-opt-in\n"
    )


def test_coordinator_close_stops_watcher_and_releases_lock(git_coord, tmp_path):
    coordinator, _project_id, _goal_id, _agent_id, _repo = git_coord
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    assert coordinator.db.acquire_lock() is True
    coordinator.start_receipt_watcher(
        str(task_dir), "TASK-1", 1, "lease-1", "worker",
    )
    first_watcher = coordinator._receipt_watcher
    first_db = coordinator._receipt_watcher_db

    coordinator.stop_receipt_watcher()
    assert coordinator._receipt_watcher is None
    assert coordinator._receipt_watcher_db is None
    assert first_watcher.watcher._running is False

    coordinator.start_receipt_watcher(
        str(task_dir), "TASK-1", 1, "lease-1", "worker",
    )
    assert coordinator._receipt_watcher is not first_watcher
    assert coordinator._receipt_watcher_db is not first_db
    coordinator.close()
    assert coordinator.db._conn is None
    assert coordinator.db.acquire_lock() is True
    coordinator.db.release_lock()


def test_mcp_summary_includes_queued_merge_count(git_coord):
    coordinator, project_id, goal_id, _agent_id, _repo = git_coord
    task_id = coordinator.create_task(
        goal_id, "Queued", allowed_paths=["src/**"], acceptance_criteria=["done"],
    )
    coordinator.db.conn.execute(
        "INSERT INTO merge_queue (id, task_id, target_branch, queue_position, status, created_at) "
        "VALUES (?, ?, 'main', 1, 'queued', datetime('now'))",
        ("mq-queued", task_id),
    )
    coordinator.db.conn.commit()
    server = BridgeMCPServer(coordinator, default_project_id=project_id)

    result = server._handle_resources_read("bridge://project/summary")
    payload = __import__("json").loads(result["contents"][0]["text"])
    assert payload["queued_merges"] == 1


def test_dependencies_cannot_cross_project_boundaries(git_coord, tmp_path):
    coordinator, project_id, goal_id, _agent_id, repo = git_coord
    other_project_id = coordinator.init_project("Other", str(repo))
    other_goal_id = coordinator.create_goal(other_project_id, "Other goal")
    first_task = coordinator.create_task(
        goal_id, "Local", allowed_paths=["src/**"], acceptance_criteria=["done"],
    )
    other_task = coordinator.create_task(
        other_goal_id, "Foreign", allowed_paths=["src/**"], acceptance_criteria=["done"],
    )

    with pytest.raises(Exception, match="different projects"):
        coordinator.add_task_dependency(first_task, other_task)


def test_database_migration_failure_aborts_initialization(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "migration.db"))

    def fail_migration():
        raise RuntimeError("migration failed")

    monkeypatch.setattr(db, "_migrate_add_attempt_id_to_validations", fail_migration)
    with pytest.raises(DatabaseError, match="migration failed"):
        db.initialize()
    db.close()


def test_mcp_validation_requires_current_agent(git_coord):
    coordinator, project_id, goal_id, agent_id, _repo = git_coord
    task_id = coordinator.create_task(
        goal_id, "Validate", allowed_paths=["src/**"], acceptance_criteria=["done"],
    )
    server = BridgeMCPServer(coordinator, default_project_id=project_id)
    server._handle_claim_task({"task_id": task_id, "agent_id": agent_id})

    with pytest.raises(Exception, match="authorized|active attempt|lease"):
        server._handle_run_validation({"task_id": task_id, "agent_id": "intruder"})


def test_git_diff_validation_fails_closed_on_adapter_error(git_coord):
    coordinator, project_id, _goal_id, _agent_id, _repo = git_coord
    coordinator.db.update_project(project_id, root_path=str(Path("missing-repository")))

    issues = coordinator._cross_validate_git_diff(
        project_id, "base", "submission", ["src/example.py"],
    )

    assert issues
    assert any("cross-validate" in issue.lower() or "repository" in issue.lower() for issue in issues)


def test_record_task_cost_persists_estimated_flag(git_coord):
    coordinator, _project_id, goal_id, agent_id, _repo = git_coord
    task_id = coordinator.create_task(
        goal_id, "Cost", allowed_paths=["src/**"], acceptance_criteria=["done"],
    )
    coordinator.record_task_cost(
        task_id, agent_id, input_tokens=10, output_tokens=5,
        estimated_cost=0.25, source="receipt", is_estimated=False,
    )

    row = coordinator.db.conn.execute(
        "SELECT is_estimated, source FROM cost_records WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    assert row["is_estimated"] == 0
    assert row["source"] == "receipt"


def test_record_task_cost_rolls_back_memory_when_database_write_fails(git_coord):
    coordinator, _project_id, goal_id, agent_id, _repo = git_coord
    task_id = coordinator.create_task(
        goal_id, "Cost rollback", allowed_paths=["src/**"], acceptance_criteria=["done"],
    )

    original_conn = coordinator.db._conn

    class FailingConnection:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("database write failed")

        def commit(self):
            raise RuntimeError("database commit failed")

    coordinator.db._conn = FailingConnection()
    try:
        with pytest.raises(RuntimeError, match="database write failed"):
            coordinator.record_task_cost(task_id, agent_id, input_tokens=10)
    finally:
        coordinator.db._conn = original_conn

    assert coordinator.costs.records_by_task(task_id) == []


def test_standalone_receipt_import_keeps_estimated_fallback(git_coord, tmp_path):
    coordinator, _project_id, goal_id, agent_id, _repo = git_coord
    task_id = coordinator.create_task(
        goal_id, "Receipt fallback", allowed_paths=["src/**"],
        acceptance_criteria=["done"],
    )
    coordinator.transition_task(task_id, TaskState.PLANNING)
    coordinator.transition_task(task_id, TaskState.READY, confirmed=True)
    coordinator.assign_task(task_id, agent_id)
    lease = coordinator.acquire_lease(task_id, agent_id)
    coordinator.create_attempt(task_id, agent_id, lease.lease_id)
    coordinator.transition_task(task_id, TaskState.IN_PROGRESS, confirmed=True)

    task_dir = tmp_path / "receipt"
    task_dir.mkdir()
    (task_dir / "RECEIPT.md").write_text(
        f"""---
protocol_version: 1
task_id: {task_id}
attempt: 1
lease_id: {lease.lease_id}
agent_id: {agent_id}
status: progress
completed_at: '2026-09-20T00:00:00Z'
---

# Progress
""",
        encoding="utf-8",
    )

    result = coordinator.import_receipt(
        str(task_dir), task_id, 1, lease.lease_id, agent_id, confirmed=True,
    )
    assert result and result["status"] == "progress"
    row = coordinator.db.conn.execute(
        "SELECT input_tokens, output_tokens, source, is_estimated "
        "FROM cost_records WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    assert dict(row) == {
        "input_tokens": 2500,
        "output_tokens": 800,
        "source": "receipt",
        "is_estimated": 1,
    }


def test_mcp_factory_owns_single_instance_lock_and_releases_it(tmp_path):
    db_path = tmp_path / "nested" / "bridge.db"
    server = __import__("bridgelib.mcp_server", fromlist=["create_mcp_server"]).create_mcp_server(str(db_path))
    assert (db_path.parent / "bridge.lock").exists()
    server.close()
    assert not (db_path.parent / "bridge.lock").exists()
    second = __import__("bridgelib.mcp_server", fromlist=["create_mcp_server"]).create_mcp_server(str(db_path))
    second.close()


def test_mcp_stdio_releases_resources_on_eof(tmp_path, monkeypatch):
    from bridgelib.mcp_server import create_mcp_server

    db_path = tmp_path / "stdio" / "bridge.db"
    server = create_mcp_server(str(db_path))
    output = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
    ))
    monkeypatch.setattr("sys.stdout", output)

    server.run_stdio()

    assert json.loads(output.getvalue())["result"] == {}
    assert not (db_path.parent / "bridge.lock").exists()
    assert server.db._conn is None
