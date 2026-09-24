"""Bridge worktree management for workspace registration, state, and branch naming.

Design reference: docs/bridge-design/06-git-worktree-and-conflicts.md
"""

import hashlib
import re
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

def _safe_component(value: str, fallback: str = "item") -> str:
    """Return a stable, filesystem/Git-safe component for an external identifier."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip(".-")
    return cleaned[:80] or fallback


def _project_component(project_id: str) -> str:
    """Create a collision-resistant project path component."""
    if not project_id:
        return ""
    safe = _safe_component(project_id, "project")
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:10]
    return f"{safe[:48]}-{digest}"


def workspace_branch_name(
    agent_id: str, task_id: str, attempt: int, project_id: str = "",
) -> str:
    """Generate a task branch name scoped to a project when available."""
    project = _project_component(project_id)
    prefix = f"{project}/" if project else ""
    return (
        f"bridge/{prefix}{_safe_component(agent_id, 'agent')}/"
        f"{_safe_component(task_id, 'task')}/a{int(attempt)}"
    )


def workspace_dir_name(agent_id: str, task_id: str, project_id: str = "") -> str:
    """Generate a project-scoped worktree directory name."""
    project = _project_component(project_id)
    prefix = f"{project}/" if project else ""
    return f"{prefix}{_safe_component(agent_id, 'agent')}/{_safe_component(task_id, 'task')}"


# ── Workspace Manager ─────────────────────────────────────

class WorkspaceManager:
    """Workspace manager using memory in Phase 2 and Git integration in Phase 3."""

    def __init__(self):
        self._workspaces: dict[str, Workspace] = {}
        self._by_task: dict[str, str] = {}  # task_id → workspace_id
        self._lock = threading.Lock()

    def register(
        self, task_id: str, agent_id: str, attempt: int = 1,
        base_commit: str = "", worktree_path: str = "", project_id: str = "",
    ) -> Workspace:
        with self._lock:
            existing_id = self._by_task.get(task_id)
            if existing_id:
                existing_ws = self._workspaces.get(existing_id)
                if existing_ws and existing_ws.status == WorkspaceStatus.ACTIVE:
                    raise WorkspaceError(
                        f"Task {task_id} already has an active workspace"
                    )
                # If the previous workspace was cleaned or inactive, clear the mapping
                del self._by_task[task_id]
            ws_id = f"ws-{secrets.token_hex(6)}"
            branch = workspace_branch_name(agent_id, task_id, attempt, project_id)
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
