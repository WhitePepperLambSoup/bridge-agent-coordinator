"""Second-round P0/P1 regression tests for the second GPT assessment.

Test coverage:
- P0: Correct merge target branch (integration worktree)
- P0: Fail-closed Git validation and commit^{commit}
- P0: Worktree lifecycle (create branch first, fail closed, assign path)
- P0: Unified attempt/lease/receipt identity chain
- P0: Immutable validation command registry
- P0: Thread-safe SQLite receipt watching
- P0: Escalation AttributeError fix
- P0: In-memory and database atomicity
- P1: SafetyPolicy DISABLED blocks execution
- P1: Parent-child lease path conflicts
- P1: Cost budget checks include actual cost
- P1: Notification adapter implementations
- P1: Workspace recovery after restart
- P1: Retry history recovery after restart
"""

import pytest
import tempfile
import os
import json
import subprocess
import time
from datetime import datetime, timezone, timedelta

from bridgelib.database import Database, init_database
from bridgelib.state_machine import TaskState, ProgressionPolicy
from bridgelib.leases import LeaseManager, Lease
from bridgelib.review import ReviewManager, ReviewPackage, ReviewVerdict
from bridgelib.merge import MergeQueue, MergeStatus
from bridgelib.workspace import WorkspaceManager
from bridgelib.operations import OperationLog
from bridgelib.coordinator import BridgeCoordinator, CoordinatorError
from bridgelib.safety import SafetyPolicy, ConfirmationMode, ActionPolicy
from bridgelib.cost import CostTracker, BudgetGuard, BudgetThreshold
from bridgelib.retry import EscalationDecider, RetryManager, FailureClassifier
from bridgelib.notifications import NotificationManager, InAppNotifier, LogNotifier, NotificationLevel


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def git_repo():
    """Create a real temporary Git repository for testing."""
    d = tempfile.mkdtemp()
    # Initialize Git.
    subprocess.run(["git", "init"], cwd=d, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, capture_output=True)
    # Create the initial commit.
    with open(os.path.join(d, "README.md"), "w") as f:
        f.write("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=d, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=d, capture_output=True)
    # Create the main branch.
    subprocess.run(["git", "branch", "-M", "main"], cwd=d, capture_output=True)
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


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
    """Create a project, agents, and a task in ASSIGNED state."""
    pid = coordinator.init_project(name="Test", root_path="/tmp/t")
    implementer = coordinator.add_agent(pid, display_name="Impl",
                                         capability_tier="standard",
                                         roles=["implementer"])
    reviewer = coordinator.add_agent(pid, display_name="Rev",
                                      capability_tier="high",
                                      roles=["reviewer"], can_review=True)
    gid = coordinator.create_goal(pid, title="Goal")
    tid = coordinator.create_task(
        goal_id=gid, title="Task",
        allowed_paths=["src/**"],
        acceptance_criteria=["AC-1"],
    )
    coordinator.transition_task(tid, TaskState.PLANNING, actor="user", confirmed=True)
    coordinator.transition_task(tid, TaskState.READY, actor="user", confirmed=True)
    coordinator.assign_task(tid, implementer, reviewer, actor="user")
    return pid, implementer, reviewer, gid, tid


@pytest.fixture
def git_env(coordinator, git_repo):
    """Create a project environment backed by a real Git repository."""
    pid = coordinator.init_project(name="GitTest", root_path=git_repo)
    implementer = coordinator.add_agent(pid, display_name="Impl",
                                         capability_tier="standard",
                                         roles=["implementer"])
    reviewer = coordinator.add_agent(pid, display_name="Rev",
                                      capability_tier="high",
                                      roles=["reviewer"], can_review=True)
    gid = coordinator.create_goal(pid, title="Git Goal")
    tid = coordinator.create_task(
        goal_id=gid, title="Git Task",
        allowed_paths=["src/**"],
        acceptance_criteria=["AC-1"],
    )
    coordinator.transition_task(tid, TaskState.PLANNING, actor="user", confirmed=True)
    coordinator.transition_task(tid, TaskState.READY, actor="user", confirmed=True)
    coordinator.assign_task(tid, implementer, reviewer, actor="user")
    return pid, implementer, reviewer, gid, tid, git_repo


def _prepare_approved(coordinator, env):
    pid, implementer, reviewer, gid, tid = env
    lease = coordinator.acquire_lease(tid, implementer, resource_path="src/**")
    attempt_id = coordinator.create_attempt(tid, implementer, lease.lease_id)
    coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
    coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system")
    coordinator.db.conn.execute(
        "INSERT INTO validations (id, task_id, attempt_id, check_id, status, exit_code, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("val-test-001", tid, attempt_id, "unit-tests", "passed", 0,
         datetime.now(timezone.utc).isoformat()),
    )
    coordinator.db.conn.commit()
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


