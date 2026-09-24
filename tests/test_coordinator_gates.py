"""Phase 0+ negative regression tests for coordinator P0 gates.

Coverage:
- Unknown agents cannot acquire leases.
- Nonexistent tasks cannot be reviewed or merged.
- Agents cannot operate across projects.
- Disabled agents are rejected.
- Unconfirmed operations are blocked under the Manual policy.
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
    """Create a project, agents, and a task in the ASSIGNED state."""
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
    """P0 acquire_lease gate tests."""

    def test_unknown_agent_rejected(self, coordinator, env):
        """A nonexistent agent cannot acquire a lease."""
        pid, planner, implementer, reviewer, gid, tid = env
        with pytest.raises(CoordinatorError, match="not found"):
            coordinator.acquire_lease(tid, "does-not-exist", resource_path="src/**")

    def test_disabled_agent_rejected(self, coordinator, env):
        """A disabled agent cannot acquire a lease."""
        pid, planner, implementer, reviewer, gid, tid = env
        # Create a disabled agent.
        disabled = coordinator.add_agent(pid, display_name="Disabled",
                                          capability_tier="standard", roles=["implementer"])
        import json
        # Disable it directly through the database.
        coordinator.db.conn.execute("UPDATE agent_profiles SET enabled = 0 WHERE id = ?", (disabled,))
        coordinator.db.conn.commit()
        with pytest.raises(CoordinatorError, match="disabled"):
            coordinator.acquire_lease(tid, disabled, resource_path="src/**")

    def test_cross_project_agent_rejected(self, coordinator, env):
        """An agent from another project cannot acquire a lease."""
        pid, planner, implementer, reviewer, gid, tid = env
        pid2 = coordinator.init_project(name="Other", root_path="/tmp/o")
        other_agent = coordinator.add_agent(pid2, display_name="OtherAgent",
                                             capability_tier="standard", roles=["implementer"])
        with pytest.raises(CoordinatorError, match="belongs to project"):
            coordinator.acquire_lease(tid, other_agent, resource_path="src/**")

    def test_non_owner_agent_rejected(self, coordinator, env):
        """An agent that is not the owner cannot acquire a lease."""
        pid, planner, implementer, reviewer, gid, tid = env
        # The planner is not the owner of this task.
        with pytest.raises(CoordinatorError, match="not the owner"):
            coordinator.acquire_lease(tid, planner, resource_path="src/**")

    def test_wrong_state_rejected(self, coordinator, env):
        """A lease requires the ASSIGNED or IN_PROGRESS state."""
        pid, planner, implementer, reviewer, gid, tid = env
        # Use a new Draft task to verify the state check.
        tid2 = coordinator.create_task(
            goal_id=gid, title="Draft task",
            allowed_paths=["src/**"], acceptance_criteria=["AC-1"],
        )
        # Keep the task in the Draft state.
        with pytest.raises(CoordinatorError, match="must be in 'assigned' or 'in_progress'"):
            coordinator.acquire_lease(tid2, implementer, resource_path="src/**")

    def test_out_of_scope_path_rejected(self, coordinator, env):
        """A path outside allowed_paths cannot receive a lease."""
        pid, planner, implementer, reviewer, gid, tid = env
        with pytest.raises(CoordinatorError, match="not within the allowed scope"):
            coordinator.acquire_lease(tid, implementer, resource_path="secret/**")

    def test_duplicate_lease_rejected(self, coordinator, env):
        """A second lease cannot be acquired while one is active."""
        pid, planner, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        with pytest.raises(CoordinatorError, match="already has an active lease"):
            coordinator.acquire_lease(tid, implementer, resource_path="src/other/**")


class TestReviewGates:
    """P0 submit_review gate tests."""

    def test_nonexistent_task_rejected(self, coordinator, env):
        """A review cannot be submitted for a nonexistent task."""
        pid, planner, implementer, reviewer, gid, tid = env
        pkg = ReviewPackage(task_id="TASK-999999", title="Ghost task")
        with pytest.raises(CoordinatorError, match="not found"):
            coordinator.submit_review("TASK-999999", reviewer, pkg)

    def test_nonexistent_reviewer_rejected(self, coordinator, env):
        """A nonexistent reviewer cannot submit a review."""
        pid, planner, implementer, reviewer, gid, tid = env
        pkg = ReviewPackage(task_id=tid, title="Test")
        with pytest.raises(CoordinatorError, match="not found"):
            coordinator.submit_review(tid, "ghost-reviewer", pkg)

    def test_disabled_reviewer_rejected(self, coordinator, env):
        """A disabled reviewer cannot submit a review."""
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
        """A reviewer from another project cannot submit a review."""
        pid, planner, implementer, reviewer, gid, tid = env
        pid2 = coordinator.init_project(name="Other", root_path="/tmp/o")
        other_rev = coordinator.add_agent(pid2, display_name="OtherRev",
                                           capability_tier="high",
                                           roles=["reviewer"], can_review=True)
        pkg = ReviewPackage(task_id=tid, title="Test")
        with pytest.raises(CoordinatorError, match="belongs to project"):
            coordinator.submit_review(tid, other_rev, pkg)

    def test_no_review_permission_rejected(self, coordinator, env):
        """An agent without review permission cannot submit a review."""
        pid, planner, implementer, reviewer, gid, tid = env
        # The implementer does not have can_review permission.
        pkg = ReviewPackage(task_id=tid, title="Test")
        with pytest.raises(CoordinatorError, match="does not have review permission"):
            coordinator.submit_review(tid, implementer, pkg)


class TestMergeGates:
    """P0 enqueue_merge gate tests."""

    def _prepare_approved(self, coordinator, env):
        """Advance the task to APPROVED with validation and review records."""
        pid, planner, implementer, reviewer, gid, tid = env
        lease = coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        attempt_id = coordinator.create_attempt(tid, implementer, lease.lease_id)
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system")
        # Create a validation record.
        from datetime import datetime, timezone
        coordinator.db.conn.execute(
            "INSERT INTO validations (id, task_id, attempt_id, check_id, status, exit_code, created_at) VALUES (?,?,?,?,?,?,?)",
            ("val-mg-001", tid, attempt_id, "unit-tests", "passed", 0, datetime.now(timezone.utc).isoformat()),
        )
        coordinator.db.conn.commit()
        # Create a review record.
        from bridgelib.review import ReviewPackage, ReviewVerdict
        pkg = ReviewPackage(task_id=tid, title="Test")
        coordinator.submit_review(tid, reviewer, pkg)
        reviews = coordinator.db.list_pending_reviews()
        for r in reviews:
            if r["task_id"] == tid:
                coordinator.complete_review(r["id"], ReviewVerdict.APPROVED, "LGTM",
                                           reviewer_agent_id=reviewer)
        coordinator.transition_task(tid, TaskState.VALIDATING, actor="system")
        coordinator.transition_task(tid, TaskState.APPROVED, actor="system", confirmed=True)
        return tid

    def test_nonexistent_task_rejected(self, coordinator, env):
        """A nonexistent task cannot be enqueued for merge."""
        with pytest.raises(CoordinatorError, match="not found"):
            coordinator.enqueue_merge("TASK-999999", "abc1234")

    def test_non_approved_state_rejected(self, coordinator, env):
        """A task outside the APPROVED state cannot be enqueued for merge."""
        pid, planner, implementer, reviewer, gid, tid = env
        with pytest.raises(CoordinatorError, match="must be in 'approved' state"):
            coordinator.enqueue_merge(tid, "abc1234")

    def test_empty_commit_rejected(self, coordinator, env):
        """An empty candidate_commit cannot be enqueued."""
        tid = self._prepare_approved(coordinator, env)
        with pytest.raises(CoordinatorError, match="candidate_commit must not be empty"):
            coordinator.enqueue_merge(tid, "")

    def test_valid_commit_enqueued(self, coordinator, env):
        """A valid APPROVED task with a nonempty commit can be enqueued."""
        tid = self._prepare_approved(coordinator, env)
        entry = coordinator.enqueue_merge(tid, "abc1234", confirmed=True)
        assert entry is not None
        assert entry.status == "queued"


class TestPersistenceGates:
    """P0 runtime state persistence tests."""

    def test_leases_persisted(self, coordinator, env):
        """A lease can be queried from the database after creation."""
        pid, planner, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        leases = coordinator.db.list_active_leases(project_id=pid)
        assert len(leases) >= 1
        assert any(l["task_id"] == tid for l in leases)

    def test_reviews_persisted(self, coordinator, env):
        """A review request can be queried after creation."""
        pid, planner, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        pkg = ReviewPackage(task_id=tid, title="Test")
        req_id = coordinator.submit_review(tid, reviewer, pkg)
        reviews = coordinator.db.list_pending_reviews(project_id=pid)
        assert len(reviews) >= 1

    def test_merge_entries_persisted(self, coordinator, env):
        """A merge entry can be queried after creation."""
        pid, planner, implementer, reviewer, gid, tid = env
        lease = coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        attempt_id = coordinator.create_attempt(tid, implementer, lease.lease_id)
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system")
        # Create validation and review records.
        from datetime import datetime, timezone
        coordinator.db.conn.execute(
            "INSERT INTO validations (id, task_id, attempt_id, check_id, status, exit_code, created_at) VALUES (?,?,?,?,?,?,?)",
            ("val-mp-001", tid, attempt_id, "unit-tests", "passed", 0, datetime.now(timezone.utc).isoformat()),
        )
        coordinator.db.conn.commit()
        from bridgelib.review import ReviewPackage, ReviewVerdict
        pkg = ReviewPackage(task_id=tid, title="Test")
        coordinator.submit_review(tid, reviewer, pkg)
        reviews = coordinator.db.list_pending_reviews()
        for r in reviews:
            if r["task_id"] == tid:
                coordinator.complete_review(r["id"], ReviewVerdict.APPROVED, "LGTM",
                                           reviewer_agent_id=reviewer)
        coordinator.transition_task(tid, TaskState.VALIDATING, actor="system")
        coordinator.transition_task(tid, TaskState.APPROVED, actor="system", confirmed=True)
        coordinator.enqueue_merge(tid, "abc1234", confirmed=True)
        entries = coordinator.db.list_merge_entries(task_id=tid)
        assert len(entries) >= 1


class TestProjectSummaryIsolation:
    """P1 project summary isolation tests."""

    def test_summary_isolated_by_project(self, coordinator, env):
        """A project summary excludes another project's runtime data."""
        pid, planner, implementer, reviewer, gid, tid = env
        # Create a lease in project 1.
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")

        # Create project 2.
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

        # Project 2's summary should not include project 1's lease.
        summary2 = coordinator.get_project_summary(pid2)
        # Project 2 has no active leases.
        assert summary2.active_leases == 0

        # Project 1's summary should include an active lease.
        summary1 = coordinator.get_project_summary(pid)
        assert summary1.active_leases >= 1
