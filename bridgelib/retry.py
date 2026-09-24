"""Bridge retry and escalation policies with failure classification and backoff.

Design reference: docs/bridge-design/04-agent-routing-and-cost.md, escalation policy
"""

import threading
from enum import Enum
from datetime import datetime, timezone


class FailureCategory(Enum):
    TEST_FAILURE = "test_failure"
    TIMEOUT = "timeout"
    SCOPE_VIOLATION = "scope_violation"
    SECURITY = "security"
    BUILD_ERROR = "build_error"
    LINT_ERROR = "lint_error"
    UNKNOWN = "unknown"


class RetryError(Exception):
    pass


class FailureClassifier:
    """Classify failures from error messages and exit codes."""

    @staticmethod
    def classify(error_message: str, exit_code: int | None = None,
                 check_id: str = "") -> FailureCategory:
        msg = error_message.lower()

        if "timed out" in msg or "timeout" in msg:
            return FailureCategory.TIMEOUT

        if "not in allowed" in msg or "scope" in msg or "forbidden" in msg:
            return FailureCategory.SCOPE_VIOLATION

        if "security" in msg or "secret" in msg or "credential" in msg:
            return FailureCategory.SECURITY

        if "syntax" in msg or "build" in msg or "compile" in msg:
            return FailureCategory.BUILD_ERROR

        if "lint" in msg or "style" in msg or "format" in msg:
            return FailureCategory.LINT_ERROR

        if "assert" in msg or "failed" in msg or exit_code == 1:
            return FailureCategory.TEST_FAILURE

        return FailureCategory.UNKNOWN


class RetryPolicy:
    """Retry policy with limits, unretryable categories, and exponential backoff."""

    UNRETRYABLE = {FailureCategory.SCOPE_VIOLATION, FailureCategory.SECURITY}

    def __init__(self, max_retries: int = 2, base_delay_seconds: float = 1.0,
                 max_delay_seconds: float = 60.0):
        self.max_retries = max_retries
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds

    def can_retry(self, failure_count: int, category: FailureCategory | None = None) -> bool:
        if category in self.UNRETRYABLE:
            return False
        return failure_count < self.max_retries

    def delay_for_attempt(self, attempt: int) -> float:
        delay = self.base_delay_seconds * (2 ** (attempt - 1))
        return min(delay, self.max_delay_seconds)


class EscalationDecider:
    """Decide when to escalate a task to a more capable model."""

    def __init__(self, max_retries_before_escalation: int = 2,
                 max_escalations: int = 3, escalation_enabled: bool = True):
        self.max_retries_before_escalation = max_retries_before_escalation
        self.max_escalations = max_escalations
        self.escalation_enabled = escalation_enabled
        self._escalation_count: int = 0
        self._last_reason: str = ""
        self._last_errors: dict[str, str] = {}

    def should_escalate(self, failure_count: int, task_id: str = "",
                        last_error: str = "") -> bool:
        if not self.escalation_enabled:
            return False
        if self._escalation_count >= self.max_escalations:
            return False
        should = failure_count >= self.max_retries_before_escalation
        if should:
            if last_error:
                self._last_errors[task_id] = last_error
            self._last_reason = (
                f"Task failed {failure_count} times "
                f"(threshold: {self.max_retries_before_escalation}). "
                f"Last error: {last_error[:200]}."
            )
        return should

    def record_escalation(self):
        self._escalation_count += 1

    def get_escalation_reason(self, task_id: str, failure_count: int,
                              last_error: str = "") -> str:
        reason = (
            f"Task {task_id} has failed {failure_count} times "
            f"(max retries: {self.max_retries_before_escalation}). "
            f"Last error: {last_error[:200]}. "
            f"Escalating to high-capability agent for diagnosis."
        )
        self._last_reason = reason
        return reason

    @property
    def last_reason(self) -> str:
        return self._last_reason


class RetryManager:
    """Track retry history and decisions for each task."""

    def __init__(self, policy: RetryPolicy | None = None,
                 escalation: EscalationDecider | None = None):
        self.policy = policy or RetryPolicy()
        self.escalation = escalation or EscalationDecider()
        self._history: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def record_failure(self, task_id: str, error_message: str, exit_code: int | None = None,
                       check_id: str = ""):
        category = FailureClassifier.classify(error_message, exit_code, check_id)
        entry = {
            "task_id": task_id,
            "error": error_message[:500],
            "exit_code": exit_code,
            "check_id": check_id,
            "category": category.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._history.setdefault(task_id, []).append(entry)

    def failure_count(self, task_id: str) -> int:
        return len(self._history.get(task_id, []))

    def reset(self, task_id: str):
        with self._lock:
            self._history.pop(task_id, None)

    def should_retry(self, task_id: str, failure_count: int | None = None,
                     category: FailureCategory | None = None) -> bool:
        count = failure_count if failure_count is not None else self.failure_count(task_id)
        return self.policy.can_retry(count, category)

    def should_escalate(self, task_id: str) -> bool:
        count = self.failure_count(task_id)
        last_error = ""
        history = self._history.get(task_id, [])
        if history:
            last_error = history[-1].get("error", "")
        return self.escalation.should_escalate(count, task_id=task_id,
                                                last_error=last_error)

    def get_history(self, task_id: str) -> list[dict]:
        return list(self._history.get(task_id, []))

    def get_last_error(self, task_id: str) -> str:
        history = self._history.get(task_id, [])
        return history[-1].get("error", "") if history else ""

    def restore_from_records(self, records: list[dict]):
        """Restore retry history from database records."""
        with self._lock:
            for rec in records:
                tid = rec.get("task_id", "")
                if tid:
                    self._history.setdefault(tid, []).append(rec)
