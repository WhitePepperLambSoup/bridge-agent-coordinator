"""Phase 0+ 反例回归测试 — 验证协调器门禁阻止审查报告中的 P0 问题。

测试覆盖：
- 未知 Agent 不能取得租约
- 虚假任务不能被审查/合并
- 跨项目 Agent 不能操作
- disabled Agent 被拒绝
- Manual 策略未确认被阻止
"""

import pytest
from bridgelib.coordinator import BridgeCoordinator, CoordinatorError
from bridgelib.database import Database, init_database
from bridgelib.state_machine import TaskState
from bridgelib.leases import LeaseManager
from bridgelib.review import ReviewManager, ReviewPackage
from bridgelib.merge import MergeQueue
from bridgelib.workspace import WorkspaceManager
from bridgelib.operations import OperationLog
import tempfile, os


@pytest.fixture
def db():
    d = tempfile.mkdtemp()
    db_path = os.path.join(d, "test.db")
    database = init_database(db_path)
    yield database
    database.close()
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def coordinator(db):
    return BridgeCoordinator(
        database=db,
        lease_manager=LeaseManager(),
        review_manager=ReviewManager(),
        merge_queue=MergeQueue(),
        workspace_manager=WorkspaceManager(),
        operation_log=OperationLog(),
    )


@pytest.fixture
def env(coordinator):
    """创建项目 + 两个 Agent + 一个任务到 ASSIGNED 状态"""
    pid = coordinator.init_project(name="Test", root_path="/tmp/t")
    planner = coordinator.add_agent(pid, display_name="Codex",
                                     capability_tier="high",
                                     roles=["planner"], can_plan=True, can_review=True)
    implementer = coordinator.add_agent(pid, display_name="Reasonix",
                                         capability_tier="standard",
                                         roles=["implementer"])
    reviewer = coordinator.add_agent(pid, display_name="Claude",
                                      capability_tier="high",
                                      roles=["reviewer"], can_review=True)
    gid = coordinator.create_goal(pid, title="Test goal")
    tid = coordinator.create_task(
        goal_id=gid, title="Test task",
        allowed_paths=["src/**"],
        acceptance_criteria=["AC-1"],
    )
    coordinator.transition_task(tid, TaskState.PLANNING, actor="user", confirmed=True)
    coordinator.transition_task(tid, TaskState.READY, actor="user", confirmed=True)
    coordinator.assign_task(tid, implementer, reviewer, actor="user")
    return pid, planner, implementer, reviewer, gid, tid


class TestLeaseGates:
    """P0: acquire_lease 门禁测试"""

    def test_unknown_agent_rejected(self, coordinator, env):
        """不存在的 Agent 不能取得租约"""
        pid, planner, implementer, reviewer, gid, tid = env
        with pytest.raises(CoordinatorError, match="not found"):
            coordinator.acquire_lease(tid, "does-not-exist", resource_path="src/**")

    def test_disabled_agent_rejected(self, coordinator, env):
        """禁用的 Agent 不能取得租约"""
        pid, planner, implementer, reviewer, gid, tid = env
        # 创建一个 disabled agent
        disabled = coordinator.add_agent(pid, display_name="Disabled",
                                          capability_tier="standard", roles=["implementer"])
        import json
        # 通过 db 直接禁用
        coordinator.db.conn.execute("UPDATE agent_profiles SET enabled = 0 WHERE id = ?", (disabled,))
        coordinator.db.conn.commit()
        with pytest.raises(CoordinatorError, match="disabled"):
            coordinator.acquire_lease(tid, disabled, resource_path="src/**")

    def test_cross_project_agent_rejected(self, coordinator, env):
        """跨项目 Agent 不能取得租约"""
        pid, planner, implementer, reviewer, gid, tid = env
        pid2 = coordinator.init_project(name="Other", root_path="/tmp/o")
        other_agent = coordinator.add_agent(pid2, display_name="OtherAgent",
                                             capability_tier="standard", roles=["implementer"])
        with pytest.raises(CoordinatorError, match="belongs to project"):
            coordinator.acquire_lease(tid, other_agent, resource_path="src/**")

    def test_non_owner_agent_rejected(self, coordinator, env):
        """非 owner 的 Agent 不能取得租约"""
        pid, planner, implementer, reviewer, gid, tid = env
        # planner 不是这个任务的 owner
        with pytest.raises(CoordinatorError, match="not the owner"):
            coordinator.acquire_lease(tid, planner, resource_path="src/**")

    def test_wrong_state_rejected(self, coordinator, env):
        """非 ASSIGNED/IN_PROGRESS 状态不能取得租约"""
        pid, planner, implementer, reviewer, gid, tid = env
        # 先让任务回到 draft（虽然不是标准转换，但测试可以验证状态检查）
        tid2 = coordinator.create_task(
            goal_id=gid, title="Draft task",
            allowed_paths=["src/**"], acceptance_criteria=["AC-1"],
        )
        # 保持在 draft 状态
        with pytest.raises(CoordinatorError, match="must be in 'assigned' or 'in_progress'"):
            coordinator.acquire_lease(tid2, implementer, resource_path="src/**")

    def test_out_of_scope_path_rejected(self, coordinator, env):
        """不在 allowed_paths 内的路径不能取得租约"""
        pid, planner, implementer, reviewer, gid, tid = env
        with pytest.raises(CoordinatorError, match="not within the allowed scope"):
            coordinator.acquire_lease(tid, implementer, resource_path="secret/**")

    def test_duplicate_lease_rejected(self, coordinator, env):
        """已有活跃租约时不能重复取得"""
        pid, planner, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        with pytest.raises(CoordinatorError, match="already has an active lease"):
            coordinator.acquire_lease(tid, implementer, resource_path="src/other/**")


