"""Bridge error model — stable error codes and categories.

Design reference: docs/bridge-design/09-database-events-and-config.md §9
"""

from datetime import datetime, timezone
from enum import Enum


class ErrorCategory(Enum):
    CONFIG = "CONFIG"
    PROTOCOL = "PROTOCOL"
    STATE = "STATE"
    WORKSPACE = "WORKSPACE"
    GIT = "GIT"
    LEASE = "LEASE"
    VALIDATION = "VALIDATION"
    SECURITY = "SECURITY"
    BUDGET = "BUDGET"
    STORAGE = "STORAGE"
    INTERNAL = "INTERNAL"


class BridgeError(Exception):
    """Stable error object used by all Bridge errors and their subclasses."""

    def __init__(
        self,
        code: str,
        category: ErrorCategory,
        message: str,
        retryable: bool = False,
        task_id: str | None = None,
        operation_id: str | None = None,
        details: dict | None = None,
        suggested_action: str | None = None,
        created_at_utc: datetime | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message
        self.retryable = retryable
        self.task_id = task_id
        self.operation_id = operation_id
        self.details = details or {}
        self.suggested_action = suggested_action
        self.created_at_utc = created_at_utc or datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "category": self.category.value,
            "message": self.message,
            "retryable": self.retryable,
            "task_id": self.task_id,
            "operation_id": self.operation_id,
            "details": self.details,
            "suggested_action": self.suggested_action,
            "created_at_utc": self.created_at_utc.isoformat(),
        }

    def __str__(self) -> str:
        return f"[{self.category.value}] {self.code}: {self.message}"


# Convenience subclasses, each with the correct category
class ConfigError(BridgeError):
    def __init__(self, code: str, message: str, **kwargs):
        super().__init__(code, ErrorCategory.CONFIG, message, **kwargs)


class ProtocolError(BridgeError):
    def __init__(self, code: str, message: str, **kwargs):
        super().__init__(code, ErrorCategory.PROTOCOL, message, **kwargs)


class StateError(BridgeError):
    def __init__(self, code: str, message: str, **kwargs):
        super().__init__(code, ErrorCategory.STATE, message, **kwargs)


class WorkspaceError(BridgeError):
    def __init__(self, code: str, message: str, **kwargs):
        super().__init__(code, ErrorCategory.WORKSPACE, message, **kwargs)


class GitError(BridgeError):
    def __init__(self, code: str, message: str, **kwargs):
        super().__init__(code, ErrorCategory.GIT, message, **kwargs)


class LeaseError(BridgeError):
    def __init__(self, code: str, message: str, **kwargs):
        super().__init__(code, ErrorCategory.LEASE, message, **kwargs)


class ValidationError(BridgeError):
    def __init__(self, code: str, message: str, **kwargs):
        super().__init__(code, ErrorCategory.VALIDATION, message, **kwargs)


class SecurityError(BridgeError):
    def __init__(self, code: str, message: str, **kwargs):
        super().__init__(code, ErrorCategory.SECURITY, message, **kwargs)


class BudgetError(BridgeError):
    def __init__(self, code: str, message: str, **kwargs):
        super().__init__(code, ErrorCategory.BUDGET, message, **kwargs)


class StorageError(BridgeError):
    def __init__(self, code: str, message: str, **kwargs):
        super().__init__(code, ErrorCategory.STORAGE, message, **kwargs)


class InternalError(BridgeError):
    def __init__(self, code: str, message: str, **kwargs):
        super().__init__(code, ErrorCategory.INTERNAL, message, **kwargs)