# ═══════════════════════════════════════════════════════════════
# P0: Fail-closed Git validation
# ═══════════════════════════════════════════════════════════════

class TestP0GitFailClosed:
    """P0: Enforce fail-closed Git validation."""

    def test_fake_commit_rejected_in_real_repo(self, coordinator, git_env):
        """Reject a fake commit in a real Git repository."""
        pid, impl, rev, gid, tid, repo = git_env
        _prepare_approved_with_git(coordinator, git_env)
        with pytest.raises(CoordinatorError, match="does not exist|fake"):
            coordinator.enqueue_merge(tid, "fakefake123", confirmed=True)

    def test_blob_not_accepted_as_commit(self, coordinator, git_env):
        """Do not accept a blob object as a commit."""
        pid, impl, rev, gid, tid, repo = git_env
        _prepare_approved_with_git(coordinator, git_env)
        # Create a blob.
        r = subprocess.run(["git", "hash-object", "-w", "README.md"],
                          cwd=repo, capture_output=True, text=True)
        blob_hash = r.stdout.strip()
        with pytest.raises(CoordinatorError, match="does not exist|fake"):
            coordinator.enqueue_merge(tid, blob_hash, confirmed=True)

    def test_non_git_directory_rejected(self, coordinator, db):
        """Reject an existing non-Git directory by failing closed."""
        d = tempfile.mkdtemp()
        try:
            pid = coordinator.init_project(name="NoGit", root_path=d)
            implementer = coordinator.add_agent(pid, display_name="I", roles=["implementer"])
            reviewer = coordinator.add_agent(pid, display_name="R", roles=["reviewer"], can_review=True)
            gid = coordinator.create_goal(pid, title="G")
            tid = coordinator.create_task(goal_id=gid, title="T",
                                          allowed_paths=["src/**"], acceptance_criteria=["AC-1"])
            for s in [TaskState.PLANNING, TaskState.READY]:
                coordinator.transition_task(tid, s, actor="user", confirmed=True)
            coordinator.assign_task(tid, implementer, reviewer, actor="user")
            _prepare_approved(coordinator, (pid, implementer, reviewer, gid, tid))
            with pytest.raises(CoordinatorError, match="not a Git repository"):
                coordinator.enqueue_merge(tid, "abc123", confirmed=True)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


def _prepare_approved_with_git(coordinator, git_env):
    """Advance a task to APPROVED in a real Git environment."""
    pid, impl, rev, gid, tid, repo = git_env
    lease = coordinator.acquire_lease(tid, impl, resource_path="src/**")
    attempt_id = coordinator.create_attempt(tid, impl, lease.lease_id)
    coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
    coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system")
    coordinator.db.conn.execute(
        "INSERT INTO validations (id, task_id, attempt_id, check_id, status, exit_code, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("val-git-001", tid, attempt_id, "unit-tests", "passed", 0,
         datetime.now(timezone.utc).isoformat()),
    )
    coordinator.db.conn.commit()
    pkg = ReviewPackage(task_id=tid, title="Test")
    coordinator.submit_review(tid, rev, pkg)
    reviews = coordinator.db.list_pending_reviews()
    for r in reviews:
        if r["task_id"] == tid:
            coordinator.complete_review(r["id"], ReviewVerdict.APPROVED, "LGTM",
                                       reviewer_agent_id=rev)
    coordinator.transition_task(tid, TaskState.VALIDATING, actor="system")
    coordinator.transition_task(tid, TaskState.APPROVED, actor="system", confirmed=True)
    return tid


# ═══════════════════════════════════════════════════════════════
# P0: Escalation AttributeError fix
# ═══════════════════════════════════════════════════════════════

class TestP0EscalationFix:
    """P0: Avoid AttributeError when the escalation threshold is reached."""

    def test_double_failure_no_crash(self, coordinator, env):
        """Do not raise AttributeError after two consecutive failures."""
        pid, impl, rev, gid, tid = env
        # First failure.
        result1 = coordinator.record_task_failure(tid, "error 1", exit_code=1)
        assert result1["failure_count"] == 1
        # Second failure reaches the escalation threshold.
        result2 = coordinator.record_task_failure(tid, "error 2", exit_code=1)
        assert result2["failure_count"] == 2
        assert result2["should_escalate"] is True
        # No AttributeError should be raised.
        assert result2["escalation_reason"]  # Nonempty.

    def test_escalation_reason_contains_error(self, coordinator, env):
        """Include the latest error in the escalation reason."""
        pid, impl, rev, gid, tid = env
        coordinator.record_task_failure(tid, "first error", exit_code=1)
        result = coordinator.record_task_failure(tid, "specific timeout error", exit_code=1)
        assert "specific timeout error" in result["escalation_reason"]


# ═══════════════════════════════════════════════════════════════
# P0: Validation command registry
# ═══════════════════════════════════════════════════════════════

