"""Bridge serial merge queue with FIFO integration, conflict handling, and status tracking.

Design reference: docs/bridge-design/06-git-worktree-and-conflicts.md, Serial Merge Queue section
"""

import secrets
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field


class MergeStatus:
    QUEUED = "queued"
    MERGING = "merging"
    MERGED = "merged"
    CONFLICT = "conflict"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MergeError(Exception):
    pass


@dataclass
class MergeEntry:
    entry_id: str
    task_id: str
    target_branch: str
    candidate_commit: str = ""
    queue_position: int = 0
    status: str = MergeStatus.QUEUED
    result: str = ""
    created_at: str = ""
    merged_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "task_id": self.task_id,
            "target_branch": self.target_branch,
            "candidate_commit": self.candidate_commit,
            "queue_position": self.queue_position,
            "status": self.status,
            "result": self.result,
            "created_at": self.created_at,
            "merged_at": self.merged_at,
        }


class MergeQueue:
    """Serial merge queue that permits only one merge operation at a time."""

    def __init__(self, target_branch: str = "main"):
        self.target_branch = target_branch
        self._entries: dict[str, MergeEntry] = {}
        self._next_position: int = 0
        self._lock = threading.Lock()

    def enqueue(self, task_id: str, candidate_commit: str = "") -> MergeEntry:
        with self._lock:
            entry_id = f"mq-{secrets.token_hex(6)}"
            entry = MergeEntry(
                entry_id=entry_id,
                task_id=task_id,
                target_branch=self.target_branch,
                candidate_commit=candidate_commit,
                queue_position=self._next_position,
            )
            self._entries[entry_id] = entry
            self._next_position += 1
            return entry

    def start_merge(self, entry_id: str) -> MergeEntry:
        with self._lock:
            entry = self._get_or_raise(entry_id)

            if entry.status != MergeStatus.QUEUED:
                raise MergeError(
                    f"Cannot start merge: entry {entry_id} is {entry.status}, not queued"
                )

            # Check whether a merge is already in progress.
            merging = [
                e for e in self._entries.values()
                if e.status == MergeStatus.MERGING
            ]
            if merging:
                raise MergeError(
                    f"Cannot start merge: {merging[0].entry_id} is already merging"
                )

            # Enforce FIFO: only the first queued entry can start.
            queued = self.list_queued()
            if queued and queued[0].entry_id != entry_id:
                raise MergeError(
                    f"Cannot start merge: entry {entry_id} is not at the front of the queue. "
                    f"Front is {queued[0].entry_id} (position {queued[0].queue_position})"
                )

            entry.status = MergeStatus.MERGING
            return entry

    def complete_merge(self, entry_id: str, result: str = "") -> MergeEntry:
        with self._lock:
            entry = self._get_or_raise(entry_id)
            if entry.status != MergeStatus.MERGING:
                raise MergeError(f"Entry {entry_id} is not merging")
            entry.status = MergeStatus.MERGED
            entry.result = result
            entry.merged_at = datetime.now(timezone.utc).isoformat()
            return entry

    def mark_conflict(self, entry_id: str, conflict_info: str = "") -> MergeEntry:
        with self._lock:
            entry = self._get_or_raise(entry_id)
            entry.status = MergeStatus.CONFLICT
            entry.result = conflict_info
            return entry

    def cancel(self, entry_id: str) -> MergeEntry:
        with self._lock:
            entry = self._get_or_raise(entry_id)
            if entry.status in (MergeStatus.MERGED,):
                raise MergeError(f"Cannot cancel already merged entry {entry_id}")
            entry.status = MergeStatus.CANCELLED
            return entry

    def get(self, entry_id: str) -> MergeEntry | None:
        return self._entries.get(entry_id)

    def get_by_task(self, task_id: str) -> list[MergeEntry]:
        return [e for e in self._entries.values() if e.task_id == task_id]

    def list_queued(self) -> list[MergeEntry]:
        return sorted(
            [e for e in self._entries.values() if e.status == MergeStatus.QUEUED],
            key=lambda e: e.queue_position,
        )

    def list_history(self) -> list[MergeEntry]:
        return [
            e for e in self._entries.values()
            if e.status in (MergeStatus.MERGED, MergeStatus.CONFLICT, MergeStatus.FAILED)
        ]

    def next_queued(self) -> MergeEntry | None:
        queued = self.list_queued()
        return queued[0] if queued else None

    def _get_or_raise(self, entry_id: str) -> MergeEntry:
        entry = self._entries.get(entry_id)
        if entry is None:
            raise MergeError(f"Merge entry {entry_id} not found")
        return entry
