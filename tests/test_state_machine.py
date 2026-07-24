"""Phase 1.4 tests for the task state machine and progression policies."""

import pytest
from bridgelib.state_machine import (
    TaskState,
    ProgressionPolicy,
    is_valid_transition,
    get_allowed_transitions,
    validate_transition,
    StateMachineError,
)


class TestTaskStateEnum:
    """Test the task state enum."""

    def test_all_17_states_defined(self):
        expected = [
            "DRAFT", "PLANNING", "READY", "ASSIGNED", "IN_PROGRESS",
            "SUBMITTED", "VALIDATING", "APPROVED", "MERGE_QUEUED",
            "MERGING", "DONE", "BLOCKED", "REVISION_REQUIRED",
            "ESCALATED", "CONFLICT", "STALE", "CANCELLED",
        ]
        for s in expected:
            assert hasattr(TaskState, s), f"Missing state: {s}"

    def test_terminal_states(self):
        """Treat DONE and CANCELLED as terminal states."""
        assert TaskState.is_terminal(TaskState.DONE)
        assert TaskState.is_terminal(TaskState.CANCELLED)
        assert not TaskState.is_terminal(TaskState.DRAFT)
        assert not TaskState.is_terminal(TaskState.IN_PROGRESS)


class TestValidTransitions:
    """Test valid state transitions."""

    def test_draft_to_planning(self):
        assert is_valid_transition(TaskState.DRAFT, TaskState.PLANNING)

    def test_draft_to_cancelled(self):
        assert is_valid_transition(TaskState.DRAFT, TaskState.CANCELLED)

    def test_planning_to_ready(self):
        assert is_valid_transition(TaskState.PLANNING, TaskState.READY)

    def test_ready_to_assigned(self):
        assert is_valid_transition(TaskState.READY, TaskState.ASSIGNED)

    def test_assigned_to_in_progress(self):
        assert is_valid_transition(TaskState.ASSIGNED, TaskState.IN_PROGRESS)

    def test_in_progress_to_submitted(self):
        assert is_valid_transition(TaskState.IN_PROGRESS, TaskState.SUBMITTED)

    def test_submitted_to_validating(self):
        assert is_valid_transition(TaskState.SUBMITTED, TaskState.VALIDATING)

    def test_validating_to_approved(self):
        assert is_valid_transition(TaskState.VALIDATING, TaskState.APPROVED)

    def test_validating_to_revision_required(self):
        assert is_valid_transition(TaskState.VALIDATING, TaskState.REVISION_REQUIRED)

    def test_validating_to_escalated(self):
        assert is_valid_transition(TaskState.VALIDATING, TaskState.ESCALATED)

    def test_validating_to_conflict(self):
        assert is_valid_transition(TaskState.VALIDATING, TaskState.CONFLICT)

    def test_revision_required_to_assigned(self):
        assert is_valid_transition(TaskState.REVISION_REQUIRED, TaskState.ASSIGNED)

    def test_escalated_to_assigned(self):
        assert is_valid_transition(TaskState.ESCALATED, TaskState.ASSIGNED)

    def test_conflict_to_assigned(self):
        assert is_valid_transition(TaskState.CONFLICT, TaskState.ASSIGNED)

    def test_approved_to_merge_queued(self):
        assert is_valid_transition(TaskState.APPROVED, TaskState.MERGE_QUEUED)

    def test_merge_queued_to_merging(self):
        assert is_valid_transition(TaskState.MERGE_QUEUED, TaskState.MERGING)

    def test_merging_to_done(self):
        assert is_valid_transition(TaskState.MERGING, TaskState.DONE)

    def test_merging_to_conflict(self):
        assert is_valid_transition(TaskState.MERGING, TaskState.CONFLICT)

    def test_in_progress_to_blocked(self):
        assert is_valid_transition(TaskState.IN_PROGRESS, TaskState.BLOCKED)

    def test_blocked_to_assigned(self):
        assert is_valid_transition(TaskState.BLOCKED, TaskState.ASSIGNED)

    def test_assigned_to_stale(self):
        assert is_valid_transition(TaskState.ASSIGNED, TaskState.STALE)

    def test_stale_to_ready(self):
        assert is_valid_transition(TaskState.STALE, TaskState.READY)


class TestInvalidTransitions:
    """Test invalid state transitions."""

    def test_done_to_anything(self):
        """Prevent transitions from a terminal state to any other state."""
        for target in TaskState:
            if target != TaskState.DONE:
                assert not is_valid_transition(TaskState.DONE, target), \
                    f"DONE -> {target.name} should be invalid"

    def test_cancelled_to_anything(self):
        for target in TaskState:
            if target != TaskState.CANCELLED:
                assert not is_valid_transition(TaskState.CANCELLED, target), \
                    f"CANCELLED -> {target.name} should be invalid"

    def test_skip_states(self):
        """Prevent skipped states."""
        assert not is_valid_transition(TaskState.DRAFT, TaskState.IN_PROGRESS)
        assert not is_valid_transition(TaskState.READY, TaskState.SUBMITTED)
        assert not is_valid_transition(TaskState.IN_PROGRESS, TaskState.APPROVED)

    def test_backward_jump(self):
        """Prevent backward transitions."""
        assert not is_valid_transition(TaskState.VALIDATING, TaskState.DRAFT)
        assert not is_valid_transition(TaskState.MERGING, TaskState.PLANNING)


