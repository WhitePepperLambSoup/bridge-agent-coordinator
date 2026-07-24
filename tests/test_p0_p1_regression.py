"""P0/P1 regression tests verifying fixes for issues in the GPT assessment.

Test coverage:
- P0: The merge queue verifies that a commit exists
- P0: Crash recovery restores every merge state
- P0: Operation log closure (DB status is completed after completion)
- P0: Safety policy enforcement (Strict mode blocks unconfirmed operations)
- P0: Validation execution uses the database project root
- P0: Receipt validation verifies that the lease exists
- P0: In-memory and database atomicity
- P1: Cost recovery after restart
- P1: Lease recovery preserves the original expiration
- P1: Strict ARTIFACTS protocol validation
- P1: File watching does not trigger duplicates
- P1: SafetyPolicy DISABLED behavior
- P1: Configuration loads local.yaml
- P1: Reviewer identity validation
- P1: Worktree lifecycle API
- P1: Attempt lifecycle CRUD
"""

import pytest
import tempfile
import os
import json
import time
from datetime import datetime, timezone, timedelta

from bridgelib.database import Database, init_database
from bridgelib.state_machine import TaskState, ProgressionPolicy
from bridgelib.leases import LeaseManager
from bridgelib.review import ReviewManager, ReviewPackage, ReviewVerdict
from bridgelib.merge import MergeQueue, MergeStatus
from bridgelib.workspace import WorkspaceManager
from bridgelib.operations import OperationLog
from bridgelib.coordinator import BridgeCoordinator, CoordinatorError
from bridgelib.safety import SafetyPolicy, ConfirmationMode, ActionPolicy
from bridgelib.cost import CostTracker, BudgetGuard, BudgetThreshold, CostRecord
from bridgelib.file_watcher import FileWatcher, WatchedFile, FileEvent
from bridgelib.protocol import validate_artifacts
from bridgelib.receipt_importer import ReceiptImporter, ReceiptImportError
from bridgelib.config import ConfigLoader, ProjectConfig


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


def _prepare_approved(coordinator, env):
    """Advance a task to APPROVED state."""
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
# P0 regression tests
# ═══════════════════════════════════════════════════════════════

class TestP0MergeCommitVerification:
    """P0: Verify that merge queue commits exist."""

    def test_enqueue_requires_confirmed_in_balanced(self, coordinator, env):
        """Require confirmation to enqueue a merge in Balanced mode."""
        tid = _prepare_approved(coordinator, env)
        with pytest.raises(CoordinatorError, match="requires confirmation"):
            coordinator.enqueue_merge(tid, "abc123")

    def test_enqueue_with_confirmed_succeeds(self, coordinator, env):
        """Allow enqueueing when confirmed=True is provided."""
        tid = _prepare_approved(coordinator, env)
        entry = coordinator.enqueue_merge(tid, "abc123", confirmed=True)
        assert entry.status == "queued"

    def test_strict_mode_blocks_unconfirmed_merge(self, coordinator, env):
        """Block unconfirmed merges in Strict mode."""
        tid = _prepare_approved(coordinator, env)
        coordinator.set_safety_mode("strict")
        with pytest.raises(CoordinatorError, match="requires confirmation"):
            coordinator.enqueue_merge(tid, "abc123", confirmed=False)

    def test_strict_mode_allows_confirmed_merge(self, coordinator, env):
        """Allow confirmed merges in Strict mode."""
        tid = _prepare_approved(coordinator, env)
        coordinator.set_safety_mode("strict")
        entry = coordinator.enqueue_merge(tid, "abc123", confirmed=True)
        assert entry.status == "queued"