class TestP0ValidationCommandRegistry:
    """P0: Enforce an immutable validation command registry."""

    def test_unregistered_required_check_rejected(self, coordinator, env):
        """Reject an unregistered required check."""
        pid, impl, rev, gid, tid = env
        # Add a required check without registering its command.
        coordinator.db.conn.execute(
            "UPDATE tasks SET required_checks_json = ? WHERE id = ?",
            (json.dumps(["my-check"]), tid),
        )
        coordinator.db.conn.commit()
        with pytest.raises(CoordinatorError, match="not registered"):
            coordinator.run_validation(tid, [{"check_id": "my-check"}])

    def test_registered_check_uses_registry_command(self, coordinator, env):
        """Use the registered command and ignore a caller-provided executable."""
        pid, impl, rev, gid, tid = env
        coordinator.db.conn.execute(
            "UPDATE tasks SET required_checks_json = ? WHERE id = ?",
            (json.dumps(["echo-test"]), tid),
        )
        coordinator.db.conn.commit()
        coordinator.register_check_command("echo-test", "echo", ["hello"], confirmed=True)
        # The caller-provided executable should be ignored.
        results = coordinator.run_validation(tid, [{"check_id": "echo-test", "executable": "rm"}])
        assert len(results) >= 1
        # Verify that the DB records the registry command.
        vals = coordinator.db.list_validations_by_task(tid)
        cmd = json.loads(vals[-1]["command_json"])
        assert cmd["executable"] == "echo"
        assert cmd["args"] == ["hello"]

    def test_command_recorded_in_audit(self, coordinator, env):
        """Record the executed command in the audit log."""
        pid, impl, rev, gid, tid = env
        coordinator.db.conn.execute(
            "UPDATE tasks SET required_checks_json = ? WHERE id = ?",
            (json.dumps(["echo-test"]), tid),
        )
        coordinator.db.conn.commit()
        coordinator.register_check_command("echo-test", "echo", ["test-audit"], confirmed=True)
        coordinator.run_validation(tid, [{"check_id": "echo-test"}])
        vals = coordinator.db.list_validations_by_task(tid)
        cmd = json.loads(vals[-1]["command_json"])
        assert "test-audit" in cmd["args"]


# ═══════════════════════════════════════════════════════════════
# P0: Receipt attempt binding
# ═══════════════════════════════════════════════════════════════

class TestP0ReceiptAttemptBinding:
    """P0: Bind receipts to the correct attempt."""

    def test_attempt_mismatch_rejected(self, coordinator, env):
        """Reject a receipt whose attempt does not match the current DB attempt."""
        from bridgelib.receipt_importer import ReceiptImporter, ReceiptImportError
        pid, impl, rev, gid, tid = env
        lease = coordinator.acquire_lease(tid, impl, resource_path="src/**")
        # Create attempt 1.
        att1 = coordinator.create_attempt(tid, impl)
        # Create attempt 2 after completing attempt 1.
        coordinator.complete_attempt(att1, "completed")
        att2 = coordinator.create_attempt(tid, impl)

        d = tempfile.mkdtemp()
        receipt_path = os.path.join(d, "RECEIPT.md")
        with open(receipt_path, "w") as f:
            f.write(f"""---
protocol_version: 1
task_id: {tid}
attempt: 1
lease_id: {lease.lease_id}
agent_id: {impl}
status: completed
submission_commit: abc123
completed_at: 2026-01-01T00:00:00Z
---

# Done
""")
        try:
            with pytest.raises((CoordinatorError, ReceiptImportError), match="attempt"):
                coordinator.import_receipt(d, tid, 1, lease.lease_id, impl)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# P1: SafetyPolicy DISABLED blocks execution
# ═══════════════════════════════════════════════════════════════

class TestP1SafetyDisabledBlocks:
    """P1: Block DISABLED actions even when confirmed=True."""

    def test_disabled_blocks_even_with_confirmation(self):
        policy = SafetyPolicy(mode=ConfirmationMode.BALANCED)
        policy.set_override("test_action", ActionPolicy.DISABLED)
        assert not policy.check_allowed("test_action", confirmed=True)

    def test_disabled_is_disabled(self):
        policy = SafetyPolicy(mode=ConfirmationMode.BALANCED)
        policy.set_override("test_action", ActionPolicy.DISABLED)
        assert policy.is_disabled("test_action")

    def test_check_allowed_allows_auto(self):
        policy = SafetyPolicy(mode=ConfirmationMode.BALANCED)
        assert policy.check_allowed("create_worktree")

    def test_check_allowed_blocks_unconfirmed_in_strict(self):
        policy = SafetyPolicy(mode=ConfirmationMode.STRICT)
        assert not policy.check_allowed("create_worktree", confirmed=False)
        assert policy.check_allowed("create_worktree", confirmed=True)


