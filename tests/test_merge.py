"""Phase 3.2 tests - serial merge queue."""

import pytest
from bridgelib.merge import (
    MergeQueue,
    MergeEntry,
    MergeStatus,
    MergeError,
)


class TestMergeEntry:
    def test_create_entry(self):
        entry = MergeEntry(
            entry_id="mq-001",
            task_id="TASK-014",
            target_branch="main",
            candidate_commit="abc123",
            queue_position=0,
            status=MergeStatus.QUEUED,
        )
        assert entry.task_id == "TASK-014"
        assert entry.status == MergeStatus.QUEUED
        assert entry.queue_position == 0

    def test_entry_to_dict(self):
        entry = MergeEntry(
            entry_id="mq-002",
            task_id="TASK-099",
            target_branch="main",
            candidate_commit="def456",
            queue_position=3,
        )
        d = entry.to_dict()
        assert d["entry_id"] == "mq-002"
        assert d["queue_position"] == 3


class TestMergeQueue:
    @pytest.fixture
    def queue(self):
        return MergeQueue(target_branch="main")

    def test_enqueue(self, queue):
        entry = queue.enqueue(
            task_id="TASK-001",
            candidate_commit="abc123",
        )
        assert entry.status == MergeStatus.QUEUED
        assert entry.queue_position == 0

    def test_fifo_order(self, queue):
        e1 = queue.enqueue(task_id="TASK-001", candidate_commit="a")
        e2 = queue.enqueue(task_id="TASK-002", candidate_commit="b")
        e3 = queue.enqueue(task_id="TASK-003", candidate_commit="c")
        assert e1.queue_position == 0
        assert e2.queue_position == 1
        assert e3.queue_position == 2

    def test_start_merge(self, queue):
        entry = queue.enqueue(task_id="TASK-001", candidate_commit="abc")
        started = queue.start_merge(entry.entry_id)
        assert started.status == MergeStatus.MERGING

    def test_complete_merge(self, queue):
        entry = queue.enqueue(task_id="TASK-001", candidate_commit="abc")
        queue.start_merge(entry.entry_id)
        completed = queue.complete_merge(entry.entry_id, result="merged as xyz789")
        assert completed.status == MergeStatus.MERGED
        assert completed.result == "merged as xyz789"

    def test_mark_conflict(self, queue):
        entry = queue.enqueue(task_id="TASK-001", candidate_commit="abc")
        queue.start_merge(entry.entry_id)
        conflicted = queue.mark_conflict(entry.entry_id, "src/auth.py has conflict")
        assert conflicted.status == MergeStatus.CONFLICT
        assert "src/auth.py" in conflicted.result

    def test_cancel_entry(self, queue):
        entry = queue.enqueue(task_id="TASK-001", candidate_commit="abc")
        cancelled = queue.cancel(entry.entry_id)
        assert cancelled.status == MergeStatus.CANCELLED

    def test_only_one_merging_at_a_time(self, queue):
        """Only one serial merge can have MERGING status at a time."""
        e1 = queue.enqueue(task_id="TASK-001", candidate_commit="a")
        e2 = queue.enqueue(task_id="TASK-002", candidate_commit="b")
        queue.start_merge(e1.entry_id)
        with pytest.raises(MergeError):
            queue.start_merge(e2.entry_id)

    def test_cannot_start_non_queued(self, queue):
        entry = queue.enqueue(task_id="TASK-001", candidate_commit="abc")
        queue.start_merge(entry.entry_id)
        # An entry already in MERGING cannot be started again.
        with pytest.raises(MergeError):
            queue.start_merge(entry.entry_id)

    def test_list_queued(self, queue):
        queue.enqueue(task_id="TASK-001", candidate_commit="a")
        queue.enqueue(task_id="TASK-002", candidate_commit="b")
        queued = queue.list_queued()
        assert len(queued) == 2

    def test_list_history(self, queue):
        e1 = queue.enqueue(task_id="TASK-001", candidate_commit="a")
        queue.start_merge(e1.entry_id)
        queue.complete_merge(e1.entry_id, "done")
        e2 = queue.enqueue(task_id="TASK-002", candidate_commit="b")
        history = queue.list_history()
        assert len(history) == 1  # Only TASK-001 is complete.

    def test_get_by_task(self, queue):
        queue.enqueue(task_id="TASK-001", candidate_commit="a")
        entries = queue.get_by_task("TASK-001")
        assert len(entries) == 1

    def test_nonexistent_entry(self, queue):
        with pytest.raises(MergeError):
            queue.start_merge("nonexistent")

    def test_next_queued(self, queue):
        """next_queued returns the first queued merge entry."""
        queue.enqueue(task_id="TASK-001", candidate_commit="a")
        queue.enqueue(task_id="TASK-002", candidate_commit="b")
        next_entry = queue.next_queued()
        assert next_entry.task_id == "TASK-001"