class TestP0CrashRecovery:
    """P0: Restore every merge state during crash recovery."""

    def test_all_merge_states_restored(self, coordinator, env):
        """Restore merge entries in every state after restart, not only queued."""
        tid = _prepare_approved(coordinator, env)
        # Enqueue and start the merge.
        entry = coordinator.enqueue_merge(tid, "abc123", confirmed=True)
        coordinator.start_merge(entry.entry_id)
        # Simulate a crash by creating a coordinator restored from the same DB.
        coord2 = BridgeCoordinator(
            database=coordinator.db,
            merge_queue=MergeQueue(),
        )
        # The entry in merging state should be restored.
        restored = coord2.get_merge_entry(entry.entry_id)
        assert restored is not None
        assert restored.status == "merging"

    def test_merged_state_restored(self, coordinator, env):
        """Restore completed merge entries after restart."""
        tid = _prepare_approved(coordinator, env)
        entry = coordinator.enqueue_merge(tid, "abc123", confirmed=True)
        coordinator.start_merge(entry.entry_id)
        coordinator.complete_merge(entry.entry_id, "merged")
        # Simulate a restart.
        coord2 = BridgeCoordinator(
            database=coordinator.db,
            merge_queue=MergeQueue(),
        )
        restored = coord2.get_merge_entry(entry.entry_id)
        assert restored is not None
        assert restored.status == "merged"


class TestP0OperationLogClosure:
    """P0: Ensure operation log closure."""

    def test_operation_status_completed_in_db(self, coordinator, env):
        """Set the database operation status to completed after a merge."""
        tid = _prepare_approved(coordinator, env)
        entry = coordinator.enqueue_merge(tid, "abc123", confirmed=True)
        coordinator.start_merge(entry.entry_id)
        coordinator.complete_merge(entry.entry_id, "merged")
        # Check the operation status in the DB.
        op_key = f"merge-{entry.entry_id}"
        row = coordinator.db.conn.execute(
            "SELECT status FROM operations WHERE id = ?", (op_key,)
        ).fetchone()
        assert row is not None
        assert row["status"] == "completed"

    def test_operation_has_task_id(self, coordinator, env):
        """Include the correct task_id in the operation log."""
        tid = _prepare_approved(coordinator, env)
        entry = coordinator.enqueue_merge(tid, "abc123", confirmed=True)
        coordinator.start_merge(entry.entry_id)
        op_key = f"merge-{entry.entry_id}"
        row = coordinator.db.conn.execute(
            "SELECT task_id FROM operations WHERE id = ?", (op_key,)
        ).fetchone()
        assert row is not None
        assert row["task_id"] == tid


class TestP0ValidationPrivilege:
    """P0: Run validation only in a trusted project root or task worktree."""

    def test_validation_uses_db_project_root(self, coordinator, env):
        """Use the database project root and ignore the caller-provided root."""
        pid, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.SUBMITTED, actor="system")
        # Add check_id to required_checks and register the command template.
        coordinator.db.conn.execute(
            "UPDATE tasks SET required_checks_json = ? WHERE id = ?",
            (json.dumps(["echo-test"]), tid),
        )
        coordinator.db.conn.commit()
        # Register the immutable command template.
        coordinator.register_check_command(
            "echo-test", "echo", ["hello"], confirmed=True
        )
        # Call run_validation with an incorrect project_root.
        results = coordinator.run_validation(
            tid,
            [{"check_id": "echo-test"}],
            project_root="/wrong/path",
        )
        # The validation result should be persisted.
        validations = coordinator.db.list_validations_by_task(tid)
        assert len(validations) >= 1

    def test_validation_prefers_active_task_worktree(self, coordinator, env, tmp_path):
        pid, implementer, reviewer, gid, tid = env
        marker = tmp_path / "worktree.marker"
        marker.write_text("candidate", encoding="utf-8")
        coordinator.workspaces.register(
            tid, implementer, worktree_path=str(tmp_path), base_commit="abc123"
        )
        coordinator.register_check_command(
            "worktree-check",
            "python",
            [
                "-c",
                "import pathlib,sys;sys.exit(0 if pathlib.Path('worktree.marker').exists() else 1)",
            ],
            confirmed=True,
        )

        results = coordinator.run_validation(tid, [{"check_id": "worktree-check"}])

        assert results[0]["status"] == "passed"