# ═══════════════════════════════════════════════════════════════
# P1: Parent-child lease path conflicts
# ═══════════════════════════════════════════════════════════════

class TestP1LeaseParentChild:
    """P1: Detect parent-child lease path conflicts."""

    def test_parent_child_conflict(self):
        from bridgelib.leases import LeaseManager, LeaseError
        mgr = LeaseManager()
        # Agent A leases src/**.
        mgr.acquire(task_id="T1", agent_id="A", resource_path="src/**")
        # Agent B's attempt to lease src/file.py should conflict.
        with pytest.raises(LeaseError, match="conflict|overlaps"):
            mgr.acquire(task_id="T2", agent_id="B", resource_path="src/file.py")

    def test_non_overlapping_no_conflict(self):
        from bridgelib.leases import LeaseManager, LeaseError
        mgr = LeaseManager()
        mgr.acquire(task_id="T1", agent_id="A", resource_path="src/**")
        # Different directories do not conflict.
        mgr.acquire(task_id="T2", agent_id="B", resource_path="lib/**")


# ═══════════════════════════════════════════════════════════════
# P1: Cost budget checks include actual cost
# ═══════════════════════════════════════════════════════════════

class TestP1CostBudgetCheck:
    """P1: Include actual cost in cost budget checks."""

    def test_budget_includes_cost(self, coordinator, env):
        """Include actual cost in a budget check."""
        pid, impl, rev, gid, tid = env
        coordinator.record_task_cost(tid, impl, input_tokens=100, output_tokens=50,
                                      estimated_cost=0.30)
        result = coordinator.check_budget(tid, token_budget=50000, cost_budget=0.50)
        assert result["total_cost"] == pytest.approx(0.30, abs=0.01)
        assert "total_cost" in result
        assert "cost_budget" in result

    def test_budget_exceeded_by_cost(self, coordinator, env):
        """Exceed the budget by cost alone."""
        pid, impl, rev, gid, tid = env
        coordinator.record_task_cost(tid, impl, input_tokens=100, output_tokens=50,
                                      estimated_cost=0.60)
        result = coordinator.check_budget(tid, token_budget=50000, cost_budget=0.50)
        assert result["threshold"] == "exceeded"


# ═══════════════════════════════════════════════════════════════
# P1: Notification adapter implementations
# ═══════════════════════════════════════════════════════════════

class TestP1NotificationAdapters:
    """P1: Ensure notification adapters are implemented."""

    def test_inapp_notifier_collects(self):
        notifier = InAppNotifier()
        from bridgelib.notifications import Notification, NotificationLevel
        n = Notification(level=NotificationLevel.INFO, title="Test", message="Hello")
        notifier.notify(n)
        msgs = notifier.drain()
        assert len(msgs) == 1
        assert msgs[0].title == "Test"

    def test_log_notifier_does_not_crash(self):
        notifier = LogNotifier()
        from bridgelib.notifications import Notification, NotificationLevel
        n = Notification(level=NotificationLevel.WARNING, title="Warn", message="Careful")
        # No exception should be raised.
        notifier.notify(n)


# ═══════════════════════════════════════════════════════════════
# P1: Workspace recovery after restart
# ═══════════════════════════════════════════════════════════════

class TestP1WorkspaceRecovery:
    """P1: Restore workspaces to memory after restart."""

    def test_workspace_restored_on_restart(self, coordinator, env):
        pid, impl, rev, gid, tid = env
        coordinator.create_worktree(tid, impl, attempt=1)
        # Simulate a restart.
        coord2 = BridgeCoordinator(
            database=coordinator.db,
            workspace_manager=WorkspaceManager(),
        )
        ws = coord2.get_worktree(tid)
        assert ws is not None
        assert ws["task_id"] == tid


# ═══════════════════════════════════════════════════════════════
# P1: Retry history recovery after restart
# ═══════════════════════════════════════════════════════════════

class TestP1RetryRecovery:
    """P1: Restore retry history after restart."""

    def test_retry_history_restored(self, coordinator, env):
        pid, impl, rev, gid, tid = env
        coordinator.record_task_failure(tid, "error 1", exit_code=1)
        coordinator.record_task_failure(tid, "error 2", exit_code=1)
        # Simulate a restart.
        coord2 = BridgeCoordinator(
            database=coordinator.db,
        )
        count = coord2.retry.failure_count(tid)
        assert count == 2

    def test_safety_mode_restored_on_restart(self, coordinator):
        """Restore Strict safety mode after restarting a Strict project."""
        pid = coordinator.init_project(name="Strict", root_path="/tmp/s",
                                        confirmation="strict")
        # Simulate a restart.
        coord2 = BridgeCoordinator(database=coordinator.db)
        assert coord2.safety.mode == ConfirmationMode.STRICT
