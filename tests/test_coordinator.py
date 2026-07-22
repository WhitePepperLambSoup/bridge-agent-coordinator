"""Phase 4.1 测试 — 协调器核心"""

import pytest
import tempfile
import os
from datetime import datetime, timezone

from bridgelib.database import Database
from bridgelib.state_machine import TaskState, ProgressionPolicy
from bridgelib.leases import LeaseManager
from bridgelib.review import ReviewManager
from bridgelib.merge import MergeQueue
from bridgelib.workspace import WorkspaceManager
from bridgelib.operations import OperationLog
from bridgelib.coordinator import (
    BridgeCoordinator,
    CoordinatorError,
    TaskSummary,
    ProjectSummary,
)


@pytest.fixture
def coordinator():
    """创建内存数据库的协调器"""
    db = Database(":memory:")
    db.initialize()
    coord = BridgeCoordinator(
        database=db,
        lease_manager=LeaseManager(),
        review_manager=ReviewManager(),
        merge_queue=MergeQueue(),
        workspace_manager=WorkspaceManager(),
        operation_log=OperationLog(),
    )
    return coord


class TestProjectLifecycle:
    """项目初始化与配置"""

    def test_init_project(self, coordinator):
        pid = coordinator.init_project(
            name="Test Project",
            root_path="/tmp/test",
            language="zh-CN",
        )
        assert pid is not None
        project = coordinator.get_project(pid)
        assert project["name"] == "Test Project"

    def test_add_agent(self, coordinator):
        pid = coordinator.init_project(name="Test", root_path="/tmp/t")
        aid = coordinator.add_agent(
            project_id=pid,
            display_name="Codex",
            capability_tier="high",
            cost_tier="high",
            roles=["planner", "reviewer"],
            can_plan=True,
            can_review=True,
        )
        assert aid is not None
        agent = coordinator.get_agent(aid)
        assert agent["display_name"] == "Codex"


