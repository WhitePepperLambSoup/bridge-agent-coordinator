"""Phase 3.3 tests - operation logs and crash recovery."""

import pytest
from datetime import datetime, timezone
from bridgelib.operations import (
    OperationLog,
    OperationEntry,
    OperationStatus,
    RecoveryScanner,
    OpError,
)


class TestOperationEntry:
    def test_prepare_entry(self):
        entry = OperationEntry.prepare(
            operation_type="merge",
            task_id="TASK-001",
            target="branch: main",
            expected_baseline="abc123",
            idempotency_key="merge-TASK-001-a1",
        )
        assert entry.status == OperationStatus.PREPARED
        assert entry.operation_type == "merge"
        assert entry.idempotency_key == "merge-TASK-001-a1"

    def test_complete_entry(self):
        entry = OperationEntry.prepare("worktree_create", task_id="TASK-002")
        entry.mark_completed(result="worktree created at /tmp/ws")
        assert entry.status == OperationStatus.COMPLETED
        assert entry.result == "worktree created at /tmp/ws"

    def test_fail_entry(self):
        entry = OperationEntry.prepare("cherry_pick", task_id="TASK-003")
        entry.mark_failed(error="conflict in src/main.py")
        assert entry.status == OperationStatus.FAILED
        assert entry.error == "conflict in src/main.py"


class TestOperationLog:
    @pytest.fixture
    def log(self):
        return OperationLog()

    def test_record_and_get(self, log):
        entry = OperationEntry.prepare("merge", task_id="TASK-001")
        log.record(entry)
        retrieved = log.get(entry.operation_id)
        assert retrieved is not None
        assert retrieved.operation_type == "merge"

    def test_list_incomplete(self, log):
        """Incomplete operations have PREPARED status."""
        e1 = OperationEntry.prepare("worktree_create", task_id="TASK-001")
        e2 = OperationEntry.prepare("cherry_pick", task_id="TASK-002")
        e3 = OperationEntry.prepare("merge", task_id="TASK-003")
        log.record(e1)
        log.record(e2)
        log.record(e3)

        # Complete one operation.
        e2.mark_completed("ok")
        log.update(e2)

        incomplete = log.list_incomplete()
        assert len(incomplete) == 2
        assert all(e.status == OperationStatus.PREPARED for e in incomplete)

    def test_list_by_task(self, log):
        e1 = OperationEntry.prepare("merge", task_id="TASK-001")
        e2 = OperationEntry.prepare("merge", task_id="TASK-001")
        log.record(e1)
        log.record(e2)
        task_ops = log.list_by_task("TASK-001")
        assert len(task_ops) == 2

    def test_idempotency_key_deduplication(self, log):
        """The same idempotency key cannot be recorded twice."""
        entry = OperationEntry.prepare(
            "merge", task_id="TASK-001",
            idempotency_key="key-123",
        )
        log.record(entry)
        # Try to record the same key again.
        entry2 = OperationEntry.prepare(
            "merge", task_id="TASK-001",
            idempotency_key="key-123",
        )
        with pytest.raises(OpError):
            log.record(entry2)

    def test_nonexistent_operation(self, log):
        assert log.get("nonexistent") is None


class TestRecoveryScanner:
    """Crash recovery scanner."""

    @pytest.fixture
    def log(self):
        return OperationLog()

    def test_scan_no_incomplete_ops(self, log):
        scanner = RecoveryScanner(log)
        actions = scanner.scan()
        assert len(actions) == 0

    def test_scan_with_incomplete_ops(self, log):
        # Record some incomplete operations.
        e1 = OperationEntry.prepare("worktree_create", task_id="TASK-001")
        e2 = OperationEntry.prepare("cherry_pick", task_id="TASK-002")
        log.record(e1)
        log.record(e2)

        scanner = RecoveryScanner(log)
        actions = scanner.scan()
        assert len(actions) == 2

    def test_recovery_action_structure(self, log):
        e1 = OperationEntry.prepare("worktree_create", task_id="TASK-001")
        log.record(e1)

        scanner = RecoveryScanner(log)
        actions = scanner.scan()
        action = actions[0]
        assert "operation_id" in action
        assert "operation_type" in action
        assert "task_id" in action
        assert "suggested_action" in action
