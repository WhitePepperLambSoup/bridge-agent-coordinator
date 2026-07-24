"""Bridge operation logging and crash recovery using the Prepare/Execute/Complete pattern.

Design references: docs/bridge-design/09-database-events-and-config.md section 5 and 06 Crash Recovery
"""

import secrets
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field


class OperationStatus:
    PREPARED = "prepared"
    COMPLETED = "completed"
    FAILED = "failed"


class OpError(Exception):
    pass


@dataclass
class OperationEntry:
    operation_id: str
    operation_type: str          # merge, cherry_pick, worktree_create, worktree_remove, revert
    task_id: str = ""
    target: str = ""             # Operation target description
    expected_baseline: str = ""  # Expected baseline for the Git operation
    idempotency_key: str = ""    # Idempotency key
    status: str = OperationStatus.PREPARED
    result: str = ""
    error: str = ""
    created_at: str = ""
    completed_at: str = ""

    @classmethod
    def prepare(
        cls, operation_type: str, task_id: str = "", target: str = "",
        expected_baseline: str = "", idempotency_key: str = "",
    ) -> "OperationEntry":
        return cls(
            operation_id=f"op-{secrets.token_hex(8)}",
            operation_type=operation_type,
            task_id=task_id,
            target=target,
            expected_baseline=expected_baseline,
            idempotency_key=idempotency_key or f"{operation_type}-{task_id}-{secrets.token_hex(4)}",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def mark_completed(self, result: str = ""):
        self.status = OperationStatus.COMPLETED
        self.result = result
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def mark_failed(self, error: str = ""):
        self.status = OperationStatus.FAILED
        self.error = error
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "operation_id": self.operation_id,
            "operation_type": self.operation_type,
            "task_id": self.task_id,
            "target": self.target,
            "expected_baseline": self.expected_baseline,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


# ── Operation Log ─────────────────────────────────────────

class OperationLog:
    """Log of all operations that could be interrupted by a crash."""

    def __init__(self):
        self._entries: dict[str, OperationEntry] = {}
        self._idempotency_keys: set[str] = set()
        self._lock = threading.Lock()

    def record(self, entry: OperationEntry) -> str:
        """Record an OperationPrepared entry, raising OpError for a duplicate idempotency key."""
        with self._lock:
            if entry.idempotency_key and entry.idempotency_key in self._idempotency_keys:
                existing = self._find_by_idempotency_key(entry.idempotency_key)
                raise OpError(
                    f"Duplicate idempotency key: {entry.idempotency_key} "
                    f"(existing: {existing.operation_id if existing else 'unknown'})"
                )
            self._entries[entry.operation_id] = entry
            if entry.idempotency_key:
                self._idempotency_keys.add(entry.idempotency_key)
            return entry.operation_id

    def update(self, entry: OperationEntry):
        """Update operation status after completion or failure."""
        with self._lock:
            if entry.operation_id not in self._entries:
                raise OpError(f"Operation {entry.operation_id} not found")
            self._entries[entry.operation_id] = entry

    def get(self, operation_id: str) -> OperationEntry | None:
        return self._entries.get(operation_id)

    def list_incomplete(self) -> list[OperationEntry]:
        """List all incomplete operations in PREPARED status."""
        return [
            e for e in self._entries.values()
            if e.status == OperationStatus.PREPARED
        ]

    def list_by_task(self, task_id: str) -> list[OperationEntry]:
        return [e for e in self._entries.values() if e.task_id == task_id]

    def _find_by_idempotency_key(self, key: str) -> OperationEntry | None:
        for e in self._entries.values():
            if e.idempotency_key == key:
                return e
        return None


# ── Recovery Scanner ─────────────────────────────────────

class RecoveryScanner:
    """Crash recovery scanner that finds incomplete operations and suggests recovery actions at startup."""

    def __init__(self, operation_log: OperationLog):
        self.log = operation_log

    def scan(self) -> list[dict]:
        """Scan incomplete operations and return a list of recovery actions."""
        incomplete = self.log.list_incomplete()
        actions = []
        for entry in incomplete:
            actions.append({
                "operation_id": entry.operation_id,
                "operation_type": entry.operation_type,
                "task_id": entry.task_id,
                "target": entry.target,
                "created_at": entry.created_at,
                "suggested_action": self._suggest(entry),
            })
        return actions

    def _suggest(self, entry: OperationEntry) -> str:
        """Suggest a recovery action based on the operation type."""
        suggestions = {
            "merge": "Check git status; if merge completed, mark as completed; otherwise abort and retry.",
            "cherry_pick": "Check if cherry-pick succeeded; if conflict, resolve or abort.",
            "worktree_create": "Check if worktree exists; if yes, mark completed; if no, retry creation.",
            "worktree_remove": "Check if worktree still exists; if removed, mark completed; otherwise retry.",
            "revert": "Check if revert commit exists; if yes, mark completed; otherwise retry.",
        }
        return suggestions.get(entry.operation_type, "Manually verify operation state and mark as completed or failed.")
