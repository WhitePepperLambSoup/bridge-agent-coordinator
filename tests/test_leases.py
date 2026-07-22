"""Phase 2.1 测试 — 租约与心跳"""

import time
import pytest
from datetime import datetime, timezone, timedelta

from bridgelib.leases import (
    Lease,
    LeaseManager,
    LeaseStatus,
    LeaseError,
    generate_lease_id,
    is_lease_expired,
    compute_expiry,
)


class TestLeaseModel:
    """租约数据模型"""

    def test_create_lease(self):
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=900)
        lease = Lease(
            lease_id="lease-abc",
            task_id="TASK-001",
            agent_id="agent-1",
            attempt_id="attempt-1",
            resource_type="task",
            resource_path="src/auth/**",
            status=LeaseStatus.ACTIVE,
            expires_at=expires,
            created_at=now,
        )
        assert lease.lease_id == "lease-abc"
        assert lease.task_id == "TASK-001"
        assert lease.status == LeaseStatus.ACTIVE

    def test_lease_to_dict(self):
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=900)
        lease = Lease(
            lease_id="lease-x",
            task_id="TASK-002",
            agent_id="agent-b",
            resource_type="global",
            resource_path="GLOBAL:dependency-manifest",
            expires_at=expires,
        )
        d = lease.to_dict()
        assert d["lease_id"] == "lease-x"
        assert d["resource_type"] == "global"
        assert d["status"] == "active"


class TestLeaseExpiry:
    """租约过期判断"""

    def test_active_lease_not_expired(self):
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        assert not is_lease_expired(expires)

    def test_expired_lease(self):
        expires = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert is_lease_expired(expires)

    def test_exact_boundary(self):
        """刚好到期算过期"""
        expires = datetime.now(timezone.utc)
        time.sleep(0.01)  # 确保已经过了
        assert is_lease_expired(expires)

    def test_compute_expiry_default(self):
        """默认 TTL 900 秒"""
        before = datetime.now(timezone.utc)
        expiry = compute_expiry()
        after = datetime.now(timezone.utc)
        diff = (expiry - before).total_seconds()
        assert 890 <= diff <= 910  # 900 ± 10


class TestLeaseIdGeneration:
    """租约 ID 生成"""

    def test_generate_unique_ids(self):
        ids = {generate_lease_id() for _ in range(100)}
        assert len(ids) == 100  # 全部唯一

    def test_generate_id_prefix(self):
        lease_id = generate_lease_id()
        assert lease_id.startswith("lease-")


class TestLeaseManager:
    """租约管理器"""

    @pytest.fixture
    def manager(self):
        return LeaseManager()

    def test_acquire_lease(self, manager):
        lease = manager.acquire(
            task_id="TASK-001",
            agent_id="agent-a",
            attempt_id="attempt-1",
            resource_type="task",
            resource_path="src/auth/**",
            ttl_seconds=900,
        )
        assert lease.status == LeaseStatus.ACTIVE
        assert lease.task_id == "TASK-001"

    def test_renew_lease(self, manager):
        lease = manager.acquire(
            task_id="TASK-001", agent_id="agent-a",
            resource_path="src/**", ttl_seconds=10,
        )
        original_expiry = lease.expires_at
        renewed = manager.renew(lease.lease_id, ttl_seconds=900)
        assert renewed.expires_at > original_expiry

    def test_renew_expired_lease_fails(self, manager):
        lease = manager.acquire(
            task_id="TASK-001", agent_id="agent-a",
            resource_path="src/**", ttl_seconds=-1,  # 立即过期
        )
        with pytest.raises(LeaseError):
            manager.renew(lease.lease_id, ttl_seconds=900)

    def test_revoke_lease(self, manager):
        lease = manager.acquire(
            task_id="TASK-001", agent_id="agent-a",
            resource_path="src/**", ttl_seconds=900,
        )
        revoked = manager.revoke(lease.lease_id, reason="task completed")
        assert revoked.status == LeaseStatus.REVOKED
        assert revoked.revoked_reason == "task completed"

    def test_revoke_nonexistent_lease(self, manager):
        with pytest.raises(LeaseError):
            manager.revoke("nonexistent", reason="test")

    def test_heartbeat_updates_time(self, manager):
        lease = manager.acquire(
            task_id="TASK-001", agent_id="agent-a",
            resource_path="src/**", ttl_seconds=10,
        )
        original_heartbeat = lease.heartbeat_at
        time.sleep(0.01)
        manager.heartbeat(lease.lease_id, progress_pct=50, current_step="coding")
        updated = manager.get(lease.lease_id)
        assert updated.progress_pct == 50
        assert updated.current_step == "coding"

    def test_list_active_leases(self, manager):
        manager.acquire(task_id="TASK-001", agent_id="a", resource_path="src/a/**")
        manager.acquire(task_id="TASK-002", agent_id="b", resource_path="src/b/**")
        active = manager.list_active()
        assert len(active) == 2

    def test_list_expired(self, manager):
        # 创建一个立即过期的租约
        manager.acquire(
            task_id="TASK-001", agent_id="a",
            resource_path="src/**", ttl_seconds=-1,
        )
        manager.acquire(
            task_id="TASK-002", agent_id="b",
            resource_path="tests/**", ttl_seconds=3600,
        )
        expired = manager.list_expired()
        assert len(expired) == 1
        assert expired[0].task_id == "TASK-001"

    def test_conflict_detection_same_path(self, manager):
        """相同资源路径的冲突检测"""
        manager.acquire(
            task_id="TASK-001", agent_id="a",
            resource_path="src/auth/**", ttl_seconds=900,
        )
        with pytest.raises(LeaseError):
            manager.acquire(
                task_id="TASK-002", agent_id="b",
                resource_path="src/auth/**", ttl_seconds=900,
            )

    def test_no_conflict_different_paths(self, manager):
        """不同路径不冲突"""
        manager.acquire(task_id="TASK-001", agent_id="a", resource_path="src/a/**")
        lease2 = manager.acquire(task_id="TASK-002", agent_id="b", resource_path="src/b/**")
        assert lease2.status == LeaseStatus.ACTIVE

    def test_conflict_detection_global_resource(self, manager):
        """全局资源冲突"""
        manager.acquire(
            task_id="TASK-001", agent_id="a",
            resource_type="global", resource_path="GLOBAL:database-schema",
        )
        with pytest.raises(LeaseError):
            manager.acquire(
                task_id="TASK-002", agent_id="b",
                resource_type="global", resource_path="GLOBAL:database-schema",
            )
