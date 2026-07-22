"""Phase 0 回归测试 — 模板与生成器"""

import pytest
from bridgelib.templates import TEMPLATES
from bridgelib.generators import (
    generate_agents_md,
    generate_collab_md,
    generate_board_md,
    generate_agent_status_md,
    generate_parallel_struct,
    generate_git_worktree_guide,
    generate_readme_md,
)


class TestTemplates:
    """TEMPLATES 配置完整性测试"""

    def test_seven_modes_exist(self):
        """七种协作模式已定义"""
        expected = [
            "architect-engineer", "peer-review", "spec-driven",
            "quick-start", "parallel-team", "loop-engineering",
            "parallel-claim",
        ]
        for mode in expected:
            assert mode in TEMPLATES, f"Missing mode: {mode}"

    def test_each_mode_has_required_fields(self):
        """每个模式有 name/description/icon/pipeline/agent_a/agent_b"""
        for key, tmpl in TEMPLATES.items():
            assert "name" in tmpl, f"{key} missing name"
            assert "description" in tmpl, f"{key} missing description"
            assert "icon" in tmpl, f"{key} missing icon"
            assert "pipeline" in tmpl, f"{key} missing pipeline"
            assert "agent_a" in tmpl, f"{key} missing agent_a"
            assert "agent_b" in tmpl, f"{key} missing agent_b"

    def test_each_pipeline_stage_has_id_agent(self):
        """每个流水线阶段有 id 和 agent"""
        for key, tmpl in TEMPLATES.items():
            for stage in tmpl["pipeline"]:
                assert "id" in stage, f"{key} stage missing id"
                assert "agent" in stage, f"{key} stage missing agent"
                assert stage["agent"] in ("Agent A", "Agent B", "Both")


class TestAgentsMdGeneration:
    """generate_agents_md 测试"""

    def test_basic_generation_zh(self):
        agent_a = {"name": "GPT", "role": "架构师", "model": "gpt-5"}
        agent_b = {"name": "Reasonix", "role": "工程师", "model": "rs-v1"}
        result = generate_agents_md("architect-engineer", agent_a, agent_b, "测试项目", "zh")
        assert "AGENTS.md" in result
        assert "GPT" in result
        assert "Reasonix" in result
        assert "架构师" in result
        assert "工程师" in result

    def test_basic_generation_en(self):
        agent_a = {"name": "GPT", "role": "Architect", "model": "gpt-5"}
        agent_b = {"name": "Reasonix", "role": "Engineer", "model": "rs-v1"}
        result = generate_agents_md("architect-engineer", agent_a, agent_b, "Test Project", "en")
        assert "AGENTS.md" in result
        assert "GPT" in result
        assert "Reasonix" in result
        # 英文输出不含中文
        has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in result)
        assert not has_chinese, f"English AGENTS.md contains Chinese"

    def test_agent_c_included_when_provided(self):
        agent_a = {"name": "GPT", "role": "Planner", "model": "gpt-5"}
        agent_b = {"name": "Reasonix", "role": "Coder", "model": "rs-v1"}
        agent_c = {"name": "Claude", "role": "Reviewer", "model": "claude-4"}
        result = generate_agents_md(
            "parallel-team", agent_a, agent_b, "Test", "zh", agent_c=agent_c
        )
        assert "Claude" in result
        assert "Reviewer" in result

    def test_agent_c_omitted_when_none(self):
        agent_a = {"name": "GPT", "role": "Planner", "model": "gpt-5"}
        agent_b = {"name": "Reasonix", "role": "Coder", "model": "rs-v1"}
        result = generate_agents_md(
            "parallel-team", agent_a, agent_b, "Test", "zh", agent_c=None
        )
        assert "Agent C" not in result


class TestBoardMdGeneration:
    """generate_board_md 测试"""

    def test_basic_board_zh(self):
        result = generate_board_md("GPT", "Reasonix", "zh")
        assert "board.md" in result
        assert "GPT" in result
        assert "Reasonix" in result
        assert "任务池" in result

    def test_basic_board_en(self):
        result = generate_board_md("GPT", "Reasonix", "en")
        assert "board.md" in result
        assert "Task Pool" in result
        has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in result)
        assert not has_chinese

    def test_agent_c_mutex_rule(self):
        result = generate_board_md("GPT", "Reasonix", "zh", agent_c_name="Claude")
        assert "Claude" in result


class TestAgentStatusGeneration:
    """generate_agent_status_md 测试"""

    def test_status_zh(self):
        result = generate_agent_status_md("GPT", "Architect", "Reasonix", "zh")
        assert "agent-gpt.md" in result
        assert "状态文件" in result

    def test_status_en(self):
        result = generate_agent_status_md("GPT", "Architect", "Reasonix", "en")
        assert "Status File" in result
        has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in result)
        assert not has_chinese


class TestWorktreeGuide:
    """generate_git_worktree_guide 测试"""

    def test_worktree_zh(self):
        result = generate_git_worktree_guide("zh")
        assert "GIT_WORKTREE.md" in result
        assert "原理" in result

    def test_worktree_en(self):
        result = generate_git_worktree_guide("en")
        assert "GIT_WORKTREE.md" in result
        assert "How It Works" in result
        has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in result)
        assert not has_chinese


class TestParallelStruct:
    """generate_parallel_struct 测试"""

    def test_parallel_zh(self):
        result = generate_parallel_struct("GPT", "Reasonix", lang="zh")
        assert "并行协作快速入门" in result
        assert "GPT" in result
        assert "Reasonix" in result

    def test_parallel_en(self):
        result = generate_parallel_struct("GPT", "Reasonix", lang="en")
        assert "Parallel Collaboration Quick Start" in result
        has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in result)
        assert not has_chinese

    def test_agent_c_included(self):
        result = generate_parallel_struct("GPT", "Reasonix", "Claude", "zh")
        assert "Claude" in result


class TestCollabMdGeneration:
    """generate_collab_md 测试"""

    def test_collab_generation(self):
        agent_a = {"name": "GPT", "role": "Planner"}
        agent_b = {"name": "Reasonix", "role": "Coder"}
        result = generate_collab_md("architect-engineer", agent_a, agent_b, lang="zh")
        assert "COLLAB.md" in result
        assert "GPT" in result
