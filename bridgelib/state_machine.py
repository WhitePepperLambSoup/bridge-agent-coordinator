"""Bridge task state machine with 17 states and three progression policies.

Design reference: docs/bridge-design/03-task-state-machine.md
"""

from enum import Enum


class TaskState(Enum):
    """Enumeration of the 17 task states."""
    DRAFT = "draft"
    PLANNING = "planning"
    READY = "ready"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    VALIDATING = "validating"
    APPROVED = "approved"
    MERGE_QUEUED = "merge_queued"
    MERGING = "merging"
    DONE = "done"
    BLOCKED = "blocked"
    REVISION_REQUIRED = "revision_required"
    ESCALATED = "escalated"
    CONFLICT = "conflict"
    STALE = "stale"
    CANCELLED = "cancelled"

    @classmethod
    def is_terminal(cls, state: "TaskState") -> bool:
        return state in (cls.DONE, cls.CANCELLED)


class ProgressionPolicy(Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    HYBRID = "hybrid"


class StateMachineError(Exception):
    """State machine error."""
    pass


class TransitionResult:
    """State transition validation result."""

    def __init__(self, is_valid: bool, requires_confirmation: bool = False,
                 reason: str = ""):
        self.is_valid = is_valid
        self.requires_confirmation = requires_confirmation
        self.reason = reason


# ── State transition graph; see 03-task-state-machine.md ──

_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.DRAFT:              {TaskState.PLANNING, TaskState.CANCELLED},
    TaskState.PLANNING:           {TaskState.READY, TaskState.BLOCKED, TaskState.CANCELLED},
    TaskState.READY:              {TaskState.ASSIGNED, TaskState.CANCELLED},
    TaskState.ASSIGNED:           {TaskState.IN_PROGRESS, TaskState.STALE, TaskState.CANCELLED},
    TaskState.IN_PROGRESS:        {TaskState.SUBMITTED, TaskState.BLOCKED, TaskState.STALE, TaskState.CANCELLED},
    TaskState.SUBMITTED:          {TaskState.VALIDATING, TaskState.REVISION_REQUIRED},
    TaskState.VALIDATING:         {TaskState.APPROVED, TaskState.REVISION_REQUIRED, TaskState.ESCALATED, TaskState.CONFLICT},
    TaskState.APPROVED:           {TaskState.MERGE_QUEUED},
    TaskState.MERGE_QUEUED:       {TaskState.MERGING, TaskState.CONFLICT, TaskState.CANCELLED},
    TaskState.MERGING:            {TaskState.DONE, TaskState.CONFLICT, TaskState.REVISION_REQUIRED},
    TaskState.DONE:               set(),       # Terminal state
    TaskState.BLOCKED:            {TaskState.ASSIGNED, TaskState.CANCELLED},
    TaskState.REVISION_REQUIRED:  {TaskState.ASSIGNED, TaskState.CANCELLED},
    TaskState.ESCALATED:          {TaskState.ASSIGNED, TaskState.BLOCKED, TaskState.CANCELLED},
    TaskState.CONFLICT:           {TaskState.ASSIGNED, TaskState.CANCELLED},
    TaskState.STALE:              {TaskState.READY, TaskState.ASSIGNED, TaskState.CANCELLED},
    TaskState.CANCELLED:          set(),       # Terminal state
}

# ── High-risk transitions requiring user confirmation ──
_HIGH_RISK_TRANSITIONS: set[tuple[TaskState, TaskState]] = {
    (TaskState.PLANNING, TaskState.READY),        # Planning complete
    (TaskState.VALIDATING, TaskState.ESCALATED),  # Escalate to a capable model
    (TaskState.VALIDATING, TaskState.APPROVED),   # Final approval
    (TaskState.APPROVED, TaskState.MERGE_QUEUED), # Enter the merge queue
    (TaskState.MERGING, TaskState.DONE),          # Final merge
    (TaskState.ASSIGNED, TaskState.IN_PROGRESS),  # User confirms agent handoff
    (TaskState.CANCELLED, TaskState.DRAFT),       # Impossible: terminal state
}

# ── Transitions that always pause under the automatic policy ──
_ALWAYS_CONFIRM: set[tuple[TaskState, TaskState]] = {
    (TaskState.VALIDATING, TaskState.ESCALATED),
    (TaskState.MERGING, TaskState.DONE),
}


def is_valid_transition(from_state: TaskState, to_state: TaskState) -> bool:
    """Check whether a state transition is valid."""
    allowed = _TRANSITIONS.get(from_state, set())
    return to_state in allowed


def get_allowed_transitions(from_state: TaskState) -> list[TaskState]:
    """Get the destination states allowed from the current state."""
    return sorted(_TRANSITIONS.get(from_state, set()), key=lambda s: s.name)


def requires_confirmation(
    from_state: TaskState, to_state: TaskState, policy: ProgressionPolicy
) -> bool:
    """Determine whether a transition requires user confirmation."""
    if not is_valid_transition(from_state, to_state):
        raise StateMachineError(
            f"Invalid transition: {from_state.value} -> {to_state.value}"
        )

    pair = (from_state, to_state)

    if policy == ProgressionPolicy.MANUAL:
        return True  # The manual policy requires confirmation for every transition

    if policy == ProgressionPolicy.AUTOMATIC:
        # The automatic policy confirms only transitions in the always-pause list
        return pair in _ALWAYS_CONFIRM

    # The hybrid policy requires confirmation for high-risk transitions
    if policy == ProgressionPolicy.HYBRID:
        return pair in _HIGH_RISK_TRANSITIONS or pair in _ALWAYS_CONFIRM

    return True  # Unknown policies default to the conservative choice


def validate_transition(
    from_state: TaskState,
    to_state: TaskState,
    policy: ProgressionPolicy = ProgressionPolicy.HYBRID,
) -> TransitionResult:
    """Validate a state transition and return the result."""
    if not is_valid_transition(from_state, to_state):
        raise StateMachineError(
            f"Invalid transition: {from_state.value} -> {to_state.value}"
        )

    needs_confirm = requires_confirmation(from_state, to_state, policy)
    return TransitionResult(
        is_valid=True,
        requires_confirmation=needs_confirm,
        reason="requires user confirmation" if needs_confirm else "automatic",
    )