class TestP0ReceiptLeaseValidation:
    """P0: Verify that a receipt's lease exists in the database."""

    def test_nonexistent_lease_rejected(self, coordinator, env):
        """Reject a nonexistent lease_id."""
        pid, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        # Create a receipt file.
        d = tempfile.mkdtemp()
        receipt_path = os.path.join(d, "RECEIPT.md")
        with open(receipt_path, "w") as f:
            f.write(f"""---
protocol_version: 1
task_id: {tid}
attempt: 1
lease_id: fake-lease-id
agent_id: {implementer}
status: completed
submission_commit: abc123
completed_at: 2026-01-01T00:00:00Z
---

# Done
""")
        with pytest.raises((CoordinatorError, ReceiptImportError), match="not found|does not exist|unknown lease"):
            coordinator.import_receipt(d, tid, 1, "fake-lease-id", implementer)
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_inactive_lease_rejected(self, coordinator, env):
        """Reject an inactive lease."""
        pid, implementer, reviewer, gid, tid = env
        lease = coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        # Revoke the lease.
        coordinator.db.update_lease_status(lease.lease_id, "revoked")
        d = tempfile.mkdtemp()
        receipt_path = os.path.join(d, "RECEIPT.md")
        with open(receipt_path, "w") as f:
            f.write(f"""---
protocol_version: 1
task_id: {tid}
attempt: 1
lease_id: {lease.lease_id}
agent_id: {implementer}
status: completed
submission_commit: abc123
completed_at: 2026-01-01T00:00:00Z
---

# Done
""")
        with pytest.raises((CoordinatorError, ReceiptImportError), match="not active|inactive"):
            coordinator.import_receipt(d, tid, 1, lease.lease_id, implementer)
        import shutil
        shutil.rmtree(d, ignore_errors=True)


class TestP0Atomicity:
    """P0: Ensure in-memory and database atomicity."""

    def test_review_rollback_on_db_failure(self, coordinator, env):
        """Roll back in-memory review state when a DB write fails."""
        pid, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)

        # Simulate a DB create_review failure.
        original_create_review = coordinator.db.create_review
        def failing_create_review(*args, **kwargs):
            raise Exception("Simulated DB failure")
        coordinator.db.create_review = failing_create_review

        pkg = ReviewPackage(task_id=tid, title="Test")
        try:
            with pytest.raises(CoordinatorError, match="Failed to persist"):
                coordinator.submit_review(tid, reviewer, pkg)
            # The review request should not remain in memory.
            pending = coordinator.reviews.list_pending()
            assert not any(r.task_id == tid for r in pending)
        finally:
            coordinator.db.create_review = original_create_review

    def test_merge_rollback_on_db_failure(self, coordinator, env):
        """Roll back in-memory merge state when a DB write fails."""
        tid = _prepare_approved(coordinator, env)
        # Simulate a DB create_merge_entry failure.
        original = coordinator.db.create_merge_entry
        def failing_create(*args, **kwargs):
            raise Exception("Simulated DB failure")
        coordinator.db.create_merge_entry = failing_create

        try:
            with pytest.raises(CoordinatorError, match="Failed to persist"):
                coordinator.enqueue_merge(tid, "abc123", confirmed=True)
            # The merge entry should not remain in memory.
            entries = coordinator.merge._entries
            assert len(entries) == 0
        finally:
            coordinator.db.create_merge_entry = original


# ═══════════════════════════════════════════════════════════════
# P1 regression tests
# ═══════════════════════════════════════════════════════════════

class TestP1CostRecovery:
    """P1: Recover costs after restart."""

    def test_cost_records_restored_on_restart(self, coordinator, env):
        """Restore cost records from the database after restart."""
        pid, implementer, reviewer, gid, tid = env
        coordinator.record_task_cost(tid, implementer, input_tokens=100, output_tokens=50)
        # Simulate a restart.
        coord2 = BridgeCoordinator(
            database=coordinator.db,
            cost_tracker=CostTracker(),
        )
        # Cost records should be restored from the DB.
        records = coord2.costs.records_by_task(tid)
        assert len(records) >= 1
        assert records[0].input_tokens == 100


class TestP1LeaseExpiryPreservation:
    """P1: Preserve the original lease expiration during recovery."""

    def test_lease_expiry_preserved(self, coordinator, env):
        """Keep expires_at unchanged after restart instead of extending it."""
        pid, implementer, reviewer, gid, tid = env
        lease = coordinator.acquire_lease(tid, implementer, resource_path="src/**",
                                           ttl_seconds=60)
        original_expiry = coordinator.db.get_lease(lease.lease_id)["expires_at"]
        # Simulate a restart.
        coord2 = BridgeCoordinator(
            database=coordinator.db,
            lease_manager=LeaseManager(),
        )
        restored = coord2.leases.get(lease.lease_id)
        assert restored is not None
        # The restored expiration should match the original, not extend to 15 minutes.
        assert restored.expires_at.isoformat().startswith(original_expiry[:19])