class TestTaskLifecycle:
    """完整任务生命周期"""

    @pytest.fixture
    def setup(self, coordinator):
        pid = coordinator.init_project(name="Test", root_path="/tmp/t")
        planner = coordinator.add_agent(pid, display_name="Codex", capability_tier="high",
                                        roles=["planner"], can_plan=True, can_review=True)
        implementer = coordinator.add_agent(pid, display_name="Reasonix", capability_tier="standard",
                                           roles=["implementer"], can_plan=False, can_review=False)
        reviewer = coordinator.add_agent(pid, display_name="Claude", capability_tier="high",
                                        roles=["reviewer"], can_review=True)
        gid = coordinator.create_goal(pid, title="Build login feature")
        return pid, planner, implementer, reviewer, gid

    def test_create_and_advance_task(self, coordinator, setup):
        pid, planner, implementer, reviewer, gid = setup

        # 创建任务
        tid = coordinator.create_task(
            goal_id=gid,
            title="Implement OAuth login",
            risk="medium",
            complexity="medium",
            allowed_paths=["src/auth/**", "tests/auth/**"],
            acceptance_criteria=["AC-1: OAuth flow works", "AC-2: Error handling"],
        )
        assert tid is not None
        task = coordinator.get_task(tid)
        assert task["state"] == "draft"

        # Draft → Planning
        coordinator.transition_task(tid, TaskState.PLANNING, actor="user", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "planning"

        # Planning → Ready (Hybrid requires confirmation)
        coordinator.transition_task(tid, TaskState.READY, actor="user", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "ready"

    def test_assign_and_lease_task(self, coordinator, setup):
        pid, planner, implementer, reviewer, gid = setup
        tid = coordinator.create_task(
            goal_id=gid, title="Test task",
            allowed_paths=["src/**"],
            acceptance_criteria=["AC-1"],
        )
        coordinator.transition_task(tid, TaskState.PLANNING, actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.READY, actor="user", confirmed=True)

        # 分配 + 获取租约
        coordinator.assign_task(tid, implementer, reviewer, actor="user")
        task = coordinator.get_task(tid)
        assert task["state"] == "assigned"
        assert task["owner_agent_id"] == implementer

        # 获取租约
        lease = coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        assert lease is not None
        assert lease.status == "active"

    def test_draft_to_approved_path(self, coordinator, setup):
        """完整快乐路径：Draft → Approved（不含 merge）"""
        pid, planner, implementer, reviewer, gid = setup
        tid = coordinator.create_task(
            goal_id=gid, title="Full flow task",
            allowed_paths=["src/**"],
            acceptance_criteria=["AC-1"],
        )

        # Draft → Planning → Ready → Assigned → InProgress
        for state in [TaskState.PLANNING, TaskState.READY]:
            coordinator.transition_task(tid, state, actor="user", confirmed=True)
        coordinator.assign_task(tid, implementer, reviewer, actor="user")
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)

        # InProgress → Submitted
        coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "submitted"

        # 创建验证记录（PASSED）以满足 Approved 门禁
        coordinator.db.conn.execute(
            "INSERT INTO validations (id, task_id, check_id, status, exit_code, created_at) VALUES (?,?,?,?,?,?)",
            ("val-001", tid, "unit-tests", "passed", 0, datetime.now(timezone.utc).isoformat()),
        )
        coordinator.db.conn.commit()

        # 创建审查记录（APPROVED）以满足 Approved 门禁
        from bridgelib.review import ReviewPackage, ReviewVerdict
        pkg = ReviewPackage(task_id=tid, title="Test", acceptance_criteria=["AC-1"])
        coordinator.submit_review(tid, reviewer, pkg)
        # 查找并完成审查
        reviews = coordinator.db.list_pending_reviews()
        for r in reviews:
            if r["task_id"] == tid:
                coordinator.complete_review(r["id"], ReviewVerdict.APPROVED, "LGTM")

        # Submitted → Validating → Approved
        coordinator.transition_task(tid, TaskState.VALIDATING, actor="system", confirmed=True)
        coordinator.transition_task(tid, TaskState.APPROVED, actor="system", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "approved"

        # 提交审查
        from bridgelib.review import ReviewPackage
        pkg = ReviewPackage(
            task_id=tid, title="Test",
            acceptance_criteria=["AC-1"],
            changed_files=["src/main.py"],
            implementer_receipt="done",
        )
        req_id = coordinator.submit_review(tid, reviewer, pkg)
        assert req_id is not None

        # 批准审查
        from bridgelib.review import ReviewVerdict
        result = coordinator.complete_review(req_id, ReviewVerdict.APPROVED, "LGTM")
        assert result.verdict == ReviewVerdict.APPROVED

    def test_revision_loop(self, coordinator, setup):
        """修复循环：Validating → RevisionRequired → Assigned"""
        pid, planner, implementer, reviewer, gid = setup
        tid = coordinator.create_task(
            goal_id=gid, title="Revision test",
            allowed_paths=["src/**"],
            acceptance_criteria=["AC-1"],
        )
        for state in [TaskState.PLANNING, TaskState.READY]:
            coordinator.transition_task(tid, state, actor="user", confirmed=True)
        coordinator.assign_task(tid, implementer, reviewer, actor="user")
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system", confirmed=True)
        coordinator.transition_task(tid, TaskState.VALIDATING, actor="system", confirmed=True)

        # 验证失败 → 需要修复
        coordinator.transition_task(tid, TaskState.REVISION_REQUIRED, actor="system", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "revision_required"

        # 重新分配
        coordinator.transition_task(tid, TaskState.ASSIGNED, actor="user", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "assigned"

    def test_escalation_path(self, coordinator, setup):
        """升级路径：Validating → Escalated → Assigned"""
        pid, planner, implementer, reviewer, gid = setup
        tid = coordinator.create_task(
            goal_id=gid, title="Escalation test",
            allowed_paths=["src/**"],
            acceptance_criteria=["AC-1"],
        )
        for state in [TaskState.PLANNING, TaskState.READY]:
            coordinator.transition_task(tid, state, actor="user", confirmed=True)
        coordinator.assign_task(tid, implementer, reviewer, actor="user")
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system", confirmed=True)
        coordinator.transition_task(tid, TaskState.VALIDATING, actor="system", confirmed=True)

        # 升级
        coordinator.transition_task(tid, TaskState.ESCALATED, actor="system", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "escalated"

        # 升级后重新分配（可能给强模型）
        coordinator.transition_task(tid, TaskState.ASSIGNED, actor="user", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "assigned"


class TestMergeQueueIntegration:
    """合并队列集成"""

    def test_enqueue_and_merge(self, coordinator):
        pid = coordinator.init_project(name="Test", root_path="/tmp/t")
        agent = coordinator.add_agent(pid, display_name="Agent1", roles=["implementer"])
        reviewer = coordinator.add_agent(pid, display_name="Rev", roles=["reviewer"], can_review=True)
        gid = coordinator.create_goal(pid, title="Goal")
        tid = coordinator.create_task(
            goal_id=gid, title="Merge test",
            allowed_paths=["src/**"],
            acceptance_criteria=["AC-1"],
        )
        # 快速推进到 Submitted
        for state in [TaskState.PLANNING, TaskState.READY]:
            coordinator.transition_task(tid, state, actor="user", confirmed=True)
        coordinator.assign_task(tid, agent, reviewer, actor="user")
        coordinator.acquire_lease(tid, agent, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system")

        # 创建验证和审查记录（Approved 门禁要求）
        coordinator.db.conn.execute(
            "INSERT INTO validations (id, task_id, check_id, status, exit_code, created_at) VALUES (?,?,?,?,?,?)",
            ("val-mq-001", tid, "unit-tests", "passed", 0, datetime.now(timezone.utc).isoformat()),
        )
        coordinator.db.conn.commit()
        from bridgelib.review import ReviewPackage, ReviewVerdict
        pkg = ReviewPackage(task_id=tid, title="Test")
        coordinator.submit_review(tid, reviewer, pkg)
        revs = coordinator.db.list_pending_reviews()
        for r in revs:
            if r["task_id"] == tid:
                coordinator.complete_review(r["id"], ReviewVerdict.APPROVED, "LGTM")

        for state in [TaskState.VALIDATING, TaskState.APPROVED]:
            coordinator.transition_task(tid, state, actor="system", confirmed=True)

        # 入队
        entry = coordinator.enqueue_merge(tid, candidate_commit="abc123")
        assert entry.status == "queued"

        # 开始合并
        coordinator.start_merge(entry.entry_id)
        coordinator.complete_merge(entry.entry_id, "merged as xyz")
        updated = coordinator.get_merge_entry(entry.entry_id)
        assert updated.status == "merged"


class TestProjectSummary:
    """项目摘要"""

    def test_summary(self, coordinator):
        pid = coordinator.init_project(name="Test", root_path="/tmp/t")
        agent = coordinator.add_agent(pid, display_name="A1")
        gid = coordinator.create_goal(pid, title="Goal")
        coordinator.create_task(goal_id=gid, title="T1", allowed_paths=["src/**"])
        coordinator.create_task(goal_id=gid, title="T2", allowed_paths=["src/**"])

        summary = coordinator.get_project_summary(pid)
        assert summary.total_tasks == 2
        assert summary.total_agents == 1


class TestErrorHandling:
    """错误处理"""

    def test_invalid_transition_rejected(self, coordinator):
        pid = coordinator.init_project(name="Test", root_path="/tmp/t")
        agent = coordinator.add_agent(pid, display_name="A1")
        gid = coordinator.create_goal(pid, title="Goal")
        tid = coordinator.create_task(goal_id=gid, title="Test", allowed_paths=["src/**"])

        # 不能直接从 Draft 跳到 InProgress
        with pytest.raises(CoordinatorError):
            coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)

    def test_assign_without_ready(self, coordinator):
        pid = coordinator.init_project(name="Test", root_path="/tmp/t")
        agent = coordinator.add_agent(pid, display_name="A1")
        gid = coordinator.create_goal(pid, title="Goal")
        tid = coordinator.create_task(goal_id=gid, title="Test", allowed_paths=["src/**"])

        # Draft 状态不能分配
        with pytest.raises(CoordinatorError):
            coordinator.assign_task(tid, agent, agent, actor="user")
