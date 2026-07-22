"""Bridge 重试策略与升级降级 — 失败分类、指数退避、升级决策。

设计参考：docs/bridge-design/04-agent-routing-and-cost.md §升级策略
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
    """失败分类器 — 根据错误信息和退出码分类。"""

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
    """重试策略 — 最大重试次数、不可重试类别、指数退避。"""

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
    """升级决策器 — 何时将任务升级给强模型。"""

    def __init__(self, max_retries_before_escalation: int = 2,
                 max_escalations: int = 3, escalation_enabled: bool = True):
        self.max_retries_before_escalation = max_retries_before_escalation
        self.max_escalations = max_escalations
        self.escalation_enabled = escalation_enabled
        self._escalation_count: int = 0

    def should_escalate(self, failure_count: int) -> bool:
        if not self.escalation_enabled:
            return False
        if self._escalation_count >= self.max_escalations:
            return False
        return failure_count >= self.max_retries_before_escalation

    def record_escalation(self):
        self._escalation_count += 1

    def get_escalation_reason(self, task_id: str, failure_count: int,
                              last_error: str = "") -> str:
        return (
            f"Task {task_id} has failed {failure_count} times "
            f"(max retries: {self.max_retries_before_escalation}). "
            f"Last error: {last_error[:200]}. "
            f"Escalating to high-capability agent for diagnosis."
        )


class RetryManager:
    """重试管理器 — 追踪每个任务的重试历史和决策。"""

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
        return self.escalation.should_escalate(self.failure_count(task_id))

    def get_history(self, task_id: str) -> list[dict]:
        return list(self._history.get(task_id, []))
