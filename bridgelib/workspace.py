"""Bridge Worktree 管理 — 工作区注册、状态追踪、分支命名。

设计参考：docs/bridge-design/06-git-worktree-and-conflicts.md
"""

import secrets
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field


class WorkspaceStatus:
    ACTIVE = "active"
    CLEANED = "cleaned"
    DIRTY = "dirty"
    ERROR = "error"


class WorkspaceError(Exception):
    pass


@dataclass
class Workspace:
    workspace_id: str
    task_id: str
    agent_id: str
    attempt: int = 1
    worktree_path: str = ""
    branch: str = ""
    base_commit: str = ""
    status: str = WorkspaceStatus.ACTIVE
    created_at: str = ""
    cleaned_at: str = ""

    def to_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "attempt": self.attempt,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "base_commit": self.base_commit,
            "status": self.status,
            "created_at": self.created_at,
            "cleaned_at": self.cleaned_at,
        }


# ── Naming Conventions ────────────────────────────────────

def workspace_branch_name(agent_id: str, task_id: str, attempt: int) -> str:
    """生成任务分支名：bridge/<agent>/<task>/a<attempt>"""
    return f"bridge/{agent_id}/{task_id}/a{attempt}"


def workspace_dir_name(agent_id: str, task_id: str) -> str:
    """生成 worktree 目录名：<agent-id>/<task-id>"""
    return f"{agent_id}/{task_id}"


# ── Workspace Manager ─────────────────────────────────────

class WorkspaceManager:
    """工作区管理器（Phase 2 内存实现，Phase 3 接入 Git）。"""

    def __init__(self):
        self._workspaces: dict[str, Workspace] = {}
        self._by_task: dict[str, str] = {}  # task_id → workspace_id
        self._lock = threading.Lock()

    def register(
        self, task_id: str, agent_id: str, attempt: int = 1,
        base_commit: str = "", worktree_path: str = "",
    ) -> Workspace:
        with self._lock:
            if task_id in self._by_task:
                raise WorkspaceError(
                    f"Task {task_id} already has an active workspace"
                )
            ws_id = f"ws-{secrets.token_hex(6)}"
            branch = workspace_branch_name(agent_id, task_id, attempt)
            ws = Workspace(
                workspace_id=ws_id,
                task_id=task_id,
                agent_id=agent_id,
                attempt=attempt,
                worktree_path=worktree_path,
                branch=branch,
                base_commit=base_commit,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            self._workspaces[ws_id] = ws
            self._by_task[task_id] = ws_id
            return ws

    def get(self, workspace_id: str) -> Workspace | None:
        return self._workspaces.get(workspace_id)

    def get_by_task(self, task_id: str) -> Workspace | None:
        ws_id = self._by_task.get(task_id)
        return self._workspaces.get(ws_id) if ws_id else None

    def list_active(self) -> list[Workspace]:
        return [
            ws for ws in self._workspaces.values()
            if ws.status == WorkspaceStatus.ACTIVE
        ]

    def mark_cleaned(self, workspace_id: str) -> Workspace:
        with self._lock:
            ws = self._workspaces.get(workspace_id)
            if ws is None:
                raise WorkspaceError(f"Workspace {workspace_id} not found")
            ws.status = WorkspaceStatus.CLEANED
            ws.cleaned_at = datetime.now(timezone.utc).isoformat()
            return ws