class TestReviewGates:
    """P0: submit_review 门禁测试"""

    def test_nonexistent_task_rejected(self, coordinator, env):
        """不存在的任务不能提交审查"""
        pid, planner, implementer, reviewer, gid, tid = env
        pkg = ReviewPackage(task_id="TASK-999999", title="Ghost task")
        with pytest.raises(CoordinatorError, match="not found"):
            coordinator.submit_review("TASK-999999", reviewer, pkg)

    def test_nonexistent_reviewer_rejected(self, coordinator, env):
        """不存在的审查者不能提交审查"""
        pid, planner, implementer, reviewer, gid, tid = env
        pkg = ReviewPackage(task_id=tid, title="Test")
        with pytest.raises(CoordinatorError, match="not found"):
            coordinator.submit_review(tid, "ghost-reviewer", pkg)

    def test_disabled_reviewer_rejected(self, coordinator, env):
        """禁用的审查者不能提交审查"""
        pid, planner, implementer, reviewer, gid, tid = env
        disabled_rev = coordinator.add_agent(pid, display_name="DisabledRev",
                                              capability_tier="high",
                                              roles=["reviewer"], can_review=True)
        coordinator.db.conn.execute("UPDATE agent_profiles SET enabled = 0 WHERE id = ?", (disabled_rev,))
        coordinator.db.conn.commit()
        pkg = ReviewPackage(task_id=tid, title="Test")
        with pytest.raises(CoordinatorError, match="disabled"):
            coordinator.submit_review(tid, disabled_rev, pkg)

    def test_cross_project_reviewer_rejected(self, coordinator, env):
        """跨项目审查者不能提交审查"""
        pid, planner, implementer, reviewer, gid, tid = env
        pid2 = coordinator.init_project(name="Other", root_path="/tmp/o")
        other_rev = coordinator.add_agent(pid2, display_name="OtherRev",
                                           capability_tier="high",
                                           roles=["reviewer"], can_review=True)
        pkg = ReviewPackage(task_id=tid, title="Test")
        with pytest.raises(CoordinatorError, match="belongs to project"):
            coordinator.submit_review(tid, other_rev, pkg)

    def test_no_review_permission_rejected(self, coordinator, env):
        """无审查权限的 Agent 不能提交审查"""
        pid, planner, implementer, reviewer, gid, tid = env
        # implementer 没有 can_review 权限
        pkg = ReviewPackage(task_id=tid, title="Test")
        with pytest.raises(CoordinatorError, match="does not have review permission"):
            coordinator.submit_review(tid, implementer, pkg)


class TestMergeGates:
    """P0: enqueue_merge 门禁测试"""

    def _prepare_approved(self, coordinator, env):
        """辅助：将任务推进到 APPROVED 状态（含验证和审查记录）"""
        pid, planner, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system")
        # 创建验证记录
        from datetime import datetime, timezone
        coordinator.db.conn.execute(
            "INSERT INTO validations (id, task_id, check_id, status, exit_code, created_at) VALUES (?,?,?,?,?,?)",
            ("val-mg-001", tid, "unit-tests", "passed", 0, datetime.now(timezone.utc).isoformat()),
        )
        coordinator.db.conn.commit()
        # 创建审查记录
        from bridgelib.review import ReviewPackage, ReviewVerdict
        pkg = ReviewPackage(task_id=tid, title="Test")
        coordinator.submit_review(tid, reviewer, pkg)
        reviews = coordinator.db.list_pending_reviews()
        for r in reviews:
            if r["task_id"] == tid:
                coordinator.complete_review(r["id"], ReviewVerdict.APPROVED, "LGTM")
        coordinator.transition_task(tid, TaskState.VALIDATING, actor="system")
        coordinator.transition_task(tid, TaskState.APPROVED, actor="system", confirmed=True)
        return tid

    def test_nonexistent_task_rejected(self, coordinator, env):
        """不存在的任务不能入队合并"""
        with pytest.raises(CoordinatorError, match="not found"):
            coordinator.enqueue_merge("TASK-999999", "abc1234")

    def test_non_approved_state_rejected(self, coordinator, env):
        """非 APPROVED 状态的任务不能入队合并"""
        pid, planner, implementer, reviewer, gid, tid = env
        with pytest.raises(CoordinatorError, match="must be in 'approved' state"):
            coordinator.enqueue_merge(tid, "abc1234")

    def test_empty_commit_rejected(self, coordinator, env):
        """空的 candidate_commit 不能入队"""
        tid = self._prepare_approved(coordinator, env)
        with pytest.raises(CoordinatorError, match="candidate_commit must not be empty"):
            coordinator.enqueue_merge(tid, "")

    def test_valid_commit_enqueued(self, coordinator, env):
        """有效的 APPROVED 任务 + 非空 commit 可以入队"""
        tid = self._prepare_approved(coordinator, env)
        entry = coordinator.enqueue_merge(tid, "abc1234")
        assert entry is not None
        assert entry.status == "queued"


