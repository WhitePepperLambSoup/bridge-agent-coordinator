"""Phase 5.2 tests for retry, escalation, and fallback policies."""

import pytest
from bridgelib.retry import (
    RetryPolicy,
    RetryManager,
    FailureClassifier,
    FailureCategory,
    EscalationDecider,
    RetryError,
)


class TestFailureClassifier:
    """Test failure classification."""

    def test_classify_test_failure(self):
        cat = FailureClassifier.classify(
            error_message="AssertionError: assert 1 == 2",
            exit_code=1,
            check_id="unit-tests",
        )
        assert cat == FailureCategory.TEST_FAILURE

    def test_classify_timeout(self):
        cat = FailureClassifier.classify(
            error_message="Timed out after 300s",
            exit_code=None,
            check_id="lint",
        )
        assert cat == FailureCategory.TIMEOUT

    def test_classify_scope_violation(self):
        cat = FailureClassifier.classify(
            error_message="Path is not in allowed scope",
            exit_code=1,
            check_id="scope-check",
        )
        assert cat == FailureCategory.SCOPE_VIOLATION

    def test_classify_unknown(self):
        cat = FailureClassifier.classify(
            error_message="Something weird happened",
            exit_code=137,
            check_id="unknown-check",
        )
        assert cat == FailureCategory.UNKNOWN


class TestRetryPolicy:
    """Test retry policies."""

    def test_default_policy(self):
        policy = RetryPolicy(max_retries=2)
        assert policy.max_retries == 2
        assert policy.can_retry(0)  # Not started yet.
        assert policy.can_retry(1)  # First retry.
        assert not policy.can_retry(2)  # Retries exhausted.
        assert not policy.can_retry(3)

    def test_cannot_retry_scope_violation(self):
        """Do not retry scope violations."""
        policy = RetryPolicy(max_retries=3)
        assert not policy.can_retry(1, FailureCategory.SCOPE_VIOLATION)

    def test_cannot_retry_security(self):
        policy = RetryPolicy(max_retries=3)
        assert not policy.can_retry(1, FailureCategory.SECURITY)

    def test_retry_delay_increases(self):
        policy = RetryPolicy(max_retries=5, base_delay_seconds=1)
        d1 = policy.delay_for_attempt(1)
        d2 = policy.delay_for_attempt(2)
        d3 = policy.delay_for_attempt(3)
        assert d3 > d2 > d1  # Exponential backoff.


class TestEscalationDecider:
    """Test escalation decisions."""

    def test_no_escalation_on_first_failure(self):
        decider = EscalationDecider(max_retries_before_escalation=2)
        assert not decider.should_escalate(failure_count=1)

    def test_escalation_after_max_retries(self):
        decider = EscalationDecider(max_retries_before_escalation=2)
        assert decider.should_escalate(failure_count=2)
        assert decider.should_escalate(failure_count=3)

    def test_no_escalation_if_not_enabled(self):
        decider = EscalationDecider(max_retries_before_escalation=2, escalation_enabled=False)
        assert not decider.should_escalate(failure_count=5)

    def test_escalation_budget_check(self):
        """Stop escalating after exhausting the escalation budget."""
        decider = EscalationDecider(max_retries_before_escalation=1, max_escalations=2)
        assert decider.should_escalate(failure_count=1)  # First escalation.
        decider.record_escalation()
        assert decider.should_escalate(failure_count=1)  # Second escalation.
        decider.record_escalation()
        assert not decider.should_escalate(failure_count=1)  # Budget exhausted.

    def test_escalation_reason_includes_context(self):
        decider = EscalationDecider()
        reason = decider.get_escalation_reason(
            task_id="TASK-042",
            failure_count=3,
            last_error="Tests consistently fail",
        )
        assert "TASK-042" in reason
        assert "3" in reason


class TestRetryManager:
    """Test the retry manager."""

    @pytest.fixture
    def manager(self):
        return RetryManager()

    def test_record_and_count(self, manager):
        manager.record_failure("TASK-001", "Test failed", 1)
        manager.record_failure("TASK-001", "Test failed again", 1)
        assert manager.failure_count("TASK-001") == 2

    def test_reset_on_success(self, manager):
        manager.record_failure("TASK-001", "Failed", 1)
        manager.reset("TASK-001")
        assert manager.failure_count("TASK-001") == 0

    def test_should_retry_default_policy(self, manager):
        assert manager.should_retry("TASK-001", failure_count=0)
        assert manager.should_retry("TASK-001", failure_count=1)
        assert not manager.should_retry("TASK-001", failure_count=2)  # Default max is 2.

    def test_failure_history_preserved(self, manager):
        manager.record_failure("TASK-001", "Error 1", 1)
        manager.record_failure("TASK-001", "Error 2", 2)
        history = manager.get_history("TASK-001")
        assert len(history) == 2
        assert history[0]["error"] == "Error 1"
