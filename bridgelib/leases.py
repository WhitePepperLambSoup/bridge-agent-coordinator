"""Bridge 租约与心跳管理 — 任务/路径/全局资源租约。

设计参考：docs/bridge-design/06-git-worktree-and-conflicts.md §租约
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
    """租约操作错误"""
    pass


@dataclass
class Lease:
    """租约数据对象"""
    lease_id: str
    task_id: str
    agent_id: str
    attempt_id: str = ""
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
    """内存中的租约管理器（Phase 2 不依赖数据库）。"""

    def __init__(self):
        self._leases: dict[str, Lease] = {}
        self._lock = threading.Lock()

    def acquire(
        self,
        task_id: str,
        agent_id: str,
        attempt_id: str = "",
        resource_type: str = "task",
        resource_path: str = "",
        ttl_seconds: int = 900,
    ) -> Lease:
        """获取租约。冲突时抛出 LeaseError。"""
        with self._lock:
            self._check_conflict(resource_type, resource_path, task_id)

            lease_id = generate_lease_id()
            lease = Lease(
                lease_id=lease_id,
                task_id=task_id,
                agent_id=agent_id,
                attempt_id=attempt_id,
                resource_type=resource_type,
                resource_path=resource_path,
                expires_at=compute_expiry(ttl_seconds),
            )
            self._leases[lease_id] = lease
            return lease

    def renew(self, lease_id: str, ttl_seconds: int = 900) -> Lease:
        """续租。过期租约不可续。"""
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
        """撤销租约。"""
        with self._lock:
            lease = self._get_or_raise(lease_id)
            lease.status = LeaseStatus.REVOKED
            lease.revoked_reason = reason
            return lease

    def heartbeat(
        self, lease_id: str, progress_pct: int = 0, current_step: str = "", blocker: str = ""
    ) -> Lease:
        """心跳更新 — 用于显示进度和续租暗示（不自动续租）。"""
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
        """列出活跃且未过期的租约。"""
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
        """检查两个资源路径是否重叠（支持 glob 模式）。"""
        if path_a == path_b:
            return True
        # 如果 a 是 glob 模式，检查 b 是否匹配 a
        if "**" in path_a or "*" in path_a:
            if matches_glob(path_b, path_a):
                return True
        # 如果 b 是 glob 模式，检查 a 是否匹配 b
        if "**" in path_b or "*" in path_b:
            if matches_glob(path_a, path_b):
                return True
        return False

    def _check_conflict(self, resource_type: str, resource_path: str, task_id: str):
        """检查是否存在活跃租约冲突。"""
        for existing in self._leases.values():
            if existing.status != LeaseStatus.ACTIVE:
                continue
            if is_lease_expired(existing.expires_at):
                continue
            if existing.resource_type != resource_type:
                continue
            # 检查路径重叠（即使是同一任务的不同 agent）
            if self._check_path_overlap(existing.resource_path, resource_path):
                raise LeaseError(
                    f"Resource conflict: {resource_path} overlaps with {existing.resource_path} "
                    f"already leased by {existing.agent_id} for {existing.task_id}"
                )
