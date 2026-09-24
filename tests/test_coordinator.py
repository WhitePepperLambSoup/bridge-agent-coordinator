"""Phase 4.1 tests - coordinator core."""

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
    """Create a coordinator backed by an in-memory database."""
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
    """Project initialization and configuration."""

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
    """Complete task lifecycle."""

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

        # Create a task.
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

        # Assign the task and acquire a lease.
        coordinator.assign_task(tid, implementer, reviewer, actor="user")
        task = coordinator.get_task(tid)
        assert task["state"] == "assigned"
        assert task["owner_agent_id"] == implementer

        # Acquire a lease.
        lease = coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        assert lease is not None
        assert lease.status == "active"

    def test_draft_to_approved_path(self, coordinator, setup):
        """Complete happy path from Draft to Approved, excluding merge."""
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
        lease = coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        attempt_id = coordinator.create_attempt(tid, implementer, lease.lease_id)
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)

        # InProgress → Submitted
        coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "submitted"

        # Create a PASSED validation record to satisfy the Approved gate.
        coordinator.db.conn.execute(
            "INSERT INTO validations (id, task_id, attempt_id, check_id, status, exit_code, created_at) VALUES (?,?,?,?,?,?,?)",
            ("val-001", tid, attempt_id, "unit-tests", "passed", 0, datetime.now(timezone.utc).isoformat()),
        )
        coordinator.db.conn.commit()

        # Create an APPROVED review record to satisfy the Approved gate.
        from bridgelib.review import ReviewPackage, ReviewVerdict
        pkg = ReviewPackage(task_id=tid, title="Test", acceptance_criteria=["AC-1"])
        coordinator.submit_review(tid, reviewer, pkg)
        # Find and complete the review.
        reviews = coordinator.db.list_pending_reviews()
        for r in reviews:
            if r["task_id"] == tid:
                coordinator.complete_review(r["id"], ReviewVerdict.APPROVED, "LGTM",
                                           reviewer_agent_id=reviewer)

        # Submitted → Validating → Approved
        coordinator.transition_task(tid, TaskState.VALIDATING, actor="system", confirmed=True)
        coordinator.transition_task(tid, TaskState.APPROVED, actor="system", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "approved"

        # Submit the review.
        from bridgelib.review import ReviewPackage
        pkg = ReviewPackage(
            task_id=tid, title="Test",
            acceptance_criteria=["AC-1"],
            changed_files=["src/main.py"],
            implementer_receipt="done",
        )
        req_id = coordinator.submit_review(tid, reviewer, pkg)
        assert req_id is not None

        # Approve the review.
        from bridgelib.review import ReviewVerdict
        result = coordinator.complete_review(req_id, ReviewVerdict.APPROVED, "LGTM",
                                             reviewer_agent_id=reviewer)
        assert result.verdict == ReviewVerdict.APPROVED

    def test_revision_loop(self, coordinator, setup):
        """Revision loop from Validating to RevisionRequired to Assigned."""
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

        # Validation fails and requires revision.
        coordinator.transition_task(tid, TaskState.REVISION_REQUIRED, actor="system", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "revision_required"

        # Reassign the task.
        coordinator.transition_task(tid, TaskState.ASSIGNED, actor="user", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "assigned"

    def test_escalation_path(self, coordinator, setup):
        """Escalation path from Validating to Escalated to Assigned."""
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

        # Escalate the task.
        coordinator.transition_task(tid, TaskState.ESCALATED, actor="system", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "escalated"

        # Reassign after escalation, potentially to a stronger model.
        coordinator.transition_task(tid, TaskState.ASSIGNED, actor="user", confirmed=True)
        task = coordinator.get_task(tid)
        assert task["state"] == "assigned"


class TestMergeQueueIntegration:
    """Merge queue integration."""

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
        # Advance quickly to Submitted.
        for state in [TaskState.PLANNING, TaskState.READY]:
            coordinator.transition_task(tid, state, actor="user", confirmed=True)
        coordinator.assign_task(tid, agent, reviewer, actor="user")
        lease = coordinator.acquire_lease(tid, agent, resource_path="src/**")
        attempt_id = coordinator.create_attempt(tid, agent, lease.lease_id)
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system")

        # Create validation and review records required by the Approved gate.
        coordinator.db.conn.execute(
            "INSERT INTO validations (id, task_id, attempt_id, check_id, status, exit_code, created_at) VALUES (?,?,?,?,?,?,?)",
            ("val-mq-001", tid, attempt_id, "unit-tests", "passed", 0, datetime.now(timezone.utc).isoformat()),
        )
        coordinator.db.conn.commit()
        from bridgelib.review import ReviewPackage, ReviewVerdict
        pkg = ReviewPackage(task_id=tid, title="Test")
        coordinator.submit_review(tid, reviewer, pkg)
        revs = coordinator.db.list_pending_reviews()
        for r in revs:
            if r["task_id"] == tid:
                coordinator.complete_review(r["id"], ReviewVerdict.APPROVED, "LGTM",
                                           reviewer_agent_id=reviewer)

        for state in [TaskState.VALIDATING, TaskState.APPROVED]:
            coordinator.transition_task(tid, state, actor="system", confirmed=True)

        # Enqueue the merge.
        entry = coordinator.enqueue_merge(tid, candidate_commit="abc123", confirmed=True)
        assert entry.status == "queued"

        # Start the merge.
        coordinator.start_merge(entry.entry_id)
        coordinator.complete_merge(entry.entry_id, "merged as xyz")
        updated = coordinator.get_merge_entry(entry.entry_id)
        assert updated.status == "merged"


class TestProjectSummary:
    """Project summary."""

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
    """Error handling."""

    def test_invalid_transition_rejected(self, coordinator):
        pid = coordinator.init_project(name="Test", root_path="/tmp/t")
        agent = coordinator.add_agent(pid, display_name="A1")
        gid = coordinator.create_goal(pid, title="Goal")
        tid = coordinator.create_task(goal_id=gid, title="Test", allowed_paths=["src/**"])

        # A task cannot jump directly from Draft to InProgress.
        with pytest.raises(CoordinatorError):
            coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)

    def test_assign_without_ready(self, coordinator):
        pid = coordinator.init_project(name="Test", root_path="/tmp/t")
        agent = coordinator.add_agent(pid, display_name="A1")
        gid = coordinator.create_goal(pid, title="Goal")
        tid = coordinator.create_task(goal_id=gid, title="Test", allowed_paths=["src/**"])

        # A task in Draft cannot be assigned.
        with pytest.raises(CoordinatorError):
            coordinator.assign_task(tid, agent, agent, actor="user")
