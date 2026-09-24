"""Phase 2.4 tests for Git worktree management."""

import pytest
from bridgelib.workspace import (
    Workspace,
    WorkspaceManager,
    WorkspaceStatus,
    WorkspaceError,
    workspace_branch_name,
    workspace_dir_name,
)


class TestWorkspaceModel:
    def test_create_workspace(self):
        ws = Workspace(
            workspace_id="ws-001",
            task_id="TASK-001",
            agent_id="agent-a",
            worktree_path="/tmp/.bridge-worktrees/agent-a/TASK-001",
            branch="bridge/agent-a/TASK-001/a1",
            base_commit="abc123",
            status=WorkspaceStatus.ACTIVE,
        )
        assert ws.workspace_id == "ws-001"
        assert ws.status == WorkspaceStatus.ACTIVE

    def test_branch_name_generation(self):
        name = workspace_branch_name("reasonix-worker", "TASK-014", 1)
        assert name == "bridge/reasonix-worker/TASK-014/a1"

    def test_branch_name_attempt_2(self):
        name = workspace_branch_name("codex", "TASK-099", 3)
        assert name == "bridge/codex/TASK-099/a3"

    def test_dir_name_generation(self):
        name = workspace_dir_name("agent-x", "TASK-005")
        assert name == "agent-x/TASK-005"


class TestWorkspaceManager:
    @pytest.fixture
    def manager(self):
        return WorkspaceManager()

    def test_register_workspace(self, manager):
        ws = manager.register(
            task_id="TASK-001",
            agent_id="agent-a",
            attempt=1,
            base_commit="abc123",
            worktree_path="/tmp/ws",
        )
        assert ws.status == WorkspaceStatus.ACTIVE
        assert ws.task_id == "TASK-001"

    def test_list_active_workspaces(self, manager):
        manager.register(task_id="TASK-001", agent_id="a", attempt=1,
                        base_commit="abc", worktree_path="/tmp/ws1")
        manager.register(task_id="TASK-002", agent_id="b", attempt=1,
                        base_commit="def", worktree_path="/tmp/ws2")
        active = manager.list_active()
        assert len(active) == 2

    def test_get_by_task(self, manager):
        manager.register(task_id="TASK-001", agent_id="a", attempt=1,
                        base_commit="abc", worktree_path="/tmp/ws")
        ws = manager.get_by_task("TASK-001")
        assert ws is not None
        assert ws.agent_id == "a"

    def test_clean_workspace(self, manager):
        ws = manager.register(task_id="TASK-001", agent_id="a", attempt=1,
                             base_commit="abc", worktree_path="/tmp/ws")
        cleaned = manager.mark_cleaned(ws.workspace_id)
        assert cleaned.status == WorkspaceStatus.CLEANED

    def test_duplicate_task_rejected(self, manager):
        manager.register(task_id="TASK-001", agent_id="a", attempt=1,
                        base_commit="abc", worktree_path="/tmp/ws1")
        with pytest.raises(WorkspaceError):
            manager.register(task_id="TASK-001", agent_id="b", attempt=1,
                           base_commit="def", worktree_path="/tmp/ws2")

    def test_get_nonexistent(self, manager):
        assert manager.get_by_task("nonexistent") is None

    def test_clean_workspace_allows_reregister_new_attempt(self, manager):
        ws1 = manager.register(task_id="TASK-001", agent_id="a", attempt=1,
                               base_commit="abc", worktree_path="/tmp/ws1")
        manager.mark_cleaned(ws1.workspace_id)
        ws2 = manager.register(task_id="TASK-001", agent_id="a", attempt=2,
                               base_commit="abc", worktree_path="/tmp/ws2")
        assert ws2.attempt == 2
        assert ws2.status == WorkspaceStatus.ACTIVE
        assert manager.get_by_task("TASK-001").workspace_id == ws2.workspace_id