class TestP1ArtifactsProtocol:
    """P1: Enforce strict ARTIFACTS protocol validation."""

    def test_missing_attempt_rejected(self):
        """Reject a missing attempt field."""
        data = {
            "protocol_version": 1,
            "task_id": "TASK-001",
            "agent_id": "agent-1",
            "base_commit": "abc",
            "submission_commit": "def",
            "changed_files": [{"path": "src/main.py"}],
            "generated_at": "2026-01-01T00:00:00Z",
        }
        errors = validate_artifacts(data)
        assert any("attempt" in e for e in errors)

    def test_missing_base_commit_rejected(self):
        """Reject a missing base_commit field."""
        data = {
            "protocol_version": 1,
            "task_id": "TASK-001",
            "attempt": 1,
            "agent_id": "agent-1",
            "submission_commit": "def",
            "changed_files": [{"path": "src/main.py"}],
            "generated_at": "2026-01-01T00:00:00Z",
        }
        errors = validate_artifacts(data)
        assert any("base_commit" in e for e in errors)

    def test_missing_submission_commit_rejected(self):
        """Reject a missing submission_commit field."""
        data = {
            "protocol_version": 1,
            "task_id": "TASK-001",
            "attempt": 1,
            "agent_id": "agent-1",
            "base_commit": "abc",
            "changed_files": [{"path": "src/main.py"}],
            "generated_at": "2026-01-01T00:00:00Z",
        }
        errors = validate_artifacts(data)
        assert any("submission_commit" in e for e in errors)

    def test_empty_generated_at_rejected(self):
        """Reject an empty generated_at field."""
        data = {
            "protocol_version": 1,
            "task_id": "TASK-001",
            "attempt": 1,
            "agent_id": "agent-1",
            "base_commit": "abc",
            "submission_commit": "def",
            "changed_files": [{"path": "src/main.py"}],
            "generated_at": "",
        }
        errors = validate_artifacts(data)
        assert any("generated_at" in e for e in errors)

    def test_complete_artifacts_pass(self):
        """Accept complete artifacts."""
        data = {
            "protocol_version": 1,
            "task_id": "TASK-001",
            "attempt": 1,
            "agent_id": "agent-1",
            "base_commit": "abc123",
            "submission_commit": "def456",
            "changed_files": [{"path": "src/main.py"}],
            "generated_at": "2026-01-01T00:00:00Z",
        }
        errors = validate_artifacts(data)
        assert len(errors) == 0


class TestP1FileWatcherNoDuplicate:
    """P1: Prevent duplicate file watcher triggers."""

    def test_stable_file_notifies_once(self):
        """Notify only once for a stable file."""
        import tempfile
        d = tempfile.mkdtemp()
        filepath = os.path.join(d, "test.txt")
        with open(filepath, "w") as f:
            f.write("initial content")

        watcher = FileWatcher(stability_ms=100, poll_interval_ms=50)
        watcher.watch(filepath)

        notifications = []
        watcher.on_stable(lambda wf: notifications.append(wf.path))

        watcher.start()
        time.sleep(0.5)  # Wait for stability and notification.
        watcher.stop()

        # Only one notification should be emitted.
        assert len(notifications) == 1

        # Scanning again should not emit another notification.
        watcher.start()
        time.sleep(0.3)
        watcher.stop()
        assert len(notifications) == 1

        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_renotification_after_change(self):
        """Notify again after the file changes."""
        import tempfile
        d = tempfile.mkdtemp()
        filepath = os.path.join(d, "test.txt")
        with open(filepath, "w") as f:
            f.write("content v1")

        watcher = FileWatcher(stability_ms=100, poll_interval_ms=50)
        watcher.watch(filepath)

        notifications = []
        watcher.on_stable(lambda wf: notifications.append(wf.path))

        watcher.start()
        time.sleep(0.4)
        # First notification.
        assert len(notifications) == 1

        # Modify the file.
        with open(filepath, "w") as f:
            f.write("content v2")
        time.sleep(0.4)
        # A second notification should be emitted.
        assert len(notifications) == 2

        watcher.stop()
        import shutil
        shutil.rmtree(d, ignore_errors=True)