class TestGetAllowedTransitions:
    """Test retrieval of allowed target states."""

    def test_draft_transitions(self):
        allowed = get_allowed_transitions(TaskState.DRAFT)
        assert TaskState.PLANNING in allowed
        assert TaskState.CANCELLED in allowed
        assert len(allowed) == 2

    def test_ready_transitions(self):
        allowed = get_allowed_transitions(TaskState.READY)
        assert TaskState.ASSIGNED in allowed
        assert TaskState.CANCELLED in allowed

    def test_done_has_no_transitions(self):
        assert get_allowed_transitions(TaskState.DONE) == []


class TestValidateTransition:
    """Test transition validation with progression policies."""

    def test_valid_manual_transition(self):
        """Require user confirmation for every valid Manual transition."""
        result = validate_transition(
            TaskState.DRAFT, TaskState.PLANNING, ProgressionPolicy.MANUAL
        )
        assert result.is_valid
        assert result.requires_confirmation

    def test_valid_automatic_transition(self):
        """Do not require confirmation for low-risk Automatic transitions."""
        result = validate_transition(
            TaskState.IN_PROGRESS, TaskState.SUBMITTED, ProgressionPolicy.AUTOMATIC
        )
        assert result.is_valid
        # SUBMITTED comes from an agent and usually advances after receipt import.

    def test_invalid_transition_raises(self):
        with pytest.raises(StateMachineError):
            validate_transition(
                TaskState.DONE, TaskState.DRAFT, ProgressionPolicy.HYBRID
            )

    def test_hybrid_high_risk_requires_confirmation(self):
        """Require confirmation for high-risk Hybrid transitions."""
        result = validate_transition(
            TaskState.VALIDATING, TaskState.ESCALATED, ProgressionPolicy.HYBRID
        )
        assert result.is_valid
        # Escalation to a strong model usually requires user confirmation.

    def test_all_policies_reject_invalid(self):
        for policy in ProgressionPolicy:
            with pytest.raises(StateMachineError):
                validate_transition(TaskState.DONE, TaskState.DRAFT, policy)


class TestProgressionPolicy:
    """Test the progression policy enum."""

    def test_three_policies(self):
        policies = list(ProgressionPolicy)
        assert ProgressionPolicy.MANUAL in policies
        assert ProgressionPolicy.AUTOMATIC in policies
        assert ProgressionPolicy.HYBRID in policies


class TestStateMachineIntegration:
    """Exercise the state machine with typical task lifecycles."""

    def test_happy_path(self):
        """Exercise the standard lifecycle from Draft to Done."""
        path = [
            (TaskState.DRAFT, TaskState.PLANNING),
            (TaskState.PLANNING, TaskState.READY),
            (TaskState.READY, TaskState.ASSIGNED),
            (TaskState.ASSIGNED, TaskState.IN_PROGRESS),
            (TaskState.IN_PROGRESS, TaskState.SUBMITTED),
            (TaskState.SUBMITTED, TaskState.VALIDATING),
            (TaskState.VALIDATING, TaskState.APPROVED),
            (TaskState.APPROVED, TaskState.MERGE_QUEUED),
            (TaskState.MERGE_QUEUED, TaskState.MERGING),
            (TaskState.MERGING, TaskState.DONE),
        ]
        for from_s, to_s in path:
            assert is_valid_transition(from_s, to_s), f"{from_s.name} -> {to_s.name}"

    def test_revision_loop(self):
        """Exercise the Validating to RevisionRequired repair loop."""
        assert is_valid_transition(TaskState.VALIDATING, TaskState.REVISION_REQUIRED)
        assert is_valid_transition(TaskState.REVISION_REQUIRED, TaskState.ASSIGNED)

    def test_escalation_path(self):
        """Exercise the Validating to Escalated path."""
        assert is_valid_transition(TaskState.VALIDATING, TaskState.ESCALATED)
        assert is_valid_transition(TaskState.ESCALATED, TaskState.ASSIGNED)

    def test_conflict_recovery(self):
        """Recover from Conflict after Merging."""
        assert is_valid_transition(TaskState.MERGING, TaskState.CONFLICT)
        assert is_valid_transition(TaskState.CONFLICT, TaskState.ASSIGNED)

    def test_blocked_recovery(self):
        """Recover from Blocked after InProgress."""
        assert is_valid_transition(TaskState.IN_PROGRESS, TaskState.BLOCKED)
        assert is_valid_transition(TaskState.BLOCKED, TaskState.ASSIGNED)

    def test_stale_recovery(self):
        """Recover from Stale after Assigned."""
        assert is_valid_transition(TaskState.ASSIGNED, TaskState.STALE)
        assert is_valid_transition(TaskState.STALE, TaskState.READY)
