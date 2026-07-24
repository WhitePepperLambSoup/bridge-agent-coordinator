"""Bridge lease and heartbeat management for task, path, and global resources.

Design reference: docs/bridge-design/06-git-worktree-and-conflicts.md, Leases section
"""

import secrets
import threading
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from enum import Enum

from bridgelib.scope import matches_glob


class LeaseStatus:
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    RELEASED = "released"


class LeaseError(Exception):
    """Lease operation error."""
    pass


@dataclass
class Lease:
    """Lease data object."""
    lease_id: str
    task_id: str
    agent_id: str
    attempt_id: str = ""
    project_id: str = ""              # P1: Project isolation
    resource_type: str = "task"       # task | path | global
    resource_path: str = ""           # glob pattern or GLOBAL:name
    status: str = LeaseStatus.ACTIVE
    expires_at: datetime = field(default_factory=lambda: compute_expiry())
    heartbeat_at: datetime | None = None
    progress_pct: int = 0
    current_step: str = ""
    blocker: str = ""
    revoked_reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "lease_id": self.lease_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "attempt_id": self.attempt_id,
            "resource_type": self.resource_type,
            "resource_path": self.resource_path,
            "status": self.status,
            "expires_at": self.expires_at.isoformat(),
            "heartbeat_at": self.heartbeat_at.isoformat() if self.heartbeat_at else None,
            "progress_pct": self.progress_pct,
            "current_step": self.current_step,
            "blocker": self.blocker,
            "revoked_reason": self.revoked_reason,
            "created_at": self.created_at.isoformat(),
        }


# ── Helpers ───────────────────────────────────────────────

def generate_lease_id() -> str:
    return f"lease-{secrets.token_hex(6)}"


def compute_expiry(ttl_seconds: int = 900) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)


def is_lease_expired(expires_at: datetime) -> bool:
    return datetime.now(timezone.utc) >= expires_at


# ── Lease Manager ─────────────────────────────────────────

