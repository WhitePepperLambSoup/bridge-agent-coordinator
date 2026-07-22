"""Bridge 安全策略引擎 — Strict/Balanced/Expert 确认模式 + 不可关闭的硬底线。

设计参考：docs/bridge-design/07-safety-and-confirmations.md
"""

from enum import Enum
from dataclasses import dataclass, field


class ConfirmationMode(Enum):
    STRICT = "strict"       # 所有写入/合并/清理/外部命令确认
    BALANCED = "balanced"   # 默认；低风险自动，高风险确认
    EXPERT = "expert"       # 可关闭大部分确认，但不能突破安全底线


class ActionPolicy(Enum):
    AUTO = "auto"                       # 自动执行
    CONFIRM_ONCE = "confirm_once"       # 每次会话确认一次
    CONFIRM_SESSION = "confirm_session"
    CONFIRM_PROJECT = "confirm_project"
    ALWAYS_CONFIRM = "always_confirm"   # 始终确认
    DISABLED = "disabled"               # 禁用此操作


# 不可关闭的安全底线 — 任何模式下都必须拒绝
HARD_FLOOR_ACTIONS = frozenset({
    "delete_user_directory",         # 删除含未提交修改的非临时用户目录
    "write_outside_workspace",       # 写入项目范围之外
    "force_push",                    # force push 或重写共享历史
    "delete_default_branch",         # 删除默认/主分支
    "expose_secret",                 # 输出/提交/复制检测到的密钥
    "overwrite_unknown_file",        # 覆盖无法确认归属的文件
    "path_resolution_failure",       # 路径解析失败/符号链接逃逸
    "db_git_mismatch_autofix",       # 数据库与 Git 矛盾时自动修复
    "agent_privilege_escalation",    # Agent 通过回执扩大权限
})


@dataclass
class ActionRule:
    """单个操作的安全规则"""
    action_id: str
    policy: ActionPolicy = ActionPolicy.ALWAYS_CONFIRM
    description: str = ""


class SafetyPolicy:
    """安全策略引擎 — 判断操作是否需要确认、是否可以自动化。"""

    # 默认 Balanced 策略
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
                 overrides: dict[str, ActionPolicy] | None = None):
        self.mode = mode
        self._rules = dict(self.DEFAULT_RULES)
        if overrides:
            self._rules.update(overrides)

    def requires_confirmation(self, action_id: str) -> bool:
        """判断操作是否需要用户确认。"""
        # 硬底线 — 始终拒绝
        if action_id in HARD_FLOOR_ACTIONS:
            return True  # 不仅确认，实际上应该完全拒绝

        policy = self._rules.get(action_id, ActionPolicy.ALWAYS_CONFIRM)

        if self.mode == ConfirmationMode.STRICT:
            return policy != ActionPolicy.DISABLED
        elif self.mode == ConfirmationMode.BALANCED:
            return policy not in (ActionPolicy.AUTO, ActionPolicy.DISABLED)
        elif self.mode == ConfirmationMode.EXPERT:
            return policy in (ActionPolicy.ALWAYS_CONFIRM, ActionPolicy.DISABLED)

        return True

    def is_hard_floor(self, action_id: str) -> bool:
        """检查是否触及不可关闭的安全底线。"""
        return action_id in HARD_FLOOR_ACTIONS

    def can_automate(self, action_id: str) -> bool:
        """检查操作是否可以自动化。"""
        if action_id in HARD_FLOOR_ACTIONS:
            return False
        return not self.requires_confirmation(action_id)

    def get_policy(self, action_id: str) -> ActionPolicy:
        """获取操作的安全策略。"""
        return self._rules.get(action_id, ActionPolicy.ALWAYS_CONFIRM)

    def set_override(self, action_id: str, policy: ActionPolicy):
        """用户覆盖某操作的安全级别。"""
        if action_id in HARD_FLOOR_ACTIONS and policy != ActionPolicy.ALWAYS_CONFIRM:
            raise ValueError(
                f"Cannot override hard floor action '{action_id}' "
                f"to {policy.value} — this action always requires confirmation"
            )
        self._rules[action_id] = policy

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "rules": {k: v.value for k, v in self._rules.items()},
        }


# 默认工厂方法
def create_strict_policy() -> SafetyPolicy:
    return SafetyPolicy(mode=ConfirmationMode.STRICT)


def create_balanced_policy() -> SafetyPolicy:
    return SafetyPolicy(mode=ConfirmationMode.BALANCED)


def create_expert_policy() -> SafetyPolicy:
    return SafetyPolicy(mode=ConfirmationMode.EXPERT)
