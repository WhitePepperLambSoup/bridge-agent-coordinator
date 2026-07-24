"""Third-round P0 regression tests for issues in the second assessment.

Test coverage:
- P0: Merges do not touch the user workspace or stash (real Git repository)
- P0: Check real Git status before deleting a dirty worktree
- P0: SafetyPolicy DISABLED blocks actions with confirmed=True
- P0: A new attempt does not reuse validation from an old attempt
- P0: Reject a review without reviewer_agent_id
- P0: Reject a completed receipt for a non-Git project
- P0: In-memory/DB atomicity for review and merge DB failure rollback
- P1: Lease path segment boundaries (src/** does not conflict with src2)
- P1: Validation thread cleanup
"""

import pytest
import tempfile
import os
import json
import subprocess
import time
import threading
from datetime import datetime, timezone

from bridgelib.database import Database, init_database
from bridgelib.state_machine import TaskState
from bridgelib.leases import LeaseManager, LeaseError
from bridgelib.review import ReviewManager, ReviewPackage, ReviewVerdict
from bridgelib.merge import MergeQueue, MergeStatus
from bridgelib.workspace import WorkspaceManager
from bridgelib.operations import OperationLog
from bridgelib.coordinator import BridgeCoordinator, CoordinatorError
from bridgelib.safety import SafetyPolicy, ConfirmationMode, ActionPolicy


@pytest.fixture
def git_repo():
    """Create a real temporary Git repository."""
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, capture_output=True)
    with open(os.path.join(d, "README.md"), "w") as f:
        f.write("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=d, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=d, capture_output=True)
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
    pid = coordinator.init_project(name="Test", root_path="/tmp/t")
    impl = coordinator.add_agent(pid, display_name="Impl", roles=["implementer"])
    rev = coordinator.add_agent(pid, display_name="Rev", roles=["reviewer"], can_review=True)
    gid = coordinator.create_goal(pid, title="Goal")
    tid = coordinator.create_task(
        goal_id=gid, title="Task", allowed_paths=["src/**"], acceptance_criteria=["AC-1"],
    )
    coordinator.transition_task(tid, TaskState.PLANNING, actor="user", confirmed=True)
    coordinator.transition_task(tid, TaskState.READY, actor="user", confirmed=True)
    coordinator.assign_task(tid, impl, rev, actor="user")
    return pid, impl, rev, gid, tid


@pytest.fixture
def git_env(coordinator, git_repo):
    """Create a project environment backed by a real Git repository."""
    pid = coordinator.init_project(name="GitTest", root_path=git_repo)
    impl = coordinator.add_agent(pid, display_name="Impl", roles=["implementer"])
    rev = coordinator.add_agent(pid, display_name="Rev", roles=["reviewer"], can_review=True)
    gid = coordinator.create_goal(pid, title="Git Goal")
    tid = coordinator.create_task(
        goal_id=gid, title="Git Task", allowed_paths=["src/**"], acceptance_criteria=["AC-1"],
    )
    coordinator.transition_task(tid, TaskState.PLANNING, actor="user", confirmed=True)
    coordinator.transition_task(tid, TaskState.READY, actor="user", confirmed=True)
    coordinator.assign_task(tid, impl, rev, actor="user")
    return pid, impl, rev, gid, tid, git_repo


def _prepare_approved(coordinator, env):
    # Support five- and six-item tuples; git_env includes git_repo.
    if len(env) == 6:
        pid, impl, rev, gid, tid, _repo = env
    else:
        pid, impl, rev, gid, tid = env
    lease = coordinator.acquire_lease(tid, impl, resource_path="src/**")
    attempt_id = coordinator.create_attempt(tid, impl, lease.lease_id)
    coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
    coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system")
    coordinator.db.conn.execute(
        "INSERT INTO validations (id, task_id, attempt_id, check_id, status, exit_code, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("val-pa-001", tid, attempt_id, "unit-tests", "passed", 0,
         datetime.now(timezone.utc).isoformat()),
    )
    coordinator.db.conn.commit()
    pkg = ReviewPackage(task_id=tid, title="Test")
    coordinator.submit_review(tid, rev, pkg)
    for r in coordinator.db.list_pending_reviews():
        if r["task_id"] == tid:
            coordinator.complete_review(r["id"], ReviewVerdict.APPROVED, "LGTM",
                                       reviewer_agent_id=rev)
    coordinator.transition_task(tid, TaskState.VALIDATING, actor="system")
    coordinator.transition_task(tid, TaskState.APPROVED, actor="system", confirmed=True)
    return tid


# ═══════════════════════════════════════════════════════════════
# P0: Merges do not touch the user workspace or stash
# ═══════════════════════════════════════════════════════════════