class TestPersistenceGates:
    """P0: 运行时状态持久化测试"""

    def test_leases_persisted(self, coordinator, env):
        """租约创建后可在数据库中查询"""
        pid, planner, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        leases = coordinator.db.list_active_leases(project_id=pid)
        assert len(leases) >= 1
        assert any(l["task_id"] == tid for l in leases)

    def test_reviews_persisted(self, coordinator, env):
        """审查请求创建后可在数据库中查询"""
        pid, planner, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        pkg = ReviewPackage(task_id=tid, title="Test")
        req_id = coordinator.submit_review(tid, reviewer, pkg)
        reviews = coordinator.db.list_pending_reviews(project_id=pid)
        assert len(reviews) >= 1

    def test_merge_entries_persisted(self, coordinator, env):
        """合并条目创建后可在数据库中查询"""
        pid, planner, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system")
        # 创建验证和审查记录
        from datetime import datetime, timezone
        coordinator.db.conn.execute(
            "INSERT INTO validations (id, task_id, check_id, status, exit_code, created_at) VALUES (?,?,?,?,?,?)",
            ("val-mp-001", tid, "unit-tests", "passed", 0, datetime.now(timezone.utc).isoformat()),
        )
        coordinator.db.conn.commit()
        from bridgelib.review import ReviewPackage, ReviewVerdict
        pkg = ReviewPackage(task_id=tid, title="Test")
        coordinator.submit_review(tid, reviewer, pkg)
        reviews = coordinator.db.list_pending_reviews()
        for r in reviews:
            if r["task_id"] == tid:
                coordinator.complete_review(r["id"], ReviewVerdict.APPROVED, "LGTM")
        coordinator.transition_task(tid, TaskState.VALIDATING, actor="system")
        coordinator.transition_task(tid, TaskState.APPROVED, actor="system", confirmed=True)
        coordinator.enqueue_merge(tid, "abc1234")
        entries = coordinator.db.list_merge_entries(task_id=tid)
        assert len(entries) >= 1


class TestProjectSummaryIsolation:
    """P1: 项目摘要隔离测试"""

    def test_summary_isolated_by_project(self, coordinator, env):
        """一个项目的摘要不应包含另一个项目的运行时数据"""
        pid, planner, implementer, reviewer, gid, tid = env
        # 在项目1中创建租约
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")

        # 创建项目2
        pid2 = coordinator.init_project(name="Project2", root_path="/tmp/p2")
        agent2 = coordinator.add_agent(pid2, display_name="Agent2",
                                        capability_tier="standard", roles=["implementer"])
        reviewer2 = coordinator.add_agent(pid2, display_name="Rev2",
                                           capability_tier="high", roles=["reviewer"], can_review=True)
        gid2 = coordinator.create_goal(pid2, title="P2 goal")
        tid2 = coordinator.create_task(
            goal_id=gid2, title="P2 task",
            allowed_paths=["lib/**"], acceptance_criteria=["AC-1"],
        )
        coordinator.transition_task(tid2, TaskState.PLANNING, actor="user", confirmed=True)
        coordinator.transition_task(tid2, TaskState.READY, actor="user", confirmed=True)
        coordinator.assign_task(tid2, agent2, reviewer2, actor="user")

        # 项目2 的摘要不应包含项目1 的租约
        summary2 = coordinator.get_project_summary(pid2)
        # 项目2 没有活跃租约
        assert summary2.active_leases == 0

        # 项目1 的摘要应有活跃租约
        summary1 = coordinator.get_project_summary(pid)
        assert summary1.active_leases >= 1
