"""Phase 1.3 测试 — 配置分层"""

import os
import tempfile
import pytest

# 如果没有 pyyaml，跳过测试
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

pytestmark = pytest.mark.skipif(not HAS_YAML, reason="pyyaml not installed")

from bridgelib.config import (
    ConfigLoader,
    ProjectConfig,
    AgentProfileConfig,
    ConfigError,
)


class TestProjectConfig:
    def test_parse_minimal(self):
        yaml_str = """
config_version: 1
protocol_version: 1
project:
  id: test-proj
  name: Test
  default_branch: main
"""
        config = ProjectConfig.from_yaml(yaml_str)
        assert config.project_id == "test-proj"
        assert config.project_name == "Test"
        assert config.default_branch == "main"

    def test_defaults(self):
        yaml_str = """
config_version: 1
protocol_version: 1
project:
  id: test
  name: Test
"""
        config = ProjectConfig.from_yaml(yaml_str)
        assert config.workspace_mode == "per_task_worktree"
        assert config.progression_policy == "hybrid"
        assert config.confirmation_policy == "balanced"
        assert config.language == "zh-CN"

    def test_coordination_section(self):
        yaml_str = """
config_version: 1
protocol_version: 1
project:
  id: test
  name: Test
coordination:
  workspace_mode: shared_directory
  progression_policy: manual
  confirmation_policy: strict
  max_parallel_tasks: 5
  lease_ttl_seconds: 1800
"""
        config = ProjectConfig.from_yaml(yaml_str)
        assert config.workspace_mode == "shared_directory"
        assert config.progression_policy == "manual"
        assert config.confirmation_policy == "strict"

    def test_validation_rules(self):
        yaml_str = """
config_version: 1
protocol_version: 1
project:
  id: test
  name: Test
validation:
  checks:
    - id: unit-tests
      executable: pytest
      args: ["-q"]
      required: true
"""
        config = ProjectConfig.from_yaml(yaml_str)
        assert len(config.checks) == 1
        assert config.checks[0]["id"] == "unit-tests"


class TestAgentProfileConfig:
    def test_parse_agent(self):
        yaml_str = """
profile_version: 1
agent:
  id: reasonix-worker
  display_name: Reasonix Worker
  provider: local-manual
  model: reasonix
  adapter: ReasonixPromptAdapter
capability:
  tier: standard
  cost_tier: low
  roles:
    - implementer
permissions:
  can_plan: false
  can_review: false
  can_merge: false
"""
        profile = AgentProfileConfig.from_yaml(yaml_str)
        assert profile.agent_id == "reasonix-worker"
        assert profile.display_name == "Reasonix Worker"
        assert profile.capability_tier == "standard"
        assert profile.cost_tier == "low"
        assert profile.can_plan is False


class TestConfigLoader:
    def test_load_project_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 写 project.yaml
            project_yaml = os.path.join(tmp, ".bridge", "project.yaml")
            os.makedirs(os.path.dirname(project_yaml), exist_ok=True)
            with open(project_yaml, "w") as f:
                f.write("""
config_version: 1
protocol_version: 1
project:
  id: loaded-proj
  name: Loaded Project
""")
            loader = ConfigLoader(tmp)
            config = loader.load()
            assert config.project_id == "loaded-proj"

    def test_load_with_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".bridge", "agents"), exist_ok=True)
            with open(os.path.join(tmp, ".bridge", "project.yaml"), "w") as f:
                f.write("""
config_version: 1
protocol_version: 1
project:
  id: multi-agent
  name: Multi Agent
""")
            with open(os.path.join(tmp, ".bridge", "agents", "codex.yaml"), "w") as f:
                f.write("""
profile_version: 1
agent:
  id: codex-primary
  display_name: Codex
capability:
  tier: high
  cost_tier: high
  roles: [planner, reviewer]
permissions:
  can_plan: true
  can_review: true
""")
            loader = ConfigLoader(tmp)
            config = loader.load()
            agents = loader.load_agents()
            assert len(agents) == 1
            assert agents[0].agent_id == "codex-primary"

    def test_missing_project_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader = ConfigLoader(tmp)
            with pytest.raises(ConfigError):
                loader.load()