class LeaseManager:
    """In-memory lease manager (Phase 2 does not depend on the database)."""

    def __init__(self):
        self._leases: dict[str, Lease] = {}
        self._lock = threading.Lock()

    def acquire(
        self,
        task_id: str,
        agent_id: str,
        attempt_id: str = "",
        project_id: str = "",
        resource_type: str = "task",
        resource_path: str = "",
        ttl_seconds: int = 900,
    ) -> Lease:
        """Acquire a lease, raising LeaseError on conflict.

        P1: project_id provides project isolation, so identical paths in
        different projects do not conflict.
        """
        with self._lock:
            self._check_conflict(resource_type, resource_path, task_id, project_id)

            lease_id = generate_lease_id()
            lease = Lease(
                lease_id=lease_id,
                task_id=task_id,
                agent_id=agent_id,
                attempt_id=attempt_id,
                project_id=project_id,
                resource_type=resource_type,
                resource_path=resource_path,
                expires_at=compute_expiry(ttl_seconds),
            )
            self._leases[lease_id] = lease
            return lease

    def renew(self, lease_id: str, ttl_seconds: int = 900) -> Lease:
        """Renew a lease; expired leases cannot be renewed."""
        with self._lock:
            lease = self._get_or_raise(lease_id)
            if lease.status == LeaseStatus.REVOKED:
                raise LeaseError(f"Lease {lease_id} has been revoked")
            if is_lease_expired(lease.expires_at):
                lease.status = LeaseStatus.EXPIRED
                raise LeaseError(f"Lease {lease_id} has expired and cannot be renewed")
            lease.expires_at = compute_expiry(ttl_seconds)
            return lease

    def revoke(self, lease_id: str, reason: str = "") -> Lease:
        """Revoke a lease."""
        with self._lock:
            lease = self._get_or_raise(lease_id)
            lease.status = LeaseStatus.REVOKED
            lease.revoked_reason = reason
            return lease

    def heartbeat(
        self, lease_id: str, progress_pct: int = 0, current_step: str = "", blocker: str = ""
    ) -> Lease:
        """Update the heartbeat to show progress and suggest renewal without renewing automatically."""
        if not 0 <= progress_pct <= 100:
            raise LeaseError(f"Progress must be 0-100, got {progress_pct}")

        with self._lock:
            lease = self._get_or_raise(lease_id)
            if lease.status == LeaseStatus.REVOKED:
                raise LeaseError(f"Lease {lease_id} has been revoked")
            if lease.status == LeaseStatus.EXPIRED or is_lease_expired(lease.expires_at):
                raise LeaseError(f"Lease {lease_id} is expired, cannot heartbeat")
            lease.heartbeat_at = datetime.now(timezone.utc)
            lease.progress_pct = progress_pct
            lease.current_step = current_step
            lease.blocker = blocker
            return lease

    def get(self, lease_id: str) -> Lease | None:
        return self._leases.get(lease_id)

    def list_active(self) -> list[Lease]:
        """List active, unexpired leases."""
        return [
            l for l in self._leases.values()
            if l.status == LeaseStatus.ACTIVE and not is_lease_expired(l.expires_at)
        ]

    def list_expired(self) -> list[Lease]:
        return [
            l for l in self._leases.values()
            if l.status == LeaseStatus.ACTIVE and is_lease_expired(l.expires_at)
        ]

    def list_by_task(self, task_id: str) -> list[Lease]:
        return [l for l in self._leases.values() if l.task_id == task_id]

    # ── Internal ──────────────────────────────────────────

    def _get_or_raise(self, lease_id: str) -> Lease:
        lease = self._leases.get(lease_id)
        if lease is None:
            raise LeaseError(f"Lease {lease_id} not found")
        return lease

    def _check_path_overlap(self, path_a: str, path_b: str) -> bool:
        """Check whether two resource paths overlap, including globs and parent paths.

        P1 fix: Detect parent-child path conflicts such as src and src/file.py.
        """
        if not path_a or not path_b:
            return False
        if path_a == path_b:
            return True

        # Normalize paths for parent-child detection.
        norm_a = path_a.replace("\\", "/").rstrip("/")
        norm_b = path_b.replace("\\", "/").rstrip("/")

        # Parent-child detection: src conflicts with src/file.py at path boundaries.
        if not ("*" in norm_a or "**" in norm_a or "*" in norm_b or "**" in norm_b):
            if norm_a.startswith(norm_b + "/") or norm_b.startswith(norm_a + "/"):
                return True

        # Glob pattern matching.
        if "**" in path_a or "*" in path_a:
            if matches_glob(path_b, path_a):
                return True
        if "**" in path_b or "*" in path_b:
            if matches_glob(path_a, path_b):
                return True

        # Glob parent-child detection: src/** conflicts with src/file.py at path boundaries.
        # src/** must not conflict with src2/file.py, so check the path boundary.
        if "/**" in norm_a:
            prefix_a = norm_a[:-3]  # Remove /** to get "src".
            # norm_b must be "src" or start with "src/" (a path boundary).
            if norm_b == prefix_a or norm_b.startswith(prefix_a + "/"):
                return True
        if "/**" in norm_b:
            prefix_b = norm_b[:-3]
            if norm_a == prefix_b or norm_a.startswith(prefix_b + "/"):
                return True

        return False

    def _check_conflict(self, resource_type: str, resource_path: str, task_id: str,
                         project_id: str = ""):
        """Check for an active lease conflict.

        P1: Isolate by project_id so identical paths in different projects
        do not conflict.
        """
        for existing in self._leases.values():
            if existing.status != LeaseStatus.ACTIVE:
                continue
            if is_lease_expired(existing.expires_at):
                continue
            if existing.resource_type != resource_type:
                continue
            # P1: Project isolation means only leases in the same project conflict.
            if project_id and existing.project_id and project_id != existing.project_id:
                continue
            if self._check_path_overlap(existing.resource_path, resource_path):
                raise LeaseError(
                    f"Resource conflict: {resource_path} overlaps with {existing.resource_path} "
                    f"already leased by {existing.agent_id} for {existing.task_id}"
                )
