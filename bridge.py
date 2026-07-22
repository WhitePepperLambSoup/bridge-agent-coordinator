#!/usr/bin/env python3
"""
Bridge — 本地多 Agent 协调器

入口文件。Phase 0 重构后，所有核心逻辑已拆分至 bridgelib/ 包。
运行方式不变：
  python bridge.py

开发测试：
  python -m pytest tests/ -v
"""
import sys
import os

# 确保项目根目录在 sys.path 中
_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 向后兼容：将 bridgelib 模块暴露为顶层名称
from bridgelib.i18n import T, set_lang, LANG, _STR
from bridgelib.templates import TEMPLATES
from bridgelib.utils import _atomic_write, _sanitize_agent_name, _check_git_repo
from bridgelib.models import MODEL_REGISTRY, get_models_by_tier, fetch_latest_models
from bridgelib.generators import (
    _stage_display,
    generate_agents_md,
    generate_collab_md,
    generate_tasks_md,
    generate_review_template,
    generate_fix_template,
    generate_acceptance_md,
    generate_readme_md,
    generate_agent_status_md,
    generate_board_md,
    generate_git_worktree_guide,
    generate_parallel_struct,
    generate_loop_budget_md,
    generate_verify_template,
    generate_spec_claim_template,
)
from bridgelib.llm import call_llm, llm_enhance_description
from bridgelib.gui import BridgeApp, main


if __name__ == "__main__":
    main()
