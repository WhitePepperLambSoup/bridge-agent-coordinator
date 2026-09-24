"""Phase 1.1 tests - error model."""

import pytest
from datetime import datetime, timezone
from bridgelib.errors import (
    BridgeError,
    ErrorCategory,
    ConfigError,
    ProtocolError,
    StateError,
    WorkspaceError,
    GitError,
    LeaseError,
    ValidationError,
    SecurityError,
    BudgetError,
    StorageError,
    InternalError,
)


class TestErrorCategories:
    """Error category enumeration."""

    def test_all_categories_defined(self):
        expected = [
            "CONFIG", "PROTOCOL", "STATE", "WORKSPACE", "GIT",
            "LEASE", "VALIDATION", "SECURITY", "BUDGET",
            "STORAGE", "INTERNAL",
        ]
        for cat in expected:
            assert hasattr(ErrorCategory, cat), f"Missing category: {cat}"

    def test_category_values_are_strings(self):
        for cat in ErrorCategory:
            assert isinstance(cat.value, str)


class TestBridgeError:
    """Base error class."""

    def test_minimal_creation(self):
        err = BridgeError(
            code="TEST_ERROR",
            category=ErrorCategory.INTERNAL,
            message="Something went wrong",
        )
        assert err.code == "TEST_ERROR"
        assert err.category == ErrorCategory.INTERNAL
        assert err.message == "Something went wrong"
        assert err.retryable is False
        assert err.task_id is None
        assert err.operation_id is None
        assert err.details == {}
        assert err.suggested_action is None

    def test_full_creation(self):
        now = datetime.now(timezone.utc)
        err = BridgeError(
            code="LEASE_OWNER_MISMATCH",
            category=ErrorCategory.LEASE,
            message="Lease owner does not match agent",
            retryable=False,
            task_id="TASK-014",
            operation_id="op_abc123",
            details={"expected": "reasonix-worker", "actual": "unknown"},
            suggested_action="Regenerate the task package",
            created_at_utc=now,
        )
        assert err.task_id == "TASK-014"
        assert err.operation_id == "op_abc123"
        assert err.details["expected"] == "reasonix-worker"
        assert err.suggested_action == "Regenerate the task package"
        assert err.created_at_utc == now

    def test_to_dict(self):
        err = BridgeError(
            code="LEASE_OWNER_MISMATCH",
            category=ErrorCategory.LEASE,
            message="Mismatch",
            retryable=False,
            task_id="TASK-014",
            suggested_action="Retry",
        )
        d = err.to_dict()
        assert d["code"] == "LEASE_OWNER_MISMATCH"
        assert d["category"] == "LEASE"
        assert d["message"] == "Mismatch"
        assert d["retryable"] is False
        assert d["task_id"] == "TASK-014"
        assert d["suggested_action"] == "Retry"

    def test_str_representation(self):
        err = BridgeError(
            code="TEST_ERROR",
            category=ErrorCategory.INTERNAL,
            message="Something broke",
        )
        s = str(err)
        assert "TEST_ERROR" in s
        assert "Something broke" in s

    def test_default_created_at(self):
        err = BridgeError(
            code="TEST",
            category=ErrorCategory.INTERNAL,
            message="test",
        )
        assert err.created_at_utc is not None


class TestSubclassErrors:
    """Default category for each error subclass."""

    def test_config_error(self):
        err = ConfigError("BAD_CONFIG", "Invalid config")
        assert err.category == ErrorCategory.CONFIG

    def test_protocol_error(self):
        err = ProtocolError("BAD_RECEIPT", "Invalid receipt")
        assert err.category == ErrorCategory.PROTOCOL

    def test_state_error(self):
        err = StateError("BAD_TRANSITION", "Cannot transition from Done to InProgress")
        assert err.category == ErrorCategory.STATE

    def test_workspace_error(self):
        err = WorkspaceError("DIRTY_WORKTREE", "Worktree has uncommitted changes")
        assert err.category == ErrorCategory.WORKSPACE

    def test_git_error(self):
        err = GitError("MERGE_CONFLICT", "Merge conflict detected")
        assert err.category == ErrorCategory.GIT

    def test_lease_error(self):
        err = LeaseError("LEASE_EXPIRED", "Lease has expired")
        assert err.category == ErrorCategory.LEASE

    def test_validation_error(self):
        err = ValidationError("CHECK_FAILED", "Unit tests failed")
        assert err.category == ErrorCategory.VALIDATION

    def test_security_error(self):
        err = SecurityError("PATH_ESCAPE", "Path traversal detected")
        assert err.category == ErrorCategory.SECURITY

    def test_budget_error(self):
        err = BudgetError("BUDGET_EXCEEDED", "Token budget exceeded")
        assert err.category == ErrorCategory.BUDGET

    def test_storage_error(self):
        err = StorageError("DB_CORRUPT", "Database is corrupt")
        assert err.category == ErrorCategory.STORAGE

    def test_internal_error(self):
        err = InternalError("UNEXPECTED", "Unexpected state")
        assert err.category == ErrorCategory.INTERNAL
