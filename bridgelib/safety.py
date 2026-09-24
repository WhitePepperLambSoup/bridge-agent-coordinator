"""Bridge safety policy engine with confirmation modes and immutable hard limits.

Design reference: docs/bridge-design/07-safety-and-confirmations.md
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Callable


class ConfirmationMode(Enum):
    STRICT = "strict"       # Confirm all writes, merges, cleanup, and external commands
    BALANCED = "balanced"   # Default: automate low risk and confirm high risk
    EXPERT = "expert"       # Most confirmations can be disabled, but hard limits remain


class ActionPolicy(Enum):
    AUTO = "auto"                       # Execute automatically
    CONFIRM_ONCE = "confirm_once"       # Confirm once per session
    CONFIRM_SESSION = "confirm_session"
    CONFIRM_PROJECT = "confirm_project"
    ALWAYS_CONFIRM = "always_confirm"   # Always confirm
    DISABLED = "disabled"               # Disable this action


# Immutable safety limits that every mode must reject
HARD_FLOOR_ACTIONS = frozenset({
    "delete_user_directory",         # Delete a non-temporary user directory with uncommitted changes
    "write_outside_workspace",       # Write outside the project scope
    "force_push",                    # Force push or rewrite shared history
    "delete_default_branch",         # Delete the default or main branch
    "expose_secret",                 # Output, commit, or copy a detected secret
    "overwrite_unknown_file",        # Overwrite a file whose ownership cannot be established
    "path_resolution_failure",       # Path resolution failure or symlink escape
    "db_git_mismatch_autofix",       # Automatically repair a database/Git inconsistency
    "agent_privilege_escalation",    # Let an agent expand permissions through a receipt
})


@dataclass
class ActionRule:
    """Safety rule for a single action."""
    action_id: str
    policy: ActionPolicy = ActionPolicy.ALWAYS_CONFIRM
    description: str = ""


class SafetyPolicy:
    """Determine whether actions require confirmation or can be automated."""

    # Default balanced policy
    DEFAULT_RULES: dict[str, ActionPolicy] = {
        "create_worktree": ActionPolicy.AUTO,
        "remove_clean_worktree": ActionPolicy.AUTO,
        "remove_dirty_worktree": ActionPolicy.ALWAYS_CONFIRM,
        "merge_low_risk": ActionPolicy.AUTO,
        "merge_medium_risk": ActionPolicy.CONFIRM_ONCE,
        "merge_high_risk": ActionPolicy.ALWAYS_CONFIRM,
        "overwrite_bridge_files": ActionPolicy.ALWAYS_CONFIRM,
        "run_approved_checks": ActionPolicy.AUTO,
        "run_custom_command": ActionPolicy.ALWAYS_CONFIRM,
        "release_expired_clean_lease": ActionPolicy.CONFIRM_ONCE,
        "revert_merged_commit": ActionPolicy.ALWAYS_CONFIRM,
        "invoke_expensive_agent": ActionPolicy.ALWAYS_CONFIRM,
        "task_assignment": ActionPolicy.AUTO,
        "task_approval": ActionPolicy.CONFIRM_ONCE,
        "receipt_import": ActionPolicy.AUTO,
        "validation_run": ActionPolicy.AUTO,
        "review_submit": ActionPolicy.AUTO,
        "review_approve": ActionPolicy.CONFIRM_ONCE,
        "merge_enqueue": ActionPolicy.CONFIRM_ONCE,
    }

    def __init__(self, mode: ConfirmationMode = ConfirmationMode.BALANCED,
                 overrides: dict[str, ActionPolicy] | None = None,
                 on_override: Callable[[str, ActionPolicy], None] | None = None):
        self.mode = mode
        self._rules = dict(self.DEFAULT_RULES)
        self._on_override = on_override
        if overrides:
            self._rules.update(overrides)

    def requires_confirmation(self, action_id: str) -> bool:
        """Determine whether an action requires user confirmation.

        P1 fix: DISABLED means the action is prohibited regardless of confirmation.
        """
        # Hard limits are always rejected
        if action_id in HARD_FLOOR_ACTIONS:
            return True

        policy = self._rules.get(action_id, ActionPolicy.ALWAYS_CONFIRM)

        # DISABLED always requires confirmation to prevent automated execution
        if policy == ActionPolicy.DISABLED:
            return True

        if self.mode == ConfirmationMode.STRICT:
            return True
        elif self.mode == ConfirmationMode.BALANCED:
            return policy != ActionPolicy.AUTO
        elif self.mode == ConfirmationMode.EXPERT:
            return policy == ActionPolicy.ALWAYS_CONFIRM

        return True

    def is_hard_floor(self, action_id: str) -> bool:
        """Check whether an action reaches an immutable safety limit."""
        return action_id in HARD_FLOOR_ACTIONS

    def can_automate(self, action_id: str) -> bool:
        """Check whether an action can be automated."""
        if action_id in HARD_FLOOR_ACTIONS:
            return False
        policy = self._rules.get(action_id, ActionPolicy.ALWAYS_CONFIRM)
        if policy == ActionPolicy.DISABLED:
            return False
        return not self.requires_confirmation(action_id)

    def is_disabled(self, action_id: str) -> bool:
        """Check whether an action is fully disabled.

        P1 fix: a DISABLED action cannot run even when confirmed=True.
        """
        if action_id in HARD_FLOOR_ACTIONS:
            return True
        policy = self._rules.get(action_id, ActionPolicy.ALWAYS_CONFIRM)
        return policy == ActionPolicy.DISABLED

    def check_allowed(self, action_id: str, confirmed: bool = False) -> bool:
        """Apply the common safety gate to determine whether an action may run.

        P1 fix: all write operations must pass through this gate.
        - HARD_FLOOR: reject completely.
        - DISABLED: reject completely, even when confirmed=True.
        - Confirmation required but absent: reject.
        - Otherwise: allow.
        """
        if self.is_hard_floor(action_id):
            return False
        if self.is_disabled(action_id):
            return False
        if self.requires_confirmation(action_id) and not confirmed:
            return False
        return True

    def get_policy(self, action_id: str) -> ActionPolicy:
        """Get the safety policy for an action."""
        return self._rules.get(action_id, ActionPolicy.ALWAYS_CONFIRM)

    def set_override(self, action_id: str, policy: ActionPolicy):
        """Set a user override for an action's safety level."""
        if action_id in HARD_FLOOR_ACTIONS and policy != ActionPolicy.ALWAYS_CONFIRM:
            raise ValueError(
                f"Cannot override hard floor action '{action_id}' "
                f"to {policy.value} — this action always requires confirmation"
            )
        if self._on_override:
            self._on_override(action_id, policy)
        self._rules[action_id] = policy

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "rules": {k: v.value for k, v in self._rules.items()},
        }


# Default factory functions
def create_strict_policy() -> SafetyPolicy:
    return SafetyPolicy(mode=ConfirmationMode.STRICT)


def create_balanced_policy() -> SafetyPolicy:
    return SafetyPolicy(mode=ConfirmationMode.BALANCED)


def create_expert_policy() -> SafetyPolicy:
    return SafetyPolicy(mode=ConfirmationMode.EXPERT)