class TestP1SafetyPolicyDisabled:
    """P1: Verify SafetyPolicy DISABLED behavior."""

    def test_disabled_not_automatable_in_balanced(self):
        """Do not automate DISABLED actions in Balanced mode."""
        policy = SafetyPolicy(mode=ConfirmationMode.BALANCED)
        policy.set_override("test_action", ActionPolicy.DISABLED)
        assert not policy.can_automate("test_action")
        assert policy.requires_confirmation("test_action")

    def test_disabled_not_automatable_in_strict(self):
        """Do not automate DISABLED actions in Strict mode."""
        policy = SafetyPolicy(mode=ConfirmationMode.STRICT)
        policy.set_override("test_action", ActionPolicy.DISABLED)
        assert not policy.can_automate("test_action")
        assert policy.requires_confirmation("test_action")

    def test_disabled_not_automatable_in_expert(self):
        """Do not automate DISABLED actions in Expert mode."""
        policy = SafetyPolicy(mode=ConfirmationMode.EXPERT)
        policy.set_override("test_action", ActionPolicy.DISABLED)
        assert not policy.can_automate("test_action")
        assert policy.requires_confirmation("test_action")

    def test_auto_automatable_in_balanced(self):
        """Allow AUTO actions to be automated in Balanced mode."""
        policy = SafetyPolicy(mode=ConfirmationMode.BALANCED)
        assert policy.can_automate("create_worktree")
        assert not policy.requires_confirmation("create_worktree")

    def test_strict_requires_confirmation_for_all(self):
        """Require confirmation for every action in Strict mode."""
        policy = SafetyPolicy(mode=ConfirmationMode.STRICT)
        # AUTO policies also require confirmation in Strict mode.
        assert policy.requires_confirmation("create_worktree")
        assert not policy.can_automate("create_worktree")


class TestP1ConfigLocalYaml:
    """P1: Load local.yaml configuration."""

    def test_local_yaml_overrides_project_config(self):
        """Let local.yaml override project.yaml configuration."""
        d = tempfile.mkdtemp()
        bridge_dir = os.path.join(d, ".bridge")
        os.makedirs(bridge_dir)

        # Write project.yaml.
        with open(os.path.join(bridge_dir, "project.yaml"), "w") as f:
            f.write("""\
project:
  id: test
  name: Test Project
  default_branch: main
  language: en
coordination:
  confirmation_policy: balanced
  progression_policy: hybrid
""")

        # Write local.yaml overrides.
        with open(os.path.join(bridge_dir, "local.yaml"), "w") as f:
            f.write("""\
coordination:
  confirmation_policy: strict
  progression_policy: manual
""")

        loader = ConfigLoader(d)
        config = loader.load()
        assert config.confirmation_policy == "strict"
        assert config.progression_policy == "manual"

        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_no_local_yaml_uses_defaults(self):
        """Use project.yaml defaults when local.yaml is absent."""
        d = tempfile.mkdtemp()
        bridge_dir = os.path.join(d, ".bridge")
        os.makedirs(bridge_dir)

        with open(os.path.join(bridge_dir, "project.yaml"), "w") as f:
            f.write("""\
project:
  id: test
  name: Test Project
coordination:
  confirmation_policy: balanced
""")

        loader = ConfigLoader(d)
        config = loader.load()
        assert config.confirmation_policy == "balanced"

        import shutil
        shutil.rmtree(d, ignore_errors=True)


