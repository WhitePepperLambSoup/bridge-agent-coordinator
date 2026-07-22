"""Bridge 任务状态机 — 17 状态 + 三种推进策略。

设计参考：docs/bridge-design/03-task-state-machine.md
"""

from enum import Enum


class TaskState(Enum):
    """任务状态枚举 — 17 个状态。"""
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
    """状态机错误"""
    pass


class TransitionResult:
    """状态转换验证结果"""

    def __init__(self, is_valid: bool, requires_confirmation: bool = False,
                 reason: str = ""):
        self.is_valid = is_valid
        self.requires_confirmation = requires_confirmation
        self.reason = reason


# ── 状态转换图（参考 03-task-state-machine.md 状态图）──

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
    TaskState.DONE:               set(),       # 终态
    TaskState.BLOCKED:            {TaskState.ASSIGNED, TaskState.CANCELLED},
    TaskState.REVISION_REQUIRED:  {TaskState.ASSIGNED, TaskState.CANCELLED},
    TaskState.ESCALATED:          {TaskState.ASSIGNED, TaskState.BLOCKED, TaskState.CANCELLED},
    TaskState.CONFLICT:           {TaskState.ASSIGNED, TaskState.CANCELLED},
    TaskState.STALE:              {TaskState.READY, TaskState.ASSIGNED, TaskState.CANCELLED},
    TaskState.CANCELLED:          set(),       # 终态
}

# ── 高风险转换（需要用户确认）──
_HIGH_RISK_TRANSITIONS: set[tuple[TaskState, TaskState]] = {
    (TaskState.PLANNING, TaskState.READY),        # 规划完成
    (TaskState.VALIDATING, TaskState.ESCALATED),  # 升级到强模型
    (TaskState.VALIDATING, TaskState.APPROVED),   # 最终批准
    (TaskState.APPROVED, TaskState.MERGE_QUEUED), # 进入合并队列
    (TaskState.MERGING, TaskState.DONE),          # 最终合并
    (TaskState.ASSIGNED, TaskState.IN_PROGRESS),  # 用户确认交予 Agent
    (TaskState.CANCELLED, TaskState.DRAFT),       # 不可能 — 终态
}

# ── Automatic 策略下也始终暂停的转换 ──
_ALWAYS_CONFIRM: set[tuple[TaskState, TaskState]] = {
    (TaskState.VALIDATING, TaskState.ESCALATED),
    (TaskState.MERGING, TaskState.DONE),
}


def is_valid_transition(from_state: TaskState, to_state: TaskState) -> bool:
    """检查状态转换是否合法。"""
    allowed = _TRANSITIONS.get(from_state, set())
    return to_state in allowed


def get_allowed_transitions(from_state: TaskState) -> list[TaskState]:
    """获取当前状态允许的目标状态列表。"""
    return sorted(_TRANSITIONS.get(from_state, set()), key=lambda s: s.name)


def requires_confirmation(
    from_state: TaskState, to_state: TaskState, policy: ProgressionPolicy
) -> bool:
    """判断此转换是否需要用户确认。"""
    if not is_valid_transition(from_state, to_state):
        raise StateMachineError(
            f"Invalid transition: {from_state.value} -> {to_state.value}"
        )

    pair = (from_state, to_state)

    if policy == ProgressionPolicy.MANUAL:
        return True  # Manual 策略：所有转换都需要确认

    if policy == ProgressionPolicy.AUTOMATIC:
        # Automatic 策略：只有始终暂停列表中的转换需要确认
        return pair in _ALWAYS_CONFIRM

    # Hybrid 策略：高风险转换需要确认
    if policy == ProgressionPolicy.HYBRID:
        return pair in _HIGH_RISK_TRANSITIONS or pair in _ALWAYS_CONFIRM

    return True  # 未知策略默认保守


def validate_transition(
    from_state: TaskState,
    to_state: TaskState,
    policy: ProgressionPolicy = ProgressionPolicy.HYBRID,
) -> TransitionResult:
    """验证状态转换并返回结果。"""
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