class TestP0MergeNoWorkspacePollution:
    """P0: Do not modify the user workspace or pop a stash during merges."""

    def test_start_merge_does_not_checkout_target(self, coordinator, git_env):
        """Do not change the user's current branch in start_merge."""
        pid, impl, rev, gid, tid, repo = git_env
        _prepare_approved(coordinator, git_env)

        # Record the current branch.
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                          cwd=repo, capture_output=True, text=True)
        original_branch = r.stdout.strip()

        # Create a feature branch and commit.
        subprocess.run(["git", "checkout", "-b", "feature-branch"], cwd=repo, capture_output=True)
        with open(os.path.join(repo, "feature.txt"), "w") as f:
            f.write("feature\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feature"], cwd=repo, capture_output=True)
        feature_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()

        # Return to main.
        subprocess.run(["git", "checkout", "main"], cwd=repo, capture_output=True)

        # Enqueue and start the merge.
        entry = coordinator.enqueue_merge(tid, feature_commit, confirmed=True)
        coordinator.start_merge(entry.entry_id, confirmed=True)

        # Verify that the user's current branch is unchanged.
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                          cwd=repo, capture_output=True, text=True)
        current_branch = r.stdout.strip()
        assert current_branch == original_branch or current_branch == "main"

    def test_user_stash_not_popped(self, coordinator, git_env):
        """Do not pop an existing user stash during a merge."""
        pid, impl, rev, gid, tid, repo = git_env
        _prepare_approved(coordinator, git_env)

        # The user creates a stash.
        with open(os.path.join(repo, "user_file.txt"), "w") as f:
            f.write("user changes\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "stash"], cwd=repo, capture_output=True)

        # Record the stash count.
        r = subprocess.run(["git", "stash", "list"], cwd=repo, capture_output=True, text=True)
        stash_count_before = len([l for l in r.stdout.strip().split("\n") if l])

        # Create a feature branch and commit.
        subprocess.run(["git", "checkout", "-b", "feature-branch2"], cwd=repo, capture_output=True)
        with open(os.path.join(repo, "feature2.txt"), "w") as f:
            f.write("feature2\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feature2"], cwd=repo, capture_output=True)
        feature_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        subprocess.run(["git", "checkout", "main"], cwd=repo, capture_output=True)

        entry = coordinator.enqueue_merge(tid, feature_commit, confirmed=True)
        coordinator.start_merge(entry.entry_id, confirmed=True)

        # The stash count should not decrease.
        r = subprocess.run(["git", "stash", "list"], cwd=repo, capture_output=True, text=True)
        stash_count_after = len([l for l in r.stdout.strip().split("\n") if l])
        assert stash_count_after == stash_count_before


# ═══════════════════════════════════════════════════════════════
# P0: Check real Git status before deleting a dirty worktree
# ═══════════════════════════════════════════════════════════════

class TestP0DirtyWorktreeDeletion:
    """P0: Do not delete a dirty worktree without confirmation."""

    def test_dirty_worktree_rejected_without_confirmation(self, coordinator, git_env):
        """Require confirmed=True to delete a dirty worktree."""
        pid, impl, rev, gid, tid, repo = git_env
        coordinator.acquire_lease(tid, impl, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)

        # Create a worktree.
        coordinator.create_worktree(tid, impl, attempt=1, confirmed=True)
        ws_info = coordinator.get_worktree(tid)
        assert ws_info is not None
        wt_path = ws_info["worktree_path"]

        # Create an uncommitted file in the worktree.
        if wt_path and os.path.isdir(wt_path):
            with open(os.path.join(wt_path, "uncommitted.txt"), "w") as f:
                f.write("dirty\n")

        # Deletion should be rejected with force=True but confirmed=False.
        with pytest.raises(CoordinatorError, match="uncommitted|confirmation|confirmed"):
            coordinator.remove_worktree(tid, force=True, confirmed=False)

    def test_dirty_worktree_allowed_with_confirmation(self, coordinator, git_env):
        """Allow deletion of a dirty worktree with confirmed=True."""
        pid, impl, rev, gid, tid, repo = git_env
        coordinator.acquire_lease(tid, impl, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)

        coordinator.create_worktree(tid, impl, attempt=1, confirmed=True)
        ws_info = coordinator.get_worktree(tid)
        wt_path = ws_info["worktree_path"] if ws_info else ""

        if wt_path and os.path.isdir(wt_path):
            with open(os.path.join(wt_path, "uncommitted.txt"), "w") as f:
                f.write("dirty\n")

        # confirmed=True should permit deletion.
        result = coordinator.remove_worktree(tid, force=True, confirmed=True)
        assert result["status"] == "cleaned"


