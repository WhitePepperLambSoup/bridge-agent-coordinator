"""Bridge configuration layers — parse and override project.yaml, agents/*.yaml, and local.yaml.

Design reference: docs/bridge-design/09-database-events-and-config.md §6
"""

import os
from dataclasses import dataclass, field

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


class ConfigError(Exception):
    """Configuration error."""
    pass


# ── Project Config ────────────────────────────────────────

@dataclass
class ProjectConfig:
    """Project configuration (.bridge/project.yaml)."""
    config_version: int = 1
    protocol_version: int = 1
    project_id: str = ""
    project_name: str = ""
    default_branch: str = "main"
    language: str = "zh-CN"
    workspace_mode: str = "per_task_worktree"
    progression_policy: str = "hybrid"
    confirmation_policy: str = "balanced"
    max_parallel_tasks: int = 3
    lease_ttl_seconds: int = 900
    receipt_stability_ms: int = 750
    checks: list[dict] = field(default_factory=list)
    budgets: dict = field(default_factory=dict)
    safety: dict = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "ProjectConfig":
        if yaml is None:
            raise ConfigError("pyyaml is required for config parsing")
        data = yaml.safe_load(yaml_str) or {}

        project = data.get("project", {})
        coord = data.get("coordination", {})
        validation = data.get("validation", {})
        budgets = data.get("budgets", {})

        return cls(
            config_version=data.get("config_version", 1),
            protocol_version=data.get("protocol_version", 1),
            project_id=project.get("id", ""),
            project_name=project.get("name", ""),
            default_branch=project.get("default_branch", "main"),
            language=project.get("language", "zh-CN"),
            workspace_mode=coord.get("workspace_mode", "per_task_worktree"),
            progression_policy=coord.get("progression_policy", "hybrid"),
            confirmation_policy=coord.get("confirmation_policy", "balanced"),
            max_parallel_tasks=coord.get("max_parallel_tasks", 3),
            lease_ttl_seconds=coord.get("lease_ttl_seconds", 900),
            receipt_stability_ms=coord.get("receipt_stability_ms", 750),
            checks=validation.get("checks", []),
            budgets=budgets,
            safety=data.get("safety", {}),
        )

    @classmethod
    def from_file(cls, path: str) -> "ProjectConfig":
        if not os.path.exists(path):
            raise ConfigError(f"Project config not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_yaml(f.read())


# ── Agent Profile Config ──────────────────────────────────

@dataclass
class AgentProfileConfig:
    """Agent profile configuration (.bridge/agents/*.yaml)."""
    profile_version: int = 1
    agent_id: str = ""
    display_name: str = ""
    provider: str = "local-manual"
    model: str = ""
    adapter: str = "GenericFileAgentAdapter"
    capability_tier: str = "standard"
    cost_tier: str = "low"
    roles: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    can_plan: bool = False
    can_approve_plan: bool = False
    can_review: bool = False
    can_merge: bool = False
    can_run_validation: bool = True
    can_request_escalation: bool = True
    max_parallel_tasks: int = 1
    default_token_budget: int = 50000
    default_time_budget_seconds: int = 7200
    default_retry_budget: int = 2
    workspace_preferred_mode: str = "per_task_worktree"
    requires_manual_launch: bool = True
    enabled: bool = True

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "AgentProfileConfig":
        if yaml is None:
            raise ConfigError("pyyaml is required for config parsing")
        data = yaml.safe_load(yaml_str) or {}

        agent = data.get("agent", {})
        cap = data.get("capability", {})
        perms = data.get("permissions", {})
        limits = data.get("limits", {})
        ws = data.get("workspace", {})

        return cls(
            profile_version=data.get("profile_version", 1),
            agent_id=agent.get("id", ""),
            display_name=agent.get("display_name", ""),
            provider=agent.get("provider", "local-manual"),
            model=agent.get("model", ""),
            adapter=agent.get("adapter", "GenericFileAgentAdapter"),
            capability_tier=cap.get("tier", "standard"),
            cost_tier=cap.get("cost_tier", "low"),
            roles=cap.get("roles", []),
            strengths=cap.get("strengths", []),
            can_plan=perms.get("can_plan", False),
            can_approve_plan=perms.get("can_approve_plan", False),
            can_review=perms.get("can_review", False),
            can_merge=perms.get("can_merge", False),
            can_run_validation=perms.get("can_run_validation", True),
            can_request_escalation=perms.get("can_request_escalation", True),
            max_parallel_tasks=limits.get("max_parallel_tasks", 1),
            default_token_budget=limits.get("default_token_budget", 50000),
            default_time_budget_seconds=limits.get("default_time_budget_seconds", 7200),
            default_retry_budget=limits.get("default_retry_budget", 2),
            workspace_preferred_mode=ws.get("preferred_mode", "per_task_worktree"),
            requires_manual_launch=ws.get("requires_manual_launch", True),
            enabled=agent.get("enabled", True),
        )

    @classmethod
    def from_file(cls, path: str) -> "AgentProfileConfig":
        if not os.path.exists(path):
            raise ConfigError(f"Agent config not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_yaml(f.read())


# ── Config Loader ─────────────────────────────────────────

class ConfigLoader:
    """Configuration loader — layered overrides (project.yaml → agents/*.yaml → local.yaml)."""

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)
        self.bridge_dir = os.path.join(self.project_root, ".bridge")

    def load(self) -> ProjectConfig:
        """Load the project configuration and apply local.yaml overrides."""
        path = os.path.join(self.bridge_dir, "project.yaml")
        if not os.path.exists(path):
            raise ConfigError(
                f"Project config not found: {path}. "
                "Run bridge init first."
            )
        config = ProjectConfig.from_file(path)

        # Load local.yaml overrides if present
        local_path = os.path.join(self.bridge_dir, "local.yaml")
        if os.path.exists(local_path):
            config = self._apply_local_overlay(config, local_path)

        return config

    def _apply_local_overlay(self, config: ProjectConfig,
                             local_path: str) -> ProjectConfig:
        """Apply local.yaml overrides to the project configuration."""
        if yaml is None:
            return config
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                local_data = yaml.safe_load(f) or {}
        except Exception:
            return config

        # Apply overrides
        project = local_data.get("project", {})
        if "default_branch" in project:
            config.default_branch = project["default_branch"]
        if "language" in project:
            config.language = project["language"]

        coord = local_data.get("coordination", {})
        if "workspace_mode" in coord:
            config.workspace_mode = coord["workspace_mode"]
        if "progression_policy" in coord:
            config.progression_policy = coord["progression_policy"]
        if "confirmation_policy" in coord:
            config.confirmation_policy = coord["confirmation_policy"]
        if "max_parallel_tasks" in coord:
            config.max_parallel_tasks = coord["max_parallel_tasks"]
        if "lease_ttl_seconds" in coord:
            config.lease_ttl_seconds = coord["lease_ttl_seconds"]
        if "receipt_stability_ms" in coord:
            config.receipt_stability_ms = coord["receipt_stability_ms"]

        # Security policy overrides
        safety = local_data.get("safety", {})
        if safety:
            config.safety.update(safety)

        # Budget overrides
        budgets = local_data.get("budgets", {})
        if budgets:
            config.budgets.update(budgets)

        return config

    def load_agents(self) -> list[AgentProfileConfig]:
        """Load all agent profiles."""
        agents_dir = os.path.join(self.bridge_dir, "agents")
        if not os.path.isdir(agents_dir):
            return []

        agents = []
        for filename in sorted(os.listdir(agents_dir)):
            if filename.endswith((".yaml", ".yml")):
                path = os.path.join(agents_dir, filename)
                try:
                    agents.append(AgentProfileConfig.from_file(path))
                except ConfigError:
                    continue  # Skip invalid configurations
        return agents