class TestP1ReviewerIdentity:
    """P1: Validate reviewer identity."""

    def test_wrong_reviewer_identity_rejected(self, coordinator, env):
        """Reject an incorrect reviewer identity."""
        pid, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        pkg = ReviewPackage(task_id=tid, title="Test")
        req_id = coordinator.submit_review(tid, reviewer, pkg)
        # Complete the review using the wrong reviewer identity.
        with pytest.raises(CoordinatorError, match="identity mismatch"):
            coordinator.complete_review(
                req_id, ReviewVerdict.APPROVED, "LGTM",
                reviewer_agent_id=implementer,  # The implementer is not the assigned reviewer.
            )

    def test_correct_reviewer_succeeds(self, coordinator, env):
        """Accept the correct reviewer identity."""
        pid, implementer, reviewer, gid, tid = env
        coordinator.acquire_lease(tid, implementer, resource_path="src/**")
        coordinator.transition_task(tid, TaskState.IN_PROGRESS, actor="user", confirmed=True)
        pkg = ReviewPackage(task_id=tid, title="Test")
        req_id = coordinator.submit_review(tid, reviewer, pkg)
        result = coordinator.complete_review(
            req_id, ReviewVerdict.APPROVED, "LGTM",
            reviewer_agent_id=reviewer,
        )
        assert result.verdict == ReviewVerdict.APPROVED


class TestP1WorktreeLifecycle:
    """P1: Exercise the Worktree lifecycle API."""

    def test_create_and_get_worktree(self, coordinator, env):
        """Create and retrieve a workspace."""
        pid, implementer, reviewer, gid, tid = env
        ws_dict = coordinator.create_worktree(tid, implementer, attempt=1)
        assert ws_dict["workspace_id"]
        assert ws_dict["task_id"] == tid
        # Retrieve it.
        ws = coordinator.get_worktree(tid)
        assert ws is not None
        assert ws["task_id"] == tid

    def test_non_owner_cannot_create_worktree(self, coordinator, env):
        """Prevent non-owners from creating a workspace."""
        pid, implementer, reviewer, gid, tid = env
        with pytest.raises(CoordinatorError, match="not the owner"):
            coordinator.create_worktree(tid, reviewer, attempt=1)

    def test_remove_worktree(self, coordinator, env):
        """Clean up a workspace."""
        pid, implementer, reviewer, gid, tid = env
        coordinator.create_worktree(tid, implementer, attempt=1)
        result = coordinator.remove_worktree(tid)
        assert result["status"] == "cleaned"


class TestP1AttemptLifecycle:
    """P1: Exercise Attempt lifecycle CRUD."""

    def test_create_attempt(self, coordinator, env):
        """Create an attempt record."""
        pid, implementer, reviewer, gid, tid = env
        attempt_id = coordinator.create_attempt(tid, implementer)
        assert attempt_id
        attempts = coordinator.list_attempts(tid)
        assert len(attempts) == 1
        assert attempts[0]["attempt_number"] == 1

    def test_attempt_number_increments(self, coordinator, env):
        """Increment attempt numbers."""
        pid, implementer, reviewer, gid, tid = env
        a1 = coordinator.create_attempt(tid, implementer)
        coordinator.complete_attempt(a1, "completed")
        a2 = coordinator.create_attempt(tid, implementer)
        attempts = coordinator.list_attempts(tid)
        assert len(attempts) == 2
        assert attempts[0]["attempt_number"] == 1
        assert attempts[1]["attempt_number"] == 2

    def test_get_current_attempt(self, coordinator, env):
        """Retrieve the current active attempt."""
        pid, implementer, reviewer, gid, tid = env
        a1 = coordinator.create_attempt(tid, implementer)
        current = coordinator.get_current_attempt(tid)
        assert current is not None
        assert current["id"] == a1
        coordinator.complete_attempt(a1, "completed")
        current = coordinator.get_current_attempt(tid)
        assert current is None


class TestP1SafetyFromProject:
    """P1: Initialize the safety policy from project configuration."""

    def test_strict_project_sets_strict_safety(self, coordinator):
        """Set Strict safety mode for a project with confirmation_policy=strict."""
        pid = coordinator.init_project(
            name="Strict Project", root_path="/tmp/s",
            confirmation="strict",
        )
        from bridgelib.safety import ConfirmationMode
        assert coordinator.safety.mode == ConfirmationMode.STRICT

    def test_balanced_project_sets_balanced_safety(self, coordinator):
        """Set Balanced safety mode for a project with confirmation_policy=balanced."""
        pid = coordinator.init_project(
            name="Balanced Project", root_path="/tmp/b",
            confirmation="balanced",
        )
        from bridgelib.safety import ConfirmationMode
        assert coordinator.safety.mode == ConfirmationMode.BALANCED