# ═══════════════════════════════════════════════════════════════
# P0: SafetyPolicy DISABLED blocks confirmed=True
# ═══════════════════════════════════════════════════════════════

class TestP0SafetyDisabledBlocksConfirmed:
    """P0: Block DISABLED actions even when confirmed=True."""

    def test_disabled_create_worktree_blocked(self, coordinator, env):
        """Reject create_worktree when it is DISABLED."""
        pid, impl, rev, gid, tid = env
        coordinator.acquire_lease(tid, impl, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        coordinator.safety.set_override("create_worktree", ActionPolicy.DISABLED)
        with pytest.raises(CoordinatorError, match="DISABLED"):
            coordinator.create_worktree(tid, impl, confirmed=True)

    def test_disabled_merge_blocked(self, coordinator, env):
        """Reject merge when it is DISABLED."""
        tid = _prepare_approved(coordinator, env)
        coordinator.safety.set_override("merge_low_risk", ActionPolicy.DISABLED)
        with pytest.raises(CoordinatorError, match="DISABLED"):
            coordinator.enqueue_merge(tid, "abc123", confirmed=True)


# ═══════════════════════════════════════════════════════════════
# P0: New attempts do not reuse old validation results
# ═══════════════════════════════════════════════════════════════

class TestP0AttemptIsolationFailClosed:
    """P0: Fail closed when a new attempt lacks its own validation."""

    def test_new_attempt_no_fallback(self, coordinator, env):
        """Prevent attempt 2 from using attempt 1's validation results."""
        pid, impl, rev, gid, tid = env
        coordinator.acquire_lease(tid, impl, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system")

        # Create attempt 1 and add validation.
        att1 = coordinator.create_attempt(tid, impl)
        coordinator.db.conn.execute(
            "INSERT INTO validations (id, task_id, attempt_id, check_id, status, exit_code, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("val-att1-001", tid, att1, "unit-tests", "passed", 0,
             datetime.now(timezone.utc).isoformat()),
        )
        coordinator.db.conn.commit()

        # Complete the review.
        pkg = ReviewPackage(task_id=tid, title="Test")
        coordinator.submit_review(tid, rev, pkg)
        for r in coordinator.db.list_pending_reviews():
            if r["task_id"] == tid:
                coordinator.complete_review(r["id"], ReviewVerdict.APPROVED, "LGTM",
                                           reviewer_agent_id=rev)

        coordinator.transition_task(tid, TaskState.VALIDATING, actor="system")

        # Complete attempt 1 and create attempt 2 without validation.
        coordinator.complete_attempt(att1, "completed")
        att2 = coordinator.create_attempt(tid, impl)

        # Approval should fail because attempt 2 has no validation.
        with pytest.raises(CoordinatorError, match="0 passed|Missing PASSED|PASSED"):
            coordinator.transition_task(tid, TaskState.APPROVED, actor="system", confirmed=True)


# ═══════════════════════════════════════════════════════════════
# P0: Reject reviews without reviewer_agent_id
# ═══════════════════════════════════════════════════════════════

class TestP0ReviewMandatoryIdentity:
    """P0: Reject complete_review without reviewer_agent_id."""

    def test_no_reviewer_id_rejected(self, coordinator, env):
        """Reject an omitted reviewer_agent_id."""
        pid, impl, rev, gid, tid = env
        coordinator.acquire_lease(tid, impl, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        pkg = ReviewPackage(task_id=tid, title="Test")
        req_id = coordinator.submit_review(tid, rev, pkg)
        with pytest.raises(CoordinatorError, match="reviewer_agent_id is required"):
            coordinator.complete_review(req_id, ReviewVerdict.APPROVED, "LGTM")

    def test_reviewer_is_owner_rejected(self, coordinator, env):
        """Prevent the task implementer from acting as reviewer."""
        pid, impl, rev, gid, tid = env
        coordinator.acquire_lease(tid, impl, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        # Submit a review with impl as reviewer, bypassing frontend validation.
        # First grant impl can_review permission.
        coordinator.db.conn.execute(
            "UPDATE agent_profiles SET permissions_json = ? WHERE id = ?",
            (json.dumps({"can_plan": False, "can_review": True, "can_run_validation": True}), impl),
        )
        coordinator.db.conn.commit()
        pkg = ReviewPackage(task_id=tid, title="Test")
        req_id = coordinator.submit_review(tid, impl, pkg)
        # Completion by impl should be rejected because impl is the owner.
        with pytest.raises(CoordinatorError, match="cannot review.*owner"):
            coordinator.complete_review(req_id, ReviewVerdict.APPROVED, "LGTM",
                                       reviewer_agent_id=impl)


# ═══════════════════════════════════════════════════════════════
# P0: Reject completed receipts for non-Git projects
# ═══════════════════════════════════════════════════════════════

class TestP0NonGitReceiptRejected:
    """P0: Reject a completed receipt for a non-Git project."""

    def test_non_git_completed_receipt_rejected(self, coordinator, env):
        """Reject a completed receipt for a non-Git project."""
        pid, impl, rev, gid, tid = env
        lease = coordinator.acquire_lease(tid, impl, resource_path="src/**")

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
submission_commit: abc1234
completed_at: 2026-01-01T00:00:00Z
---

# Done
""")
        try:
            # Reject because /tmp/t is not a Git repository.
            from bridgelib.receipt_importer import ReceiptImportError
            with pytest.raises((CoordinatorError, ReceiptImportError),
                              match="not a Git repository|cannot verify"):
                coordinator.import_receipt(d, tid, 1, lease.lease_id, impl)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# P0: In-memory/DB atomicity for review and merge failure rollback
# ═══════════════════════════════════════════════════════════════

class TestP0AtomicityRollback:
    """P0: Roll back in-memory state when a DB write fails."""

    def test_review_complete_rollback(self, coordinator, env):
        """Roll back in-memory state when review completion fails in the DB."""
        pid, impl, rev, gid, tid = env
        coordinator.acquire_lease(tid, impl, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        pkg = ReviewPackage(task_id=tid, title="Test")
        req_id = coordinator.submit_review(tid, rev, pkg)

        # Simulate a DB update_review failure.
        original = coordinator.db.update_review
        def failing_update(*a, **kw):
            raise Exception("Simulated DB failure")
        coordinator.db.update_review = failing_update

        try:
            with pytest.raises(CoordinatorError, match="Failed to persist"):
                coordinator.complete_review(req_id, ReviewVerdict.APPROVED, "LGTM",
                                           reviewer_agent_id=rev)
            # The review should not be completed in memory.
            result = coordinator.reviews.get_result(req_id)
            assert result is None
        finally:
            coordinator.db.update_review = original

    def test_merge_complete_rollback(self, coordinator, env):
        """Roll back in-memory state when merge completion fails in the DB."""
        tid = _prepare_approved(coordinator, env)
        entry = coordinator.enqueue_merge(tid, "abc123", confirmed=True)
        coordinator.start_merge(entry.entry_id, confirmed=True)

        # Simulate a DB update_merge_entry failure.
        original = coordinator.db.update_merge_entry
        def failing_update(*a, **kw):
            raise Exception("Simulated DB failure")
        coordinator.db.update_merge_entry = failing_update

        try:
            with pytest.raises(CoordinatorError, match="DB persist failed|Failed to persist"):
                coordinator.complete_merge(entry.entry_id, "test", confirmed=True)
            # The in-memory merge state should remain merging.
            e = coordinator.get_merge_entry(entry.entry_id)
            assert e.status == "merging"
        finally:
            coordinator.db.update_merge_entry = original


# ═══════════════════════════════════════════════════════════════
# P1: Lease path segment boundaries
# ═══════════════════════════════════════════════════════════════

class TestP1LeasePathSegmentBoundary:
    """P1: Ensure src/** does not conflict with src2/file.py."""

    def test_src_star_star_not_conflict_with_src2(self):
        from bridgelib.leases import LeaseManager
        mgr = LeaseManager()
        mgr.acquire(task_id="T1", agent_id="A", resource_path="src/**")
        # A file under src2 should not conflict with src/**.
        mgr.acquire(task_id="T2", agent_id="B", resource_path="src2/file.py")

    def test_src_star_star_conflict_with_src_subpath(self):
        from bridgelib.leases import LeaseManager, LeaseError
        mgr = LeaseManager()
        mgr.acquire(task_id="T1", agent_id="A", resource_path="src/**")
        # A file under src should conflict with src/**.
        with pytest.raises(LeaseError):
            mgr.acquire(task_id="T2", agent_id="B", resource_path="src/file.py")


# ═══════════════════════════════════════════════════════════════
# P1: Validation thread cleanup
# ═══════════════════════════════════════════════════════════════

class TestP1ValidationThreadCleanup:
    """P1: Ensure watcher threads exit after validation."""

    def test_threads_cleanup_after_validation(self):
        from bridgelib.validation import ValidationExecutor, ValidationCheck
        initial_count = threading.active_count()
        executor = ValidationExecutor(project_root=".")
        checks = [
            ValidationCheck(check_id=f"test-{i}", executable="echo", args=[str(i)],
                           timeout_seconds=5)
            for i in range(5)
        ]
        executor.execute_all(checks)
        # Wait for threads to exit.
        time.sleep(1.0)
        final_count = threading.active_count()
        # Thread count should not rise significantly; allow one or two delayed exits.
        assert final_count <= initial_count + 2
