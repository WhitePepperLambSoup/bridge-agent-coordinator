#!/usr/bin/env python3
"""
Bridge — AI Agent 协作桥接器

一个 GUI 工具，用于在目标项目文件夹中生成 AI Agent 桥接流程的 .md 文件。
两个 AI Agent 通过这些文件了解各自的角色、流水线和当前任务状态，
从而实现高效协作。

支持 5 种协作模式：
  1. Architect-Engineer  — GPT 架构师 + Reasonix 工程师（9 道工序）
  2. Peer-Review         — 两个平等 Agent 互相审查
  3. Spec-Driven         — 规范先行，严格门禁
  4. Quick-Start         — 最小化设置，立刻开始
  5. Custom              — 用户自定义流水线

可选：接入 OpenAI 兼容 API 辅助理解需求并自动填充内容。

运行方式：
  python bridge.py
"""

import os
import sys
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime
import threading
import urllib.request
import urllib.error

# ═══════════════════════════════════════════════════════════════
# 国际化 (i18n)
# ═══════════════════════════════════════════════════════════════

LANG = "zh"  # 默认语言，GUI 可切换

def T(key, lang=None, **fmt):
    """获取翻译文本。key 用点号分隔层级，如 'mode.architect.name'"""
    if lang is None:
        lang = LANG
    parts = key.split(".")
    d = _STR
    for p in parts:
        if isinstance(d, dict) and p in d:
            d = d[p]
        else:
            return key  # fallback: 返回 key 本身
    if isinstance(d, dict):
        text = d.get(lang, d.get("zh", str(d)))
    else:
        text = str(d)
    if fmt:
        text = text.format(**fmt)
    return text

def set_lang(lang):
    global LANG
    LANG = lang

# 完整翻译字典 — 新增文本只需在此添加
_STR = {
    "window_title": {"zh": "Bridge — AI Agent 协作桥接器", "en": "Bridge — AI Agent Collaboration Hub"},
    "tab_project": {"zh": "  📁 项目设置  ", "en": "  📁 Project Setup  "},
    "tab_agent": {"zh": "  🤖 Agent 配置  ", "en": "  🤖 Agent Config  "},
    "tab_llm": {"zh": "  🧠 LLM 辅助  ", "en": "  🧠 LLM Assist  "},
    "tab_pipeline": {"zh": "  🔧 流水线编辑  ", "en": "  🔧 Pipeline Editor  "},

    "lbl_target_dir": {"zh": "目标项目文件夹", "en": "Target Project Folder"},
    "lbl_browse": {"zh": "浏览...", "en": "Browse..."},
    "lbl_project_name": {"zh": "项目名称", "en": "Project Name"},
    "lbl_collab_mode": {"zh": "协作模式", "en": "Collaboration Mode"},
    "lbl_agent_a": {"zh": "Agent A（架构师/规划者）", "en": "Agent A (Architect/Planner)"},
    "lbl_agent_b": {"zh": "Agent B（工程师/执行者）", "en": "Agent B (Engineer/Executor)"},
    "lbl_name": {"zh": "名称", "en": "Name"},
    "lbl_role": {"zh": "角色描述", "en": "Role Description"},
    "lbl_model": {"zh": "模型", "en": "Model"},
    "lbl_llm_enable": {"zh": "启用 LLM API 辅助理解需求", "en": "Enable LLM API to assist requirement analysis"},
    "lbl_api_key": {"zh": "API Key", "en": "API Key"},
    "lbl_api_base": {"zh": "API Base URL", "en": "API Base URL"},
    "lbl_llm_model": {"zh": "模型", "en": "Model"},
    "lbl_llm_desc": {"zh": "描述你的项目需求（LLM 将辅助分析并自动填充配置）：",
                     "en": "Describe your project requirements (LLM will analyze and auto-fill):"},
    "lbl_llm_analyze": {"zh": "🤖 AI 分析需求", "en": "🤖 AI Analyze"},
    "lbl_pipeline_hint": {"zh": "仅「Custom」模式下生效。拖拽排序未实现，请用上下按钮调整。",
                          "en": "Only effective in Custom mode. Use up/down buttons to reorder."},
    "lbl_pipeline_stages": {"zh": "流水线阶段", "en": "Pipeline Stages"},
    "lbl_stage_name": {"zh": "阶段名称", "en": "Stage Name"},
    "lbl_stage_agent": {"zh": "执行 Agent", "en": "Agent"},
    "lbl_stage_desc": {"zh": "描述", "en": "Description"},

    "btn_add": {"zh": "➕ 添加", "en": "➕ Add"},
    "btn_update": {"zh": "✏️ 更新", "en": "✏️ Update"},
    "btn_delete": {"zh": "🗑 删除", "en": "🗑 Delete"},
    "btn_preview": {"zh": "👁 预览生成内容", "en": "👁 Preview"},
    "btn_generate": {"zh": "🚀 生成到项目文件夹", "en": "🚀 Generate to Project Folder"},
    "btn_lang_zh": {"zh": "中", "en": "中"},
    "btn_lang_en": {"zh": "En", "en": "En"},

    "status_ready": {"zh": "就绪", "en": "Ready"},
    "status_generated": {"zh": "✅ 已生成 {n} 个文件", "en": "✅ Generated {n} files"},
    "status_preview": {"zh": "预览已更新", "en": "Preview updated"},
    "status_analyzing": {"zh": "⏳ 分析中...", "en": "⏳ Analyzing..."},
    "status_analyze_ok": {"zh": "✅ 分析完成，配置已自动填充", "en": "✅ Analysis complete, config auto-filled"},
    "status_analyze_err": {"zh": "⚠️ 结果解析异常: {e}", "en": "⚠️ Parse error: {e}"},

    "msg_confirm_overwrite": {"zh": "以下文件已存在，将被覆盖：\n{files}\n\n是否继续？",
                              "en": "These files already exist and will be overwritten:\n{files}\n\nContinue?"},
    "msg_overwrite_title": {"zh": "确认覆盖", "en": "Confirm Overwrite"},
    "msg_no_dir": {"zh": "请先选择目标项目文件夹", "en": "Please select a target project folder first"},
    "msg_dir_not_exist": {"zh": "文件夹不存在: {d}", "en": "Folder not found: {d}"},
    "msg_llm_disabled": {"zh": "请先勾选「启用 LLM API」", "en": "Please check 'Enable LLM API' first"},
    "msg_llm_no_input": {"zh": "请先输入项目需求描述", "en": "Please enter project requirements first"},
    "msg_no_stage_name": {"zh": "请输入阶段名称", "en": "Please enter stage name"},
    "msg_generated": {"zh": "已在 {d} 中生成以下文件：\n\n{files}",
                      "en": "Generated the following files in {d}:\n\n{files}"},
    "msg_generate_title": {"zh": "生成完成", "en": "Generation Complete"},
    "msg_error_title": {"zh": "生成失败", "en": "Generation Failed"},
    "msg_error": {"zh": "错误", "en": "Error"},
    "msg_info": {"zh": "提示", "en": "Info"},

    "preview_header": {"zh": """══════════════════════════════════════
  Bridge 生成预览
  模式: {mode}
  并发: {concurrency}
  项目: {project}
  Agent A: {a_name} ({a_role})
  Agent B: {b_name} ({b_role})
══════════════════════════════════════
""", "en": """══════════════════════════════════════
  Bridge Generation Preview
  Mode: {mode}
  Concurrency: {concurrency}
  Project: {project}
  Agent A: {a_name} ({a_role})
  Agent B: {b_name} ({b_role})
══════════════════════════════════════
"""},
    "concurrency_parallel": {"zh": "⚡ 并行（分离文件+git仲裁）", "en": "⚡ Parallel (split files + git arbitration)"},
    "concurrency_serial": {"zh": "🔗 串行（接力棒模式）", "en": "🔗 Serial (relay baton mode)"},

    "preview_parallel": {"zh": """
📄 AGENTS.md: 项目元信息 + 6 道工序流水线
📄 agent-{a}.md: {a} 独立状态（只有 {a} 写）
📄 agent-{b}.md: {b} 独立状态（只有 {b} 写）
📄 board.md: 共享任务看板（git 仲裁并发）
📄 PARALLEL_GUIDE.md: 并行协作快速入门
📄 GIT_WORKTREE.md: worktree 物理隔离指南
📂 tasks/: 独立任务文件
📂 specs/: 只读规范文档

-- 关键设计 --
🔒 互斥写: 各自的状态文件互不冲突
📋 共享写: board.md 和 tasks/ 通过 git 控制并发
🔄 节奏: 原子操作写完立即 commit→push，开始前先 pull
""", "en": """
📄 AGENTS.md: Project metadata + 6-stage pipeline
📄 agent-{a}.md: {a}'s private state (only {a} writes)
📄 agent-{b}.md: {b}'s private state (only {b} writes)
📄 board.md: Shared task board (git-arbitrated)
📄 PARALLEL_GUIDE.md: Parallel collaboration quick-start
📄 GIT_WORKTREE.md: Worktree isolation guide
📂 tasks/: Per-task definition files
📂 specs/: Read-only spec documents

-- Key Design --
🔒 Mutex Writes: Each agent's status file never conflicts
📋 Shared Writes: board.md and tasks/ concurrency via git
🔄 Rhythm: Commit+push after each atomic change; pull before starting
"""},
    "preview_serial_tail": {"zh": "\n... 以及 specs/ 目录下的 tasks.md、review 模板、fix-orders 模板、acceptance.md 等\n",
                            "en": "\n... plus tasks.md, review templates, fix-order templates, acceptance.md under specs/\n"},

    # 模式元信息
    "mode": {
        "architect-engineer": {
            "name": {"zh": "Architect-Engineer", "en": "Architect-Engineer"},
            "desc": {"zh": "GPT 做架构师（规划/审查/验收），Reasonix 做工程师（编码/测试/修复）。9 道工序流水线，含交付审查门、整改闭环、GPT 升级修复机制。",
                     "en": "GPT as architect (plan/review/accept), Reasonix as engineer (code/test/fix). 9-stage pipeline with review gate, fix loop, and GPT escalation mode."},
        },
        "peer-review": {
            "name": {"zh": "Peer-Review", "en": "Peer-Review"},
            "desc": {"zh": "两个平等的 AI Agent 互相协作和审查。各自实现不同模块并交叉审查。",
                     "en": "Two equal AI agents collaborate and cross-review each other's work on different modules."},
        },
        "spec-driven": {
            "name": {"zh": "Spec-Driven", "en": "Spec-Driven"},
            "desc": {"zh": "规范先行，严格门禁。先写完整规范，再按任务逐个实现和审查。",
                     "en": "Spec-first with strict gates. Write complete specs first, then implement and review task by task."},
        },
        "quick-start": {
            "name": {"zh": "Quick-Start", "en": "Quick-Start"},
            "desc": {"zh": "最小化设置。只有一个 AGENTS.md + COLLAB.md，适合快速原型和小项目。",
                     "en": "Minimal setup. Just AGENTS.md + COLLAB.md. For rapid prototypes and small projects."},
        },
        "parallel-team": {
            "name": {"zh": "Parallel-Team", "en": "Parallel-Team"},
            "desc": {"zh": "两个 Agent 同时并行工作。通过分离状态文件 + git 仲裁解决并发冲突。",
                     "en": "Two agents work in parallel. Solves concurrency via split state files + git arbitration."},
        },
    },

    # 流水线阶段名称
    "stage": {
        "discovery":    {"zh": "需求澄清",   "en": "Discovery"},
        "architecture": {"zh": "架构设计",   "en": "Architecture"},
        "task_breakdown":{"zh":"任务分解",   "en": "Task Breakdown"},
        "implement":    {"zh": "编码实现",   "en": "Implementation"},
        "self_test":    {"zh": "自测验证",   "en": "Self-Test"},
        "review":       {"zh": "交付审查",   "en": "Review"},
        "fix":          {"zh": "整改修复",   "en": "Fix"},
        "escalation":   {"zh": "升级修复",   "en": "Escalation Fix"},
        "acceptance":   {"zh": "最终验收",   "en": "Final Acceptance"},
        "plan_together":{"zh": "联合规划",   "en": "Joint Planning"},
        "claim_tasks":  {"zh": "认领任务",   "en": "Claim Tasks"},
        "parallel_work":{"zh": "并行开发",   "en": "Parallel Dev"},
        "merge_review": {"zh": "合并审查",   "en": "Merge Review"},
        "fix_merge":    {"zh": "合并修复",   "en": "Fix Merge"},
        "final_accept": {"zh": "最终验收",   "en": "Final Accept"},
        "plan":         {"zh": "规划",       "en": "Plan"},
        "build":        {"zh": "构建",       "en": "Build"},
        "check":        {"zh": "检查",       "en": "Check"},
        "plan_together_p":{"zh":"联合规划",  "en": "Joint Planning"},
        "impl_review":  {"zh": "实现+审查循环","en":"Impl+Review Loop"},
        "integration":  {"zh": "集成验证",   "en": "Integration"},
        "signoff":      {"zh": "签字交付",   "en": "Signoff"},
        "spec_init":    {"zh": "规范初始化", "en": "Spec Init"},
        "requirements": {"zh": "需求编写",   "en": "Requirements"},
        "design":       {"zh": "架构设计",   "en": "Design"},
        "tasks":        {"zh": "任务分解",   "en": "Tasks"},
        "parallel_impl":{"zh": "并行实现",   "en": "Parallel Impl"},
        "cross_review": {"zh": "交叉审查",   "en": "Cross Review"},
        "merge_test":   {"zh": "合并测试",   "en": "Merge Test"},
        "joint_accept": {"zh": "联合验收",   "en": "Joint Accept"},
        "goal":         {"zh": "定义目标",   "en": "Define Goal"},
        "execute":      {"zh": "执行构建",   "en": "Execute Build"},
        "verify":       {"zh": "独立验证",   "en": "Verify"},
        "settle":       {"zh": "结算",       "en": "Settle"},
        "init_specs":   {"zh": "初始化 Spec","en": "Init Specs"},
        "claim":        {"zh": "认领 Spec",  "en": "Claim Specs"},
    },

    # 模板内容
    "tmpl": {
        "agents_header": {
            "zh": "# AGENTS.md — 项目身份证 & 协作流水线定义\n\n> 本文件是项目的永久元信息，两个 agent 启动时第一件事就是读它。\n> 修改频率：低（技术栈/流水线变更时更新）。",
            "en": "# AGENTS.md — Project Identity & Collaboration Pipeline\n\n> This file is the permanent metadata of the project. Both agents read it first on startup.\n> Update frequency: Low (only when tech stack or pipeline changes)."
        },
        "agents_project_info": {
            "zh": "## 项目信息\n\n- **名称**：{name}\n- **创建时间**：{time}\n- **协作模式**：{mode}",
            "en": "## Project Info\n\n- **Name**: {name}\n- **Created**: {time}\n- **Mode**: {mode}"
        },
        "agents_tech_stack": {
            "zh": "## 技术栈\n\n<!-- 首次规划后填写 -->",
            "en": "## Tech Stack\n\n<!-- Fill after initial planning -->"
        },
        "agents_roles": {
            "zh": "## 协作模式\n\n### Agent A — {a_name}\n- **角色**：{a_role}\n- **模型**：{a_model}\n- **职责**：{a_duties}\n\n### Agent B — {b_name}\n- **角色**：{b_role}\n- **模型**：{b_model}\n- **职责**：{b_duties}",
            "en": "## Collaboration Mode\n\n### Agent A — {a_name}\n- **Role**: {a_role}\n- **Model**: {a_model}\n- **Duties**: {a_duties}\n\n### Agent B — {b_name}\n- **Role**: {b_role}\n- **Model**: {b_model}\n- **Duties**: {b_duties}"
        },
        "agents_pipeline": {
            "zh": "## 开发流水线（{n} 道工序）\n\n```\n{stages}\n```",
            "en": "## Development Pipeline ({n} stages)\n\n```\n{stages}\n```"
        },
        "agents_rules": {
            "zh": """## 关键规则

1. **COLLAB.md 是唯一真相源**：所有状态变更必须写入，不靠记忆
2. **失败不跳级**：任何阶段不通过必须回到实现层重做
3. **先读后写**：每个 agent 启动时第一件事：读 AGENTS.md → COLLAB.md → specs/
4. **无证据不签字**：验收必须基于可验证证据""",
            "en": """## Key Rules

1. **COLLAB.md is the single source of truth**: All state changes must be written, not memorized
2. **Failures don't skip stages**: Any stage failure must return to implementation
3. **Read before write**: Every agent reads AGENTS.md → COLLAB.md → specs/ on startup
4. **No evidence, no sign-off**: Acceptance requires verifiable evidence"""
        },
        "collab_header": {
            "zh": """# COLLAB.md — Agent 实时协作状态

> ⚠️ **唯一真相源**：所有 agent 启动时第一读取、结束前最后写入。
> 保持精简（3分钟可读完），过期信息删除，不要堆积历史。""",
            "en": """# COLLAB.md — Agent Real-Time Collaboration State

> ⚠️ **Single source of truth**: Every agent reads this first on startup and writes last before exit.
> Keep it concise (3 min read), delete stale info, don't accumulate history."""
        },
        "collab_current_stage": {
            "zh": "## 📍 当前流水线阶段\n\n<!-- 阶段流转：{flow} -->\n⏳ **{first_stage}** — 等待 {a_name} 启动",
            "en": "## 📍 Current Pipeline Stage\n\n<!-- Stage flow: {flow} -->\n⏳ **{first_stage}** — Waiting for {a_name} to start"
        },
        "collab_task_table_header": {
            "zh": "## 🗺️ 任务状态总览\n\n| 任务ID | 任务名称 | 状态 | 负责人 | 最新 commit | 迭代轮次 |\n|--------|---------|------|--------|------------|---------|\n| - | 等待 {a_name} 初始化 | - | - | - | - |",
            "en": "## 🗺️ Task Status Overview\n\n| Task ID | Name | Status | Owner | Latest Commit | Iteration |\n|--------|------|--------|-------|------------|-----------|\n| - | Awaiting {a_name} init | - | - | - | - |"
        },
        "collab_sections": {
            "zh": """---

## 🔍 当前任务详情

_等待 {a_name} 初始化_

---

## 📋 活跃决策

_暂无_

---

## 🐛 陷阱 & 已知问题

_暂无_

---

## 🚨 升级记录

_暂无升级_

---

## 📝 审查记录

_暂无审查_

---

## 🤝 Handoff 接力区

> **→ {a_name}**：等待首次项目规划。请阅读 AGENTS.md 了解流水线，然后在 specs/active/ 下创建规范文档。""",
            "en": """---

## 🔍 Current Task Details

_Waiting for {a_name} to initialize_

---

## 📋 Active Decisions

_None yet_

---

## 🐛 Pitfalls & Known Issues

_None yet_

---

## 🚨 Escalation Log

_No escalations_

---

## 📝 Review Log

_No reviews yet_

---

## 🤝 Handoff Zone

> **→ {a_name}**: Waiting for initial project planning. Please read AGENTS.md for the pipeline, then create spec docs under specs/active/."""
        },
        "tasks_header": {
            "zh": """# 任务列表 & 状态追踪

> 状态机：TODO → IN_PROGRESS → SELF_TESTED → UNDER_REVIEW → APPROVED / REVISION_REQUIRED → FIXING → ESCALATED → GPT_FIXING → ACCEPTED → DONE""",
            "en": """# Task List & Status Tracking

> State machine: TODO → IN_PROGRESS → SELF_TESTED → UNDER_REVIEW → APPROVED / REVISION_REQUIRED → FIXING → ESCALATED → GPT_FIXING → ACCEPTED → DONE"""
        },
        "tasks_meta": {
            "zh": """## 元信息

- **总任务数**：0
- **已完成**：0
- **进行中**：0

## 成本分级（agent-agnostic）

| Tier | 标签 | 含义 | 适合的 Agent 类型 |
|------|------|------|------------------|
| 🟢 LOW | 低复杂度 | 模板代码、CRUD、配置修改、简单修复 | 低成本模型（deepseek-v3 / 本地模型） |
| 🟡 MID | 中等复杂度 | 业务逻辑、重构、性能优化 | 中等模型 |
| 🔴 HIGH | 高复杂度 | 架构设计、安全审计、算法设计、代码审查 | 高能力模型（gpt-5.6-sol / claude-opus-4-8） |

> 此分级不绑定具体模型名称——由用户根据自己手头的模型自行映射。""",
            "en": """## Meta

- **Total Tasks**: 0
- **Completed**: 0
- **In Progress**: 0

## Cost Tier (agent-agnostic)

| Tier | Label | Meaning | Suitable Agent Type |
|------|-------|---------|---------------------|
| 🟢 LOW | Low complexity | Boilerplate, CRUD, config changes, simple fixes | Low-cost model (DeepSeek / local) |
| 🟡 MID | Medium complexity | Business logic, refactoring, optimization | Mid-tier model |
| 🔴 HIGH | High complexity | Architecture, security audit, algorithm design, code review | High-capability model (gpt-5.6-sol / claude-opus-4-8) |

> Tiers don't name specific models — you map them to whatever agents you have."""
        },
        "tasks_notes": {
            "zh": """_等待分解任务_

---

## 实现笔记（跨任务知识传递）

_暂无_""",
            "en": """_Awaiting task breakdown_

---

## Implementation Notes (cross-task knowledge transfer)

_None yet_"""
        },
        "review_template": {
            "zh": """# 审查报告：Task N — [任务标题]

- **审查日期**：YYYY-MM-DD
- **审查人**：[Agent Name]
- **审查轮次**：第 1 轮
- **被审查 commit**：abc1234

## 审查结论

✅ **通过** / ❌ **不通过，需整改**

## 审查维度

### 1. 功能完整性
- [ ] 验收标准逐条满足
- 问题：...

### 2. 代码质量
- [ ] 命名清晰、符合规范
- 问题：...

### 3. 测试覆盖
- [ ] 测试通过，覆盖率达标
- 问题：...

### 4. 安全性
- [ ] 无注入风险、无密钥泄露
- 问题：...

### 5. 架构合规
- [ ] 符合设计，未破坏模块边界
- 问题：...

## 整改清单（如果不通过）

| 编号 | 问题描述 | 严重程度 | 涉及文件 | 修复建议 |
|------|---------|---------|---------|---------|
| F-01 | ... | 🔴阻塞 / 🟡建议 | src/x.ts | ... |""",
            "en": """# Review Report: Task N — [Title]

- **Review Date**: YYYY-MM-DD
- **Reviewer**: [Agent Name]
- **Round**: 1
- **Commit Reviewed**: abc1234

## Verdict

✅ **Pass** / ❌ **Fail — Revision Required**

## Review Dimensions

### 1. Functional Completeness
- [ ] All acceptance criteria met
- Issues: ...

### 2. Code Quality
- [ ] Clear naming, follows conventions
- Issues: ...

### 3. Test Coverage
- [ ] Tests pass, coverage meets threshold
- Issues: ...

### 4. Security
- [ ] No injection risks, no secret leaks
- Issues: ...

### 5. Architecture Compliance
- [ ] Follows design, no boundary violations
- Issues: ...

## Fix Checklist (if failed)

| # | Issue | Severity | Files | Suggestion |
|---|-------|----------|-------|------------|
| F-01 | ... | 🔴Blocker / 🟡Suggestion | src/x.ts | ... |"""
        },
        "fix_template": {
            "zh": """# 整改指令：Task N — [任务标题] — 第 X 轮

- **下达日期**：YYYY-MM-DD
- **基于审查**：review/review-T00N.md
- **执行人**：[Agent Name]

## 整改项

### F-01：[问题简述] 🔴阻塞
- **审查指出**：...
- **期望结果**：...
- **涉及文件**：src/xxx.ts

## 整改后自检

- [ ] 所有 🔴 阻塞项已修复
- [ ] 所有测试仍然通过
- [ ] 已 git commit""",
            "en": """# Fix Order: Task N — [Title] — Round X

- **Issued**: YYYY-MM-DD
- **Based on review**: review/review-T00N.md
- **Assignee**: [Agent Name]

## Fix Items

### F-01: [Brief] 🔴Blocker
- **Review finding**: ...
- **Expected result**: ...
- **Affected files**: src/xxx.ts

## Post-Fix Self-Check

- [ ] All 🔴 blockers resolved
- [ ] All tests still pass
- [ ] Git committed"""
        },
        "acceptance_template": {
            "zh": """# 最终验收报告

> **签字人**：Agent A
> **原则**：无证据不签字

## 验收检查清单

### 1. 功能完整性
- [ ] 所有任务标记为 APPROVED 或 ACCEPTED
- 证据：...

### 2. 测试通过
- [ ] 全部测试通过
- 证据：...

### 3. 代码质量
- [ ] 无 linter 错误、无 TODO 残留
- 证据：...

### 4. 安全性
- [ ] 无密钥泄露、无注入风险
- 证据：...

### 5. 文档
- [ ] README 已更新
- 证据：...

### 6. 部署就绪
- [ ] 构建脚本正常运行
- 证据：...

---

## 验收结论

### ✅ 验收通过 / ⚠️ 有条件通过 / ❌ 不通过

- **验收人**：[Agent Name]
- **日期**：YYYY-MM-DD""",
            "en": """# Final Acceptance Report

> **Signatory**: Agent A
> **Principle**: No evidence, no sign-off

## Acceptance Checklist

### 1. Functional Completeness
- [ ] All tasks marked APPROVED or ACCEPTED
- Evidence: ...

### 2. Tests Passing
- [ ] All tests pass
- Evidence: ...

### 3. Code Quality
- [ ] No linter errors, no TODO leftovers
- Evidence: ...

### 4. Security
- [ ] No secret leaks, no injection risks
- Evidence: ...

### 5. Documentation
- [ ] README updated
- Evidence: ...

### 6. Deployment Ready
- [ ] Build scripts run successfully
- Evidence: ...

---

## Conclusion

### ✅ Accepted / ⚠️ Conditional / ❌ Rejected

- **Signatory**: [Agent Name]
- **Date**: YYYY-MM-DD"""
        },
        "escalation_file": {
            "zh": "# 升级记录\n\n暂无升级记录。\n",
            "en": "# Escalation Log\n\nNo escalations recorded.\n"
        },
        "parallel_guide": {
            "zh": """# 并行协作快速入门

## 文件分工

```
项目根目录/
├── AGENTS.md                  ← [只读] 项目元信息
├── agent-{a}.md              ← [{a} 专写] 状态文件
├── agent-{b}.md              ← [{b} 专写] 状态文件
├── board.md                   ← [共享写] 任务看板（git 仲裁）
├── tasks/
│   ├── T001-xxx.md           ← [共享写] 任务定义
│   └── T002-yyy.md
├── specs/
│   ├── overview.md           ← [只读] 项目总览
│   └── architecture.md       ← [只读] 架构设计
├── GIT_WORKTREE.md            ← [参考] worktree 隔离指南
└── src/                       ← [共享写] 实际代码
```

## {a} 启动流程

1. `git pull`
2. 读 `AGENTS.md` → `agent-{a}.md` → `board.md`
3. 认领任务 → 更新 board.md → `git commit` → `git push`
4. 写代码 → 更新自己的状态文件 → commit → push
5. 需要对方做的事写在「我需要对方做的事」区

## {b} 启动流程

1. `git pull`
2. 读 `AGENTS.md` → `agent-{b}.md` → `board.md`
3. 查看对方状态文件的「我需要对方做的事」区
4. 认领任务 → 更新 board.md → commit → push

## 关键原则

| 原则 | 说明 |
|------|------|
| 写完就 commit | 不要攒一堆改动再提交 |
| 开始前先 pull | 看到最新状态再动手 |
| 不写对方的文件 | 互斥写是防止冲突的基础 |
| 冲突不慌 | git rebase 后手动解决，在 board.md 记录 |""",
            "en": """# Parallel Collaboration Quick-Start

## File Ownership

```
project/
├── AGENTS.md                  ← [read-only] Project metadata
├── agent-{a}.md              ← [{a} exclusive write] Status file
├── agent-{b}.md              ← [{b} exclusive write] Status file
├── board.md                   ← [shared write] Task board (git-arbitrated)
├── tasks/
│   ├── T001-xxx.md           ← [shared write] Task definitions
│   └── T002-yyy.md
├── specs/
│   ├── overview.md           ← [read-only] Project overview
│   └── architecture.md       ← [read-only] Architecture design
├── GIT_WORKTREE.md            ← [reference] Worktree isolation guide
└── src/                       ← [shared write] Actual code
```

## {a} Startup Flow

1. `git pull`
2. Read `AGENTS.md` → `agent-{a}.md` → `board.md`
3. Claim a task → update board.md → `git commit` → `git push`
4. Write code → update own status file → commit → push
5. Put requests for the other agent in the "What I need from counterpart" section

## {b} Startup Flow

1. `git pull`
2. Read `AGENTS.md` → `agent-{b}.md` → `board.md`
3. Check counterpart's "What I need" section
4. Claim a task → update board.md → commit → push

## Key Principles

| Principle | Description |
|-----------|-------------|
| Commit after each change | Don't batch unrelated changes |
| Pull before starting | See latest state before acting |
| Never write counterpart's file | Mutex writes prevent conflicts |
| Don't panic on conflict | `git rebase`, resolve manually, log in board.md |"""
        },
        "readme_title": {
            "zh": "# {name} — {mode} 协作模式\n\n> {desc}",
            "en": "# {name} — {mode} Collaboration Mode\n\n> {desc}"
        },
        "readme_arch": {
            "zh": """## 协作架构

```
┌─────────────────┐         ┌─────────────────┐
│  {a_name:<15} │  specs/  │  {b_name:<15} │
│  {a_role:<15} │◄───────▶│  {b_role:<15} │
│  {a_duties}│  COLLAB │  {b_duties}│
└─────────────────┘         └─────────────────┘
```

## 流水线

```
{pipeline_flow}
```

## 关键文件

| 文件 | 作用 |
|------|------|
| `AGENTS.md` | 项目身份证 + 流水线定义 |
| `COLLAB.md` | 唯一真相源：当前状态 |
| `specs/active/tasks.md` | 任务分解 + 状态追踪 |
| `specs/active/review/` | 审查报告 |
| `specs/active/fix-orders/` | 整改指令 |

## 快速开始

### Agent A 启动
读 `AGENTS.md` → `COLLAB.md` → 执行你的流水线阶段

### Agent B 启动
读 `AGENTS.md` → `COLLAB.md` → `specs/active/tasks.md` → 开始编码

---

*由 Bridge 生成于 {time}*""",
            "en": """## Collaboration Architecture

```
┌─────────────────┐         ┌─────────────────┐
│  {a_name:<15} │  specs/  │  {b_name:<15} │
│  {a_role:<15} │◄───────▶│  {b_role:<15} │
│  {a_duties}│  COLLAB │  {b_duties}│
└─────────────────┘         └─────────────────┘
```

## Pipeline

```
{pipeline_flow}
```

## Key Files

| File | Purpose |
|------|---------|
| `AGENTS.md` | Project identity + pipeline definition |
| `COLLAB.md` | Single source of truth: current state |
| `specs/active/tasks.md` | Task breakdown + status tracking |
| `specs/active/review/` | Review reports |
| `specs/active/fix-orders/` | Fix instructions |

## Quick Start

### Agent A
Read `AGENTS.md` → `COLLAB.md` → execute your pipeline stage

### Agent B
Read `AGENTS.md` → `COLLAB.md` → `specs/active/tasks.md` → start coding

---

*Generated by Bridge at {time}*"""
        },
        "gitignore_content": {
            "zh": "# OS\n.DS_Store\nThumbs.db\n\n# IDE\n.vscode/\n.idea/\n\n# Dependencies\nnode_modules/\n__pycache__/\n*.pyc\n\n# Build\ndist/\nbuild/\ntarget/\n\n# Env\n.env\n.env.local\n",
            "en": "# OS\n.DS_Store\nThumbs.db\n\n# IDE\n.vscode/\n.idea/\n\n# Dependencies\nnode_modules/\n__pycache__/\n*.pyc\n\n# Build\ndist/\nbuild/\ntarget/\n\n# Env\n.env\n.env.local\n"
        },
    },
}

# ═══════════════════════════════════════════════════════════════
# 模型注册表 — 当前主流模型，agent-agnostic
# ═══════════════════════════════════════════════════════════════

MODEL_REGISTRY = {
    # ── OpenAI (July 2026) ──
    "gpt-5.6-sol":      {"vendor": "OpenAI", "tier": "high",  "cost": "$$$", "notes": "旗舰，复杂推理+编码 $5/$30"},
    "gpt-5.6-terra":    {"vendor": "OpenAI", "tier": "high",  "cost": "$$",  "notes": "平衡智能与成本 $2.50/$15"},
    "gpt-5.6-luna":     {"vendor": "OpenAI", "tier": "mid",   "cost": "$",   "notes": "高性价比 $1/$6"},
    "o3":               {"vendor": "OpenAI", "tier": "high",  "cost": "$$$", "notes": "推理模型（legacy）"},
    "o4-mini":          {"vendor": "OpenAI", "tier": "high",  "cost": "$$",  "notes": "轻量推理"},
    # ── Anthropic (July 2026) ──
    "claude-fable-5":   {"vendor": "Anthropic", "tier": "high",  "cost": "$$$", "notes": "最强 Claude $10/$50"},
    "claude-opus-4-8":  {"vendor": "Anthropic", "tier": "high",  "cost": "$$",  "notes": "复杂编码+企业 $5/$25"},
    "claude-sonnet-5":  {"vendor": "Anthropic", "tier": "high",  "cost": "$$",  "notes": "速度+智能最佳比 $3/$15"},
    "claude-haiku-4-5": {"vendor": "Anthropic", "tier": "low",   "cost": "$",   "notes": "最快 $1/$5"},
    # ── Google (July 2026) ──
    "gemini-3.5-flash":       {"vendor": "Google", "tier": "high",  "cost": "$$",  "notes": "Gemini 最强 agentic/编码"},
    "gemini-3.1-pro":         {"vendor": "Google", "tier": "high",  "cost": "$$",  "notes": "高级推理（preview）"},
    "gemini-3.1-flash-lite":  {"vendor": "Google", "tier": "low",   "cost": "$",   "notes": "最强性价比"},
    "gemini-2.5-pro":         {"vendor": "Google", "tier": "high",  "cost": "$$$", "notes": "深度推理"},
    "gemini-2.5-flash":       {"vendor": "Google", "tier": "mid",   "cost": "$",   "notes": "价格性能最佳比"},
    "gemini-2.5-flash-lite":  {"vendor": "Google", "tier": "low",   "cost": "$",   "notes": "最快最便宜"},
    # ── DeepSeek ──
    "deepseek-v3":     {"vendor": "DeepSeek", "tier": "high",  "cost": "$",   "notes": "MoE 旗舰，极便宜"},
    "deepseek-r1":     {"vendor": "DeepSeek", "tier": "high",  "cost": "$$",  "notes": "推理模型"},
    # ── 国产模型 ──
    "qwen3-max":       {"vendor": "Alibaba", "tier": "high",  "cost": "$$",  "notes": "通义千问旗舰"},
    "qwen3-plus":      {"vendor": "Alibaba", "tier": "mid",   "cost": "$",   "notes": "千问中等"},
    "qwen3-turbo":     {"vendor": "Alibaba", "tier": "low",   "cost": "$",   "notes": "千问快速"},
    "doubao-1.5-pro":  {"vendor": "ByteDance","tier": "high", "cost": "$",   "notes": "豆包旗舰"},
    "glm-4.5":         {"vendor": "Zhipu",   "tier": "high",  "cost": "$$",  "notes": "智谱旗舰"},
    "kimi-k2":         {"vendor": "Moonshot", "tier": "high",  "cost": "$",   "notes": "Kimi 最新"},
    "yi-lightning":    {"vendor": "01.AI",    "tier": "mid",   "cost": "$",   "notes": "零一万物"},
    # ── 开源/本地 ──
    "llama-4-maverick":{"vendor": "Meta",    "tier": "high",  "cost": "$",   "notes": "开源旗舰"},
    "llama-4-scout":   {"vendor": "Meta",    "tier": "mid",   "cost": "$",   "notes": "开源轻量"},
    "mistral-large":   {"vendor": "Mistral",  "tier": "high",  "cost": "$$",  "notes": "Mistral 旗舰"},
    "local-model":     {"vendor": "Local",    "tier": "varies","cost": "$",   "notes": "本地模型（Ollama/LM Studio）"},
}

def get_models_by_tier(tier=None):
    """按 tier 筛选模型"""
    if tier:
        return {k: v for k, v in MODEL_REGISTRY.items() if v["tier"] == tier}
    return MODEL_REGISTRY

# ═══════════════════════════════════════════════════════════════
# 模板定义
# ═══════════════════════════════════════════════════════════════

TEMPLATES = {
    "architect-engineer": {
        "name": "Architect-Engineer",
        "description": "GPT 做架构师（规划/审查/验收），Reasonix 做工程师（编码/测试/修复）。9 道工序流水线，含交付审查门、整改闭环、GPT 升级修复机制。",
        "icon": "🏗️",
        "pipeline": [
            {"id": "discovery",    "name": "需求澄清",   "agent": "Agent A", "desc": "分析需求，明确范围和用户故事"},
            {"id": "architecture", "name": "架构设计",   "agent": "Agent A", "desc": "技术选型、模块划分、API 设计"},
            {"id": "task_breakdown","name":"任务分解",   "agent": "Agent A", "desc": "将需求拆解为可独立实现的任务"},
            {"id": "implement",    "name": "编码实现",   "agent": "Agent B", "desc": "读 spec，写代码，跑测试（TDD）"},
            {"id": "self_test",    "name": "自测验证",   "agent": "Agent B", "desc": "运行全部测试，确保无回归"},
            {"id": "review",       "name": "交付审查",   "agent": "Agent A", "desc": "5 维度审查：安全/功能/测试/架构/质量"},
            {"id": "fix",          "name": "整改修复",   "agent": "Agent B", "desc": "修复审查指出的问题（最多 2 轮）"},
            {"id": "escalation",   "name": "升级修复",   "agent": "Agent A", "desc": "2 轮修复失败后 Agent A 亲自下场"},
            {"id": "acceptance",   "name": "最终验收",   "agent": "Agent A", "desc": "6 维度验收清单，无证据不签字"},
        ],
        "agent_a": {"name": "GPT", "role": "架构师 / 审核员", "model": "gpt-5.6-sol"},
        "agent_b": {"name": "Reasonix", "role": "工程师 / 执行者", "model": "deepseek-v3"},
    },

    "peer-review": {
        "name": "Peer-Review",
        "description": "两个平等的 AI Agent 互相协作和审查。适合两个能力相近的模型（如 GPT + Claude），各自实现不同模块并交叉审查。",
        "icon": "🤝",
        "pipeline": [
            {"id": "plan_together", "name": "联合规划",   "agent": "Both",    "desc": "两个 Agent 共同制定计划和分工"},
            {"id": "parallel_impl", "name": "并行实现",   "agent": "Both",    "desc": "各自认领模块，并行编码"},
            {"id": "cross_review",  "name": "交叉审查",   "agent": "Both",    "desc": "交换审查对方的代码"},
            {"id": "merge_test",    "name": "合并测试",   "agent": "Both",    "desc": "合并代码，运行集成测试"},
            {"id": "joint_accept",  "name": "联合验收",   "agent": "Both",    "desc": "共同确认交付质量"},
        ],
        "agent_a": {"name": "GPT", "role": "模块 A 负责人", "model": "gpt-5.6-sol"},
        "agent_b": {"name": "Claude", "role": "模块 B 负责人", "model": "claude-sonnet-4-5"},
    },

    "spec-driven": {
        "name": "Spec-Driven",
        "description": "规范先行，严格门禁。参考 cc-sdd 和 AppGenesisForge 的设计理念。先写完整规范，再按任务逐个实现和审查。",
        "icon": "📋",
        "pipeline": [
            {"id": "discovery",    "name": "需求发现",   "agent": "Agent A", "desc": "路由需求，确定是否需创建 spec"},
            {"id": "spec_init",    "name": "规范初始化", "agent": "Agent A", "desc": "创建规范文档骨架"},
            {"id": "requirements", "name": "需求编写",   "agent": "Agent A", "desc": "EARS 格式需求 + 验收标准"},
            {"id": "design",       "name": "架构设计",   "agent": "Agent A", "desc": "架构图、文件结构规划、边界定义"},
            {"id": "tasks",        "name": "任务分解",   "agent": "Agent A", "desc": "任务列表含依赖和边界标注"},
            {"id": "impl_review",  "name": "实现+审查循环","agent":"Both",   "desc": "逐任务：实现 → 独立审查 → 修复 → 通过"},
            {"id": "integration",  "name": "集成验证",   "agent": "Agent A", "desc": "跨任务集成检查"},
            {"id": "signoff",      "name": "签字交付",   "agent": "Agent A", "desc": "最终验收签字"},
        ],
        "agent_a": {"name": "GPT", "role": "规范编写者 / 审查员", "model": "gpt-5.6-sol"},
        "agent_b": {"name": "Reasonix", "role": "任务实现者", "model": "deepseek-v3"},
    },

    "quick-start": {
        "name": "Quick-Start",
        "description": "最小化设置。只有一个 AGENTS.md + COLLAB.md，不定义严格流水线，适合快速原型和小项目。",
        "icon": "⚡",
        "pipeline": [
            {"id": "plan",  "name": "规划", "agent": "Agent A", "desc": "写需求和任务"},
            {"id": "build", "name": "构建", "agent": "Agent B", "desc": "编码实现"},
            {"id": "check", "name": "检查", "agent": "Agent A", "desc": "快速审查"},
        ],
        "agent_a": {"name": "GPT", "role": "规划者", "model": "gpt-5.6-sol"},
        "agent_b": {"name": "Reasonix", "role": "执行者", "model": "deepseek-v3"},
    },

    "parallel-team": {
        "name": "Parallel-Team",
        "description": "两个 Agent 同时并行工作。通过分离状态文件 + git 仲裁解决并发冲突。适合各有独立模块可并行推进的项目。",
        "icon": "⚡⚡",
        "pipeline": [
            {"id": "plan_together", "name": "联合规划",   "agent": "Both",    "desc": "共同制定架构和任务分解"},
            {"id": "claim_tasks",   "name": "认领任务",   "agent": "Both",    "desc": "各自认领 board.md 上的无主任务"},
            {"id": "parallel_work", "name": "并行开发",   "agent": "Both",    "desc": "各干各的，通过 git 同步进度"},
            {"id": "merge_review",  "name": "合并审查",   "agent": "Agent A", "desc": "Agent A 审查合并后的代码"},
            {"id": "fix_merge",     "name": "合并修复",   "agent": "Both",    "desc": "解决合并冲突和审查意见"},
            {"id": "final_accept",  "name": "最终验收",   "agent": "Agent A", "desc": "全量验收签字"},
        ],
        "agent_a": {"name": "GPT", "role": "架构师 / 审查员", "model": "gpt-5.6-sol"},
        "agent_b": {"name": "Reasonix", "role": "主力工程师", "model": "deepseek-v3"},
    },

    "loop-engineering": {
        "name": "Loop-Engineering",
        "description": "借鉴 loop-engineering (8.9k⭐) + loop.js 设计。Goal→Execute→Verify→Settle。独立 Verify agent，预算守卫，分层门禁，适合长周期自主开发。",
        "icon": "🔄",
        "pipeline": [
            {"id": "goal",       "name": "定义目标",   "agent": "Agent A", "desc": "明确 Goal：什么是「完成」"},
            {"id": "plan",       "name": "制定计划",   "agent": "Agent A", "desc": "架构 + 任务分解"},
            {"id": "execute",    "name": "执行构建",   "agent": "Agent B", "desc": "读 spec → 编码 → 自测 → 写 handoff"},
            {"id": "verify",     "name": "独立验证",   "agent": "Agent A", "desc": "独立 Verify agent 判定是否通过（从不自己打分）"},
            {"id": "settle",     "name": "结算",       "agent": "Agent A", "desc": "ok→交付 / not yet→返回 Execute / impossible→放弃"},
        ],
        "agent_a": {"name": "GPT", "role": "Goal 定义者 / Verify 裁判", "model": "gpt-5.6-sol"},
        "agent_b": {"name": "Reasonix", "role": "Execute 执行者", "model": "deepseek-v3"},
    },

    "parallel-claim": {
        "name": "Parallel-Claim",
        "description": "借鉴 LoopGate 的 Claim 认领机制。多 Agent 通过 spec claim 行无冲突并行，不依赖 worktree。适合 2+ Agent 同时工作的场景。",
        "icon": "🏷️",
        "pipeline": [
            {"id": "init_specs",  "name": "初始化 Spec",  "agent": "Agent A", "desc": "创建 specs/ 目录，每个 spec 含 claim 行"},
            {"id": "claim",       "name": "认领 Spec",    "agent": "Both",    "desc": "Agent 读取 specs/，认领无主 spec 后 commit"},
            {"id": "build",       "name": "并行构建",     "agent": "Both",    "desc": "各 Agent 在认领的 spec 范围内编码"},
            {"id": "verify",      "name": "独立验证",     "agent": "Agent A", "desc": "Agent A 验证所有 spec 的完成情况"},
            {"id": "settle",      "name": "结算交付",     "agent": "Agent A", "desc": "全部通过→交付 / 未通过→退回对应 Agent"},
        ],
        "agent_a": {"name": "GPT", "role": "Spec 管理者 / 验证者", "model": "gpt-5.6-sol"},
        "agent_b": {"name": "Agent B", "role": "Spec 执行者", "model": "Configurable"},
    },
}


# ═══════════════════════════════════════════════════════════════
# 文件生成引擎
# ═══════════════════════════════════════════════════════════════

def generate_agents_md(mode, agent_a, agent_b, project_name="未命名项目", lang="zh"):
    """生成 AGENTS.md 内容"""
    tmpl = TEMPLATES[mode]
    a_name = agent_a.get('name', 'Agent A')
    a_role = agent_a.get('role', '')
    a_model = agent_a.get('model', '')
    b_name = agent_b.get('name', 'Agent B')
    b_role = agent_b.get('role', '')
    b_model = agent_b.get('model', '')

    a_duties = "、".join([T(f"stage.{s['id']}", lang) for s in tmpl["pipeline"] if s['agent'] in ('Agent A', 'Both')])
    b_duties = "、".join([T(f"stage.{s['id']}", lang) for s in tmpl["pipeline"] if s['agent'] in ('Agent B', 'Both')])

    stages_str = ""
    for i, stage in enumerate(tmpl["pipeline"]):
        stages_str += "│  " + str(i+1) + ". " + T(f"stage.{stage['id']}", lang) + " (" + stage['agent'] + ")\n"

    return T("tmpl.agents_header", lang) + "\n\n" + \
           T("tmpl.agents_project_info", lang, name=project_name,
             time=datetime.now().strftime('%Y-%m-%d %H:%M'),
             mode=T(f"mode.{mode}.name", lang)) + "\n\n" + \
           T("tmpl.agents_tech_stack", lang) + "\n\n" + \
           T("tmpl.agents_roles", lang, a_name=a_name, a_role=a_role, a_model=a_model,
             a_duties=a_duties, b_name=b_name, b_role=b_role, b_model=b_model, b_duties=b_duties) + "\n\n" + \
           T("tmpl.agents_pipeline", lang, n=len(tmpl["pipeline"]), stages=stages_str.strip()) + "\n\n" + \
           T("tmpl.agents_rules", lang)


def generate_collab_md(mode, agent_a, agent_b, pipeline_custom=None, lang="zh"):
    """生成 COLLAB.md 内容"""
    tmpl = TEMPLATES[mode]
    pipeline = pipeline_custom if pipeline_custom else tmpl["pipeline"]
    a_name = agent_a.get('name', 'Agent A')
    flow = " → ".join([T(f"stage.{s['id']}", lang) for s in pipeline])
    first = T(f"stage.{pipeline[0]['id']}", lang)
    return T("tmpl.collab_header", lang) + "\n\n---\n\n" + \
           T("tmpl.collab_current_stage", lang, flow=flow, first_stage=first, a_name=a_name) + "\n\n---\n\n" + \
           T("tmpl.collab_task_table_header", lang, a_name=a_name) + "\n\n" + \
           T("tmpl.collab_sections", lang, a_name=a_name)


def generate_tasks_md(mode, lang="zh"):
    """生成 tasks.md 模板"""
    return T("tmpl.tasks_header", lang) + "\n\n---\n\n" + \
           T("tmpl.tasks_meta", lang) + "\n\n---\n\n" + \
           T("tmpl.tasks_notes", lang)


def generate_review_template(lang="zh"):
    return T("tmpl.review_template", lang)


def generate_fix_template(lang="zh"):
    return T("tmpl.fix_template", lang)


def generate_acceptance_md(lang="zh"):
    return T("tmpl.acceptance_template", lang)


def generate_readme_md(mode, agent_a, agent_b, project_name, lang="zh"):
    """生成 README.md"""
    tmpl = TEMPLATES[mode]
    a_name = agent_a.get('name', 'Agent A')
    a_role = agent_a.get('role', '')
    b_name = agent_b.get('name', 'Agent B')
    b_role = agent_b.get('role', '')

    a_short = "、".join([T(f"stage.{s['id']}", lang) for s in tmpl["pipeline"] if s['agent'] in ('Agent A', 'Both')][:3])
    b_short = "、".join([T(f"stage.{s['id']}", lang) for s in tmpl["pipeline"] if s['agent'] in ('Agent B', 'Both')][:3])

    pipe_flow = ""
    for i, s in enumerate(tmpl["pipeline"]):
        arrow = "" if i == len(tmpl["pipeline"]) - 1 else " ──→"
        pipe_flow += "  " + T(f"stage.{s['id']}", lang) + " (" + s['agent'] + ")" + arrow + "\n"

    return T("tmpl.readme_title", lang, name=project_name, mode=T(f"mode.{mode}.name", lang),
             desc=T(f"mode.{mode}.desc", lang)) + "\n\n" + \
           T("tmpl.readme_arch", lang, a_name=a_name, a_role=a_role, b_name=b_name, b_role=b_role,
             a_duties=a_short, b_duties=b_short, pipeline_flow=pipe_flow.strip(),
             time=datetime.now().strftime('%Y-%m-%d %H:%M'))


# ═══════════════════════════════════════════════════════════════
# 并行模式（Parallel-Team）专用生成函数
# ═══════════════════════════════════════════════════════════════

def generate_agent_status_md(agent_name, agent_role, counterpart_name):
    """生成单个 agent 的独立状态文件（并行模式核心文件）"""
    return f"""# agent-{agent_name.lower()}.md — {agent_name} 状态文件

> ⚠️ **只有 {agent_name} 写入此文件，{counterpart_name} 只读**。这是并行模式解决并发冲突的关键设计。
> 修改后立即 git commit，每次启动前 git pull。

---

## 当前状态

🔄 **工作中** — [当前阶段]

## 我认领的任务

| 任务ID | 任务名称 | 状态 | 最新 commit | 备注 |
|--------|---------|------|------------|------|
| - | 等待认领 | - | - | - |

## 我完成的里程碑

- [ ] 无

## 我需要 {counterpart_name} 做的事

<!-- 写在这里，对方下次 git pull 后就能看到 -->
_暂无_

## 我遇到的阻塞

_暂无_

## Handoff

> **{agent_name} → {counterpart_name}**：等待联合规划阶段完成。
"""


def generate_board_md(agent_a_name, agent_b_name, lang="zh"):
    """生成共享任务看板（并行模式核心）"""
    table = f"""| 任务ID | 任务名称 | 状态 | OWNER | 复杂度 | 涉及模块 | 验收标准 |
|--------|---------|------|-------|--------|---------|---------|
| - | 等待规划 | - | - | - | - | - |""" if lang == "zh" else f"""| Task ID | Name | Status | OWNER | Tier | Module | Acceptance |
|--------|------|--------|-------|------|--------|------------|
| - | Awaiting plan | - | - | - | - | - |"""

    rules = f"""## {'成本路由规则' if lang == 'zh' else 'Cost Routing Rules'}

| {'复杂度' if lang == 'zh' else 'Tier'} | {'应由谁做' if lang == 'zh' else 'Assigned To'} | {'原因' if lang == 'zh' else 'Rationale'} |
|--------|---------|------|
| 🟢 LOW | {agent_b_name}（{'低成本 Agent' if lang == 'zh' else 'Low-cost Agent'}） | {'简单代码不消耗贵模型 token' if lang == 'zh' else 'Simple code, saves expensive model tokens'} |
| 🟡 MID | {agent_b_name} {'或' if lang == 'zh' else 'or'} {agent_a_name} | {'视任务紧要程度' if lang == 'zh' else 'Depends on urgency'} |
| 🔴 HIGH | {agent_a_name}（{'高能力 Agent' if lang == 'zh' else 'High-capability Agent'}） | {'需要深度推理，便宜模型可能做不对' if lang == 'zh' else 'Needs deep reasoning; cheap models may fail'} |

> {'此路由规则不绑定模型名称。根据你实际使用的模型调整。' if lang == 'zh' else 'These rules are model-agnostic. Adjust based on your actual agents.'}

## {'并行规则速查' if lang == 'zh' else 'Parallel Rules Quick Reference'}

| {'规则' if lang == 'zh' else 'Rule'} | {'说明' if lang == 'zh' else 'Description'} |
|------|------|
| 🔒 {'互斥写' if lang == 'zh' else 'Mutex Write'} | `agent-{agent_a_name.lower()}.md` {'只有' if lang == 'zh' else 'only'} {agent_a_name} {'写' if lang == 'zh' else 'writes'}{'；' if lang == 'zh' else '; '}`agent-{agent_b_name.lower()}.md` {'只有' if lang == 'zh' else 'only'} {agent_b_name} {'写' if lang == 'zh' else 'writes'} |
| 📋 {'共享写' if lang == 'zh' else 'Shared Write'} | `board.md` {'和' if lang == 'zh' else 'and'} `tasks/*.md` {'都可以写，通过 git 控制并发' if lang == 'zh' else 'shared; git-arbitrated concurrency'} |
| 📖 {'只读' if lang == 'zh' else 'Read-Only'} | `AGENTS.md`、`specs/*.md` {'只读（规划阶段写完后不再改）' if lang == 'zh' else 'read-only after planning phase'} |
| 🔄 {'同步节奏' if lang == 'zh' else 'Sync Rhythm'} | {'每个 agent 完成一个原子操作后立即 git commit + git push；开始新操作前 git pull' if lang == 'zh' else 'commit+push after each atomic change; pull before starting'} |
| 💰 {'成本优化' if lang == 'zh' else 'Cost Optimization'} | {agent_a_name} {'只做' if lang == 'zh' else 'only handles'} 🔴HIGH {'规划/审查/验收' if lang == 'zh' else 'planning/review/acceptance'}{'；' if lang == 'zh' else '; '}{agent_b_name} {'包揽' if lang == 'zh' else 'covers'} 🟢LOW + 🟡MID {'编码实现' if lang == 'zh' else 'implementation'} |"""

    return f"""# board.md — {'共享任务看板' if lang == 'zh' else 'Shared Task Board'}

> {'🔴 **并发规则**：两个 agent 都可能修改此文件。' if lang == 'zh' else '🔴 **Concurrency Rule**: Both agents may edit this file.'}
> {'**修改前**：`git pull` → **修改后立即**：`git add board.md && git commit -m "[board] 更新任务状态"' if lang == 'zh' else '**Before editing**: `git pull` → **After editing immediately**: `git add board.md && git commit -m "[board] update task status"'}
> {'**冲突时**：后 commit 的人 `git pull --rebase`，手动解决冲突。' if lang == 'zh' else '**On conflict**: the later committer does `git pull --rebase` and resolves manually.'}

---

## {'任务池' if lang == 'zh' else 'Task Pool'}

<!--
{'认领规则：' if lang == 'zh' else 'Claim rules:'}
1. {'找到状态为 📌待认领 且复杂度匹配你能力的任务' if lang == 'zh' else 'Find tasks with 📌unclaimed status and a tier matching your capability'}
2. {'把 OWNER 改为你的名字，状态改为 🔄进行中' if lang == 'zh' else 'Change OWNER to your name, status to 🔄in progress'}
3. {'立即 git commit，避免冲突' if lang == 'zh' else 'Immediately git commit to avoid conflicts'}
4. {'如果两个 agent 同时认领同一个任务 → git rebase 时后者会看到冲突 → 放弃认领，选另一个任务' if lang == 'zh' else 'If two agents claim the same task → the later one sees a conflict on rebase → abandon and pick another'}
-->

{table}

## {'合并清单' if lang == 'zh' else 'Merge Checklist'}

<!-- {'任务完成后，OWNER 在此打勾。全部打勾后进入合并审查阶段。' if lang == 'zh' else 'OWNER checks off when done. All checked → merge review phase.'} -->
- [ ] {'无' if lang == 'zh' else 'None'}

## {'合并冲突日志' if lang == 'zh' else 'Merge Conflict Log'}

<!-- {'记录每次合并冲突及解决方案' if lang == 'zh' else 'Record each merge conflict and its resolution'} -->
| {'日期' if lang == 'zh' else 'Date'} | {'冲突文件' if lang == 'zh' else 'Conflict File'} | {'涉及人' if lang == 'zh' else 'Involved'} | {'解决方式' if lang == 'zh' else 'Resolution'} |
|------|---------|--------|---------|

{rules}
"""


def generate_git_worktree_guide():
    """生成 git worktree 隔离指南"""
    return """# GIT_WORKTREE.md — 可选：使用 Git Worktree 实现物理隔离

> 如果你遇到频繁的 git 冲突，可以使用 git worktree 让两个 agent 在
> 物理隔离的工作区中并行开发，彻底避免文件层面的并发问题。

## 原理

```
主仓库 (main branch)
    │
    ├── worktree-gpt/       ← GPT 的工作区（独立文件夹）
    │   └── 在 feature/gpt 分支上工作
    │
    └── worktree-reasonix/  ← Reasonix 的工作区（独立文件夹）
        └── 在 feature/reasonix 分支上工作
```

两个 agent 在不同文件夹里各自 git commit，互不干扰。
合并时由人类或 Agent A 在 main 分支上 git merge。

## 实际操作

```bash
# 1. 创建 worktree（只需做一次）
cd /path/to/project
git worktree add ../worktree-gpt feature/gpt
git worktree add ../worktree-reasonix feature/reasonix

# 2. GPT 在 worktree-gpt/ 下工作
cd ../worktree-gpt
# ... 编码、commit、push ...

# 3. Reasonix 在 worktree-reasonix/ 下工作  
cd ../worktree-reasonix
# ... 编码、commit、push ...

# 4. 合并（由人类操作）
cd /path/to/project   # 回到主仓库
git merge feature/gpt
git merge feature/reasonix
# 解决冲突（如有）
git push

# 5. 清理
git worktree remove ../worktree-gpt
git worktree remove ../worktree-reasonix
git branch -d feature/gpt feature/reasonix
```

## 何时用 worktree？

| 场景 | 推荐方案 |
|------|---------|
| 任务不重叠（各自改不同文件） | 基础方案：分离状态文件 + git 即可 |
| 可能改同一文件 | 用 worktree 隔离 |
| 频繁冲突 | 必须用 worktree |
| 小项目/快速原型 | 串行模式（Architect-Engineer）更简单 |
"""


def generate_parallel_struct(agent_a_name, agent_b_name):
    """生成并行模式的项目结构说明"""
    return f"""# 并行协作快速入门

## 文件分工

```
项目根目录/
├── AGENTS.md                  ← [只读] 项目元信息
├── agent-{agent_a_name.lower()}.md        ← [{agent_a_name} 专写] {agent_a_name}的状态
├── agent-{agent_b_name.lower()}.md     ← [{agent_b_name} 专写] {agent_b_name}的状态
├── board.md                   ← [共享写] 任务看板（git 仲裁）
├── tasks/
│   ├── T001-xxx.md           ← [共享写] 任务定义
│   └── T002-yyy.md
├── specs/
│   ├── overview.md           ← [只读] 项目总览
│   └── architecture.md       ← [只读] 架构设计
├── GIT_WORKTREE.md            ← [参考] worktree 隔离指南
└── src/                       ← [共享写] 实际代码
```

## {agent_a_name} 启动流程

1. `git pull`
2. 读 `AGENTS.md` → `agent-{agent_a_name.lower()}.md` → `board.md`
3. 认领任务 → 更新 board.md → `git commit` → `git push`
4. 写代码 → 更新自己的 agent-{agent_a_name.lower()}.md → commit → push
5. 需要 {agent_b_name} 做的事写在 agent-{agent_a_name.lower()}.md 的「我需要对方做的事」区

## {agent_b_name} 启动流程

1. `git pull`
2. 读 `AGENTS.md` → `agent-{agent_b_name.lower()}.md` → `board.md`
3. 查看 agent-{agent_a_name.lower()}.md 的「我需要对方做的事」区
4. 认领任务 → 更新 board.md → commit → push
5. 写代码 → 更新自己的状态文件 → commit → push

## 关键原则

| 原则 | 说明 |
|------|------|
| 写完就 commit | 不要攒一堆改动再提交 |
| 开始前先 pull | 看到最新状态再动手 |
| 不写对方的文件 | 互斥写是防止冲突的基础 |
| 冲突不慌 | git rebase 后手动解决，在 board.md 记录 |
"""


# ═══════════════════════════════════════════════════════════════
# Loop-Engineering & Parallel-Claim 专用函数
# ═══════════════════════════════════════════════════════════════

def generate_loop_budget_md(lang="zh"):
    """生成 loop-budget.md — 守护循环不失控"""
    zh = """# loop-budget.md — 循环预算追踪

> 借鉴 loop.js 的 guards 设计。守卫是「逃脱舱口」，不是「完成」的定义。

## 预算设置

| 守卫 | 值 | 说明 |
|------|-----|------|
| max_rounds | 20 | 最多执行轮次 |
| max_usd | $5.00 | 总 token 费用上限 |
| max_time_per_round | 15 min | 单轮超时 |

## 当前消耗

| 轮次 | 日期 | tokens in | tokens out | 费用 | 累计 | 耗时 |
|------|------|-----------|------------|------|------|------|
| 1 | - | - | - | - | - | - |

## 守卫触发记录

_无_

## 规则

- 任一守卫触发 → 立即停止，记录原因
- `not yet` 裁决不计入「失败」— 它是正常迭代
- `impossible` 裁决 → 明确放弃，保留预算
"""
    en = """# loop-budget.md — Loop Budget Tracker

> Guards are escape hatches, not definitions of "done". Inspired by loop.js.

## Budget Settings

| Guard | Value | Description |
|-------|-------|-------------|
| max_rounds | 20 | Maximum execution rounds |
| max_usd | $5.00 | Total token cost cap |
| max_time_per_round | 15 min | Per-round timeout |

## Current Consumption

| Round | Date | Tokens In | Tokens Out | Cost | Cumulative | Duration |
|-------|------|-----------|------------|------|------------|----------|
| 1 | - | - | - | - | - | - |

## Guard Triggers

_None_

## Rules

- Any guard fires → immediate stop, log reason
- "not yet" verdict is NOT a failure — it's normal iteration
- "impossible" verdict → explicit give-up, budget preserved
"""
    return zh if lang == "zh" else en


def generate_verify_template(lang="zh"):
    """生成 verify-template.md — 独立 Verify agent 的裁决模板"""
    zh = """# Verify 裁决：Round N

> ⚠️ **Verify agent 独立裁决。执行 agent 不得自己打分。**（loop.js 核心原则）

## 裁决

### ✅ ok — 通过
> Goal 已达成，证据确凿，可以交付。

### 🔄 not yet — 未通过
> 还需要改进。以下是具体原因：

**必须改进的项：**
1. ...

**建议改进的项：**
1. ...

**给下一轮 Execute 的提示：**
> ...

### ❌ impossible — 不可能
> 此 Goal 在当前约束下无法达成。
> 原因：...

---

## 验证依据

- [ ] 测试全部通过？→ 证据：...
- [ ] 功能满足 Goal？→ 证据：...
- [ ] 无安全风险？→ 证据：...
- [ ] 预算未超？→ 当前消耗：...

## 裁决人

- **Agent**：[Verify Agent Name]
- **日期**：YYYY-MM-DD
- **Round**：N
"""
    en = """# Verify Verdict: Round N

> ⚠️ **Independent Verify agent verdict. The executing agent never grades itself.** (loop.js core principle)

## Verdict

### ✅ ok — Pass
> Goal achieved with verifiable evidence. Ready to ship.

### 🔄 not yet — Retry
> Improvements needed. Specific reasons below:

**Must fix:**
1. ...

**Should improve:**
1. ...

**Hint for next Execute round:**
> ...

### ❌ impossible — Give Up
> Goal cannot be achieved under current constraints.
> Reason: ...

---

## Evidence Base

- [ ] All tests pass? → Evidence: ...
- [ ] Goal satisfied? → Evidence: ...
- [ ] No security risks? → Evidence: ...
- [ ] Budget not exceeded? → Current: ...

## Verdict By

- **Agent**: [Verify Agent Name]
- **Date**: YYYY-MM-DD
- **Round**: N
"""
    return zh if lang == "zh" else en


def generate_spec_claim_template(lang="zh"):
    """生成 spec claim 模板 — 借鉴 LoopGate 的 claim 机制"""
    zh = """# spec-claim-guide.md — Spec 认领机制

> 借鉴 LoopGate 的 "Spec claimed by agent: <unclaimed>" 机制。
> 多 Agent 并行时，通过 claim 行实现无冲突协作——不需要 worktree。

## 工作原理

每个 spec 文件顶部有一行：
```
Spec claimed by agent: <unclaimed>
```

Agent 在开始工作前：
1. 读取 specs/ 目录
2. 找到 `<unclaimed>` 的 spec
3. 将 `<unclaimed>` 替换为自己的名字
4. **立即 git commit**（这是关键——原子认领）
5. 开始在该 spec 范围内工作

完成或放弃时，将 claim 行恢复为 `<unclaimed>`。

## 冲突处理

- 如果 git pull 后发现自己的 claim 被覆盖 → 说明另一个 Agent 抢先认领了
- 如果发现所有 spec 都被认领 → 等待，或帮助已认领的 Agent
- **绝不修改别人的 claim 行**——这是互斥锁

## Spec 模板

```markdown
# Spec: [功能名称]
Spec claimed by agent: <unclaimed>
Complexity: 🟢LOW

## 目标
...

## 验收标准
- [ ] ...
- [ ] ...

## 涉及文件
- src/...
```
"""
    en = """# spec-claim-guide.md — Spec Claim Mechanism

> Inspired by LoopGate's "Spec claimed by agent: <unclaimed>" mechanism.
> Multi-agent parallel collaboration without conflicts — no worktree needed.

## How It Works

Each spec file has a line at the top:
```
Spec claimed by agent: <unclaimed>
```

Before starting work, an agent:
1. Reads the specs/ directory
2. Finds a spec marked `<unclaimed>`
3. Replaces `<unclaimed>` with their own name
4. **Immediately git commit** (this is the key — atomic claim)
5. Starts working within that spec's scope

When done or abandoning, restore the claim line to `<unclaimed>`.

## Conflict Handling

- If `git pull` shows your claim was overwritten → another agent claimed it first
- If all specs are claimed → wait, or help the claiming agent
- **Never modify someone else's claim line** — it's a mutex

## Spec Template

```markdown
# Spec: [Feature Name]
Spec claimed by agent: <unclaimed>
Complexity: 🟢LOW

## Goal
...

## Acceptance Criteria
- [ ] ...
- [ ] ...

## Affected Files
- src/...
```
"""
    return zh if lang == "zh" else en


# ═══════════════════════════════════════════════════════════════
# LLM 辅助模块
# ═══════════════════════════════════════════════════════════════

def call_llm(api_key, api_base, model, system_prompt, user_prompt, timeout=30):
    """调用 OpenAI 兼容 API"""
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 2000
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {err_body[:500]}")
    except Exception as e:
        raise RuntimeError(str(e))


def llm_enhance_description(api_key, api_base, model, user_input, mode_name):
    """用 LLM 理解用户输入并增强项目描述"""
    system_prompt = f"""你是一个 AI Agent 协作框架的配置助手。用户选择了「{mode_name}」协作模式。
请根据用户的描述，简洁地提取以下信息（JSON 格式）：
{{
  "project_name": "项目名称",
  "agent_a_role": "Agent A 的具体角色描述（20字以内）",
  "agent_b_role": "Agent B 的具体角色描述（20字以内）",
  "tech_stack": "推荐技术栈",
  "key_features": ["核心功能1", "核心功能2"]
}}
只输出 JSON，不要其他内容。"""

    result = call_llm(api_key, api_base, model, system_prompt, user_input)
    # 尝试提取 JSON
    result = result.strip()
    if result.startswith("```"):
        result = result.split("\n", 1)[1]
        if result.endswith("```"):
            result = result[:-3]
    return json.loads(result)


# ═══════════════════════════════════════════════════════════════
# GUI 界面
# ═══════════════════════════════════════════════════════════════

class BridgeApp:
    def __init__(self, root):
        self.root = root
        self.lang = "zh"
        self.root.title(T("window_title", self.lang))
        self.root.geometry("1000x720")
        self.root.minsize(900, 600)

        # 样式
        style = ttk.Style()
        style.theme_use("clam")

        # 数据
        self.target_dir = tk.StringVar(value="")
        self.mode = tk.StringVar(value="architect-engineer")
        self.project_name = tk.StringVar(value="")
        self.agent_a_name = tk.StringVar(value="GPT")
        self.agent_a_role = tk.StringVar(value=T("mode.architect-engineer.agent_a.role", self.lang) if False else "架构师 / 审核员")
        self.agent_a_model = tk.StringVar(value="gpt-5.6-sol")
        self.agent_b_name = tk.StringVar(value="Reasonix")
        self.agent_b_role = tk.StringVar(value="工程师 / 执行者")
        self.agent_b_model = tk.StringVar(value="deepseek-v3")

        # Agent C（可选）
        self.agent_c_enabled = tk.BooleanVar(value=False)
        self.agent_c_name = tk.StringVar(value="Agent C")
        self.agent_c_role = tk.StringVar(value="辅助执行者")
        self.agent_c_model = tk.StringVar(value="claude-haiku-4-5")

        # LLM 设置
        self.llm_enabled = tk.BooleanVar(value=False)
        self.llm_api_key = tk.StringVar(value="")
        self.llm_api_base = tk.StringVar(value="https://api.openai.com/v1")
        self.llm_model = tk.StringVar(value="gpt-5.6-luna")
        self.llm_user_input = tk.StringVar(value="")

        # 自定义流水线
        self.custom_pipeline = []

        self._build_ui()

    def _build_ui(self):
        # 主容器
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 顶部标题
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(title_frame, text="🌉 Bridge — AI Agent 协作桥接器",
                  font=("Microsoft YaHei", 16, "bold")).pack(side=tk.LEFT)
        ttk.Label(title_frame, text="在项目文件夹中生成 AI 协作流程文件",
                  font=("Microsoft YaHei", 9)).pack(side=tk.LEFT, padx=10)

        # 语言切换
        lang_frame = ttk.Frame(title_frame)
        lang_frame.pack(side=tk.RIGHT)
        self.btn_zh = ttk.Button(lang_frame, text="中", width=3,
                                  command=lambda: self._switch_lang("zh"))
        self.btn_zh.pack(side=tk.LEFT, padx=1)
        self.btn_en = ttk.Button(lang_frame, text="En", width=3,
                                  command=lambda: self._switch_lang("en"))
        self.btn_en.pack(side=tk.LEFT, padx=1)

        # Notebook 分页
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # ─── Tab 1: 项目设置 ───
        tab1 = ttk.Frame(notebook, padding=15)
        notebook.add(tab1, text="  📁 项目设置  ")

        # 项目文件夹
        ttk.Label(tab1, text="目标项目文件夹", font=("", 10, "bold")).pack(anchor=tk.W, pady=(0, 5))
        dir_frame = ttk.Frame(tab1)
        dir_frame.pack(fill=tk.X, pady=(0, 15))
        ttk.Entry(dir_frame, textvariable=self.target_dir, width=60).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dir_frame, text="浏览...", command=self._browse_dir).pack(side=tk.LEFT, padx=5)

        # 项目名称
        ttk.Label(tab1, text="项目名称", font=("", 10, "bold")).pack(anchor=tk.W, pady=(0, 5))
        ttk.Entry(tab1, textvariable=self.project_name, width=60).pack(fill=tk.X, pady=(0, 15))

        # 协作模式选择
        ttk.Label(tab1, text="协作模式", font=("", 10, "bold")).pack(anchor=tk.W, pady=(0, 10))

        mode_frame = ttk.Frame(tab1)
        mode_frame.pack(fill=tk.BOTH, expand=True)

        self.mode_frames = {}
        row = 0
        for mode_key, tmpl in TEMPLATES.items():
            concurrency = "⚡并行" if mode_key == "parallel-team" else "🔗串行"
            fm = ttk.LabelFrame(mode_frame, text=f"{tmpl['icon']} {tmpl['name']} ({concurrency})")
            fm.grid(row=row, column=0, sticky="ew", pady=3, padx=(0, 10))
            mode_frame.columnconfigure(0, weight=1)

            desc_frame = ttk.Frame(fm, padding=8)
            desc_frame.pack(fill=tk.X)

            ttk.Radiobutton(
                desc_frame, text=tmpl['description'][:80] + "...",
                variable=self.mode, value=mode_key,
                command=self._on_mode_change
            ).pack(anchor=tk.W)

            ttk.Label(desc_frame, text=f"工序：{' → '.join([s['name'] for s in tmpl['pipeline']])}",
                      font=("", 8), foreground="gray").pack(anchor=tk.W, padx=20)

            self.mode_frames[mode_key] = fm
            row += 1

        # ─── Tab 2: Agent 配置 ───
        tab2 = ttk.Frame(notebook, padding=15)
        notebook.add(tab2, text="  🤖 Agent 配置  ")

        # Agent A
        a_frame = ttk.LabelFrame(tab2, text="Agent A（架构师/规划者）", padding=10)
        a_frame.pack(fill=tk.X, pady=(0, 15))
        for i, (label, var) in enumerate([
            ("名称", self.agent_a_name), ("角色描述", self.agent_a_role), ("模型", self.agent_a_model)
        ]):
            ttk.Label(a_frame, text=label, width=10).grid(row=i, column=0, sticky=tk.W, pady=3)
            ttk.Entry(a_frame, textvariable=var, width=40).grid(row=i, column=1, sticky=tk.EW, padx=5)
        a_frame.columnconfigure(1, weight=1)

        # Agent B
        b_frame = ttk.LabelFrame(tab2, text="Agent B（工程师/执行者）", padding=10)
        b_frame.pack(fill=tk.X, pady=(0, 15))
        for i, (label, var) in enumerate([
            ("名称", self.agent_b_name), ("角色描述", self.agent_b_role), ("模型", self.agent_b_model)
        ]):
            ttk.Label(b_frame, text=label, width=10).grid(row=i, column=0, sticky=tk.W, pady=3)
            ttk.Entry(b_frame, textvariable=var, width=40).grid(row=i, column=1, sticky=tk.EW, padx=5)
        b_frame.columnconfigure(1, weight=1)

        # Agent C（可选，默认折叠）
        self.c_frame = ttk.LabelFrame(tab2, text="Agent C（可选 — 第三 Agent）", padding=10)
        self.c_toggle_btn = ttk.Button(tab2, text="➕ 添加第三个 Agent",
                                        command=self._toggle_agent_c)
        self.c_toggle_btn.pack(fill=tk.X, pady=(0, 5))
        # 默认隐藏
        for i, (label, var) in enumerate([
            ("名称", self.agent_c_name), ("角色描述", self.agent_c_role), ("模型", self.agent_c_model)
        ]):
            ttk.Label(self.c_frame, text=label, width=10).grid(row=i, column=0, sticky=tk.W, pady=3)
            ttk.Entry(self.c_frame, textvariable=var, width=40).grid(row=i, column=1, sticky=tk.EW, padx=5)
        self.c_frame.columnconfigure(1, weight=1)

        # ─── Tab 3: LLM 辅助 ───
        tab3 = ttk.Frame(notebook, padding=15)
        notebook.add(tab3, text="  🧠 LLM 辅助  ")

        ttk.Checkbutton(tab3, text="启用 LLM API 辅助理解需求",
                        variable=self.llm_enabled).pack(anchor=tk.W, pady=(0, 10))

        llm_frame = ttk.LabelFrame(tab3, text="API 设置", padding=10)
        llm_frame.pack(fill=tk.X, pady=(0, 15))

        fields = [
            ("API Key", self.llm_api_key, True),
            ("API Base URL", self.llm_api_base, False),
            ("模型", self.llm_model, False),
        ]
        for i, (label, var, is_secret) in enumerate(fields):
            ttk.Label(llm_frame, text=label, width=12).grid(row=i, column=0, sticky=tk.W, pady=3)
            entry = ttk.Entry(llm_frame, textvariable=var, width=50,
                             show="*" if is_secret else "")
            entry.grid(row=i, column=1, sticky=tk.EW, padx=5)
            if is_secret:
                self._llm_key_entry = entry

        ttk.Label(tab3, text="描述你的项目需求（LLM 将辅助分析并自动填充配置）：",
                  font=("", 9)).pack(anchor=tk.W, pady=(10, 5))
        self.llm_input_text = scrolledtext.ScrolledText(tab3, height=4, width=60)
        self.llm_input_text.pack(fill=tk.X, pady=(0, 10))
        self.llm_input_text.insert("1.0", "")

        llm_btn_frame = ttk.Frame(tab3)
        llm_btn_frame.pack(fill=tk.X)
        ttk.Button(llm_btn_frame, text="🤖 AI 分析需求",
                   command=self._llm_analyze).pack(side=tk.LEFT, padx=(0, 10))
        self.llm_status = ttk.Label(llm_btn_frame, text="", foreground="gray")
        self.llm_status.pack(side=tk.LEFT)

        # ─── Tab 4: 自定义流水线 ───
        tab4 = ttk.Frame(notebook, padding=15)
        notebook.add(tab4, text="  🔧 流水线编辑  ")

        ttk.Label(tab4, text="仅「Custom」模式下生效。拖拽排序未实现，请用上下按钮调整。",
                  font=("", 9), foreground="gray").pack(anchor=tk.W, pady=(0, 10))

        pipeline_edit_frame = ttk.Frame(tab4)
        pipeline_edit_frame.pack(fill=tk.BOTH, expand=True)

        # 流水线列表
        list_frame = ttk.Frame(pipeline_edit_frame)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        ttk.Label(list_frame, text="流水线阶段", font=("", 9, "bold")).pack(anchor=tk.W)
        self.pipeline_listbox = tk.Listbox(list_frame, height=12, selectmode=tk.SINGLE)
        self.pipeline_listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.pipeline_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.pipeline_listbox.config(yscrollcommand=scrollbar.set)

        # 编辑区
        edit_frame = ttk.Frame(pipeline_edit_frame)
        edit_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        stage_fields = [
            ("阶段名称", "stage_name"),
            ("执行 Agent", "stage_agent"),
            ("描述", "stage_desc"),
        ]
        self.stage_vars = {}
        for i, (label, key) in enumerate(stage_fields):
            ttk.Label(edit_frame, text=label, font=("", 9)).pack(anchor=tk.W, pady=(5, 0))
            var = tk.StringVar()
            self.stage_vars[key] = var
            ttk.Entry(edit_frame, textvariable=var, width=30).pack(fill=tk.X)

        btn_row = ttk.Frame(edit_frame)
        btn_row.pack(fill=tk.X, pady=10)
        ttk.Button(btn_row, text="➕ 添加", command=self._add_stage).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="✏️ 更新", command=self._update_stage).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="🗑 删除", command=self._delete_stage).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="⬆", command=self._move_stage_up, width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="⬇", command=self._move_stage_down, width=3).pack(side=tk.LEFT, padx=2)

        self.pipeline_listbox.bind("<<ListboxSelect>>", self._on_stage_select)

        # ─── 底部操作栏 ───
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill=tk.X, pady=(10, 0))

        # 预览区
        preview_frame = ttk.LabelFrame(main_frame, text="生成预览", padding=5)
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.preview_text = scrolledtext.ScrolledText(preview_frame, height=6, width=80,
                                                       font=("Consolas", 9))
        self.preview_text.pack(fill=tk.BOTH, expand=True)

        ttk.Button(bottom_frame, text="👁 预览生成内容",
                   command=self._preview).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(bottom_frame, text="🚀 生成到项目文件夹",
                   command=self._generate).pack(side=tk.LEFT)
        self.status_label = ttk.Label(bottom_frame, text="就绪", foreground="gray")
        self.status_label.pack(side=tk.RIGHT)

        # 初始化
        self._on_mode_change()
        self._init_custom_pipeline()

    def _browse_dir(self):
        d = filedialog.askdirectory(title="选择目标项目文件夹")
        if d:
            self.target_dir.set(d)
            # 自动提取项目名
            if not self.project_name.get():
                self.project_name.set(os.path.basename(d))

    def _on_mode_change(self, *args):
        mode = self.mode.get()
        if mode in TEMPLATES:
            tmpl = TEMPLATES[mode]
            self.agent_a_name.set(tmpl["agent_a"]["name"])
            self.agent_a_role.set(tmpl["agent_a"]["role"])
            self.agent_a_model.set(tmpl["agent_a"]["model"])
            self.agent_b_name.set(tmpl["agent_b"]["name"])
            self.agent_b_role.set(tmpl["agent_b"]["role"])
            self.agent_b_model.set(tmpl["agent_b"]["model"])

    def _toggle_agent_c(self):
        if self.agent_c_enabled.get():
            self.c_frame.pack_forget()
            self.c_toggle_btn.config(text="➕ 添加第三个 Agent")
            self.agent_c_enabled.set(False)
        else:
            self.c_frame.pack(fill=tk.X, pady=(0, 15), before=self.c_toggle_btn)
            self.c_toggle_btn.config(text="➖ 移除第三个 Agent")
            self.agent_c_enabled.set(True)

    def _switch_lang(self, lang):
        self.lang = lang
        set_lang(lang)
        self.root.title(T("window_title", lang))
        # 刷新模式标签
        for mode_key, fm in self.mode_frames.items():
            concurrency = "⚡Parallel" if mode_key == "parallel-team" else ("🔗Serial" if lang == "en" else "🔗串行")
            if lang == "zh":
                concurrency = "⚡并行" if mode_key == "parallel-team" else "🔗串行"
            fm.configure(text=f"{TEMPLATES[mode_key]['icon']} {T(f'mode.{mode_key}.name', lang)} ({concurrency})")
        self._on_mode_change()
        self.status_label.config(text=T("status_ready", lang))

    def _init_custom_pipeline(self):
        """初始化自定义流水线（使用 architect-engineer 作为默认）"""
        self.custom_pipeline = [
            {"id": f"s{i}", "name": s["name"], "agent": s["agent"], "desc": s["desc"]}
            for i, s in enumerate(TEMPLATES["architect-engineer"]["pipeline"])
        ]
        self._refresh_pipeline_list()

    def _refresh_pipeline_list(self):
        self.pipeline_listbox.delete(0, tk.END)
        for stage in self.custom_pipeline:
            self.pipeline_listbox.insert(tk.END, f"{stage['name']}  [{stage['agent']}]")

    def _on_stage_select(self, event):
        sel = self.pipeline_listbox.curselection()
        if sel:
            idx = sel[0]
            stage = self.custom_pipeline[idx]
            self.stage_vars["stage_name"].set(stage["name"])
            self.stage_vars["stage_agent"].set(stage["agent"])
            self.stage_vars["stage_desc"].set(stage["desc"])

    def _add_stage(self):
        name = self.stage_vars["stage_name"].get().strip()
        agent = self.stage_vars["stage_agent"].get().strip()
        desc = self.stage_vars["stage_desc"].get().strip()
        if not name:
            messagebox.showwarning("提示", "请输入阶段名称")
            return
        self.custom_pipeline.append({
            "id": f"s{len(self.custom_pipeline)}",
            "name": name, "agent": agent or "Both", "desc": desc
        })
        self._refresh_pipeline_list()

    def _update_stage(self):
        sel = self.pipeline_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self.custom_pipeline[idx]["name"] = self.stage_vars["stage_name"].get().strip()
        self.custom_pipeline[idx]["agent"] = self.stage_vars["stage_agent"].get().strip()
        self.custom_pipeline[idx]["desc"] = self.stage_vars["stage_desc"].get().strip()
        self._refresh_pipeline_list()

    def _delete_stage(self):
        sel = self.pipeline_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        del self.custom_pipeline[idx]
        self._refresh_pipeline_list()

    def _move_stage_up(self):
        sel = self.pipeline_listbox.curselection()
        if not sel or sel[0] == 0:
            return
        idx = sel[0]
        self.custom_pipeline[idx], self.custom_pipeline[idx-1] = \
            self.custom_pipeline[idx-1], self.custom_pipeline[idx]
        self._refresh_pipeline_list()
        self.pipeline_listbox.selection_set(idx - 1)

    def _move_stage_down(self):
        sel = self.pipeline_listbox.curselection()
        if not sel or sel[0] >= len(self.custom_pipeline) - 1:
            return
        idx = sel[0]
        self.custom_pipeline[idx], self.custom_pipeline[idx+1] = \
            self.custom_pipeline[idx+1], self.custom_pipeline[idx]
        self._refresh_pipeline_list()
        self.pipeline_listbox.selection_set(idx + 1)

    def _get_agent_configs(self):
        agents = [
            {"name": self.agent_a_name.get(), "role": self.agent_a_role.get(), "model": self.agent_a_model.get()},
            {"name": self.agent_b_name.get(), "role": self.agent_b_role.get(), "model": self.agent_b_model.get()},
        ]
        if self.agent_c_enabled.get():
            agents.append({"name": self.agent_c_name.get(), "role": self.agent_c_role.get(), "model": self.agent_c_model.get()})
        return agents

    def _llm_analyze(self):
        if not self.llm_enabled.get():
            messagebox.showinfo("提示", "请先勾选「启用 LLM API」")
            return
        user_input = self.llm_input_text.get("1.0", tk.END).strip()
        if not user_input:
            messagebox.showinfo("提示", "请先输入项目需求描述")
            return

        self.llm_status.config(text="⏳ 分析中...", foreground="blue")
        self.root.update()

        def task():
            try:
                api_key = self.llm_api_key.get().strip()
                api_base = self.llm_api_base.get().strip()
                model = self.llm_model.get().strip()
                mode_name = TEMPLATES[self.mode.get()]["name"]

                result = llm_enhance_description(api_key, api_base, model, user_input, mode_name)

                # 在主线程更新 UI
                self.root.after(0, lambda: self._apply_llm_result(result))

            except Exception as e:
                self.root.after(0, lambda: self.llm_status.config(
                    text=f"❌ {str(e)[:80]}", foreground="red"))

        threading.Thread(target=task, daemon=True).start()

    def _apply_llm_result(self, result):
        try:
            if result.get("project_name"):
                self.project_name.set(result["project_name"])
            if result.get("agent_a_role"):
                self.agent_a_role.set(result["agent_a_role"])
            if result.get("agent_b_role"):
                self.agent_b_role.set(result["agent_b_role"])
            self.llm_status.config(text="✅ 分析完成，配置已自动填充", foreground="green")
        except Exception as e:
            self.llm_status.config(text=f"⚠️ 结果解析异常: {e}", foreground="orange")

    def _preview(self):
        mode = self.mode.get()
        agents = self._get_agent_configs()
        agent_a, agent_b = agents[0], agents[1]
        a_name, b_name = agent_a['name'], agent_b['name']
        project_name = self.project_name.get() or "未命名项目"

        if mode == "custom":
            pipeline = self.custom_pipeline
        else:
            pipeline = None

        preview = f"""══════════════════════════════════════
  Bridge 生成预览
  模式: {TEMPLATES.get(mode, {}).get('name', 'Custom')}
  并发: {'⚡ 并行（分离文件+git仲裁）' if mode == 'parallel-team' else '🔗 串行（接力棒模式）'}
  项目: {project_name}
  Agent A: {agent_a['name']} ({agent_a['role']})
  Agent B: {agent_b['name']} ({agent_b['role']})
══════════════════════════════════════
"""

        if mode in ("parallel-team", "loop-engineering", "parallel-claim"):
            preview += f"""
📄 AGENTS.md: 项目元信息 + {len(TEMPLATES[mode]['pipeline'])} 道工序流水线
📄 agent-{a_name.lower()}.md: {a_name} 独立状态（只有 {a_name} 写）
📄 agent-{b_name.lower()}.md: {b_name} 独立状态（只有 {b_name} 写）
📄 board.md: 共享任务看板（git 仲裁并发）
📄 PARALLEL_GUIDE.md: 并行协作快速入门
📄 GIT_WORKTREE.md: worktree 物理隔离指南
📂 tasks/: 独立任务文件
📂 specs/: 只读规范文档"""

            if mode == "loop-engineering":
                preview += """
📄 loop-budget.md: 循环预算追踪（rounds/tokens/time 守卫）
📄 verify-template.md: 独立 Verify agent 裁决模板"""
            elif mode == "parallel-claim":
                preview += """
📄 spec-claim-guide.md: Spec 认领机制（无 worktree 并行）"""

            preview += """

-- 关键设计 --
🔒 互斥写: 各自的状态文件互不冲突
📋 共享写: board.md 和 tasks/ 通过 git 控制并发
🔄 节奏: 原子操作写完立即 commit→push，开始前先 pull
"""
        else:
            preview += f"""
📄 AGENTS.md:
{generate_agents_md(mode, agent_a, agent_b, project_name, self.lang)[:600]}...

📄 COLLAB.md:
{generate_collab_md(mode, agent_a, agent_b, pipeline, self.lang)[:600]}...

📄 README.md:
{generate_readme_md(mode, agent_a, agent_b, project_name, self.lang)[:600]}...

... 以及 specs/ 目录下的 tasks.md、review 模板、fix-orders 模板、acceptance.md 等
"""
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert("1.0", preview)
        self.status_label.config(text="预览已更新", foreground="blue")

    def _generate(self):
        target = self.target_dir.get().strip()
        if not target:
            messagebox.showerror("错误", "请先选择目标项目文件夹")
            return
        if not os.path.isdir(target):
            messagebox.showerror("错误", f"文件夹不存在: {target}")
            return

        mode = self.mode.get()
        agents = self._get_agent_configs()
        agent_a, agent_b = agents[0], agents[1]
        agent_c = agents[2] if len(agents) > 2 else None
        a_name, b_name = agent_a['name'], agent_b['name']
        project_name = self.project_name.get() or os.path.basename(target) or "未命名项目"

        if mode == "custom":
            pipeline = self.custom_pipeline
        else:
            pipeline = None

        # 检查覆盖
        check_files = ["AGENTS.md", "COLLAB.md", "README.md", "board.md",
                       f"agent-{a_name.lower()}.md", f"agent-{b_name.lower()}.md"]
        existing = [f for f in check_files if os.path.exists(os.path.join(target, f))]
        if existing:
            if not messagebox.askyesno("确认覆盖",
                                        f"以下文件已存在，将被覆盖：\n" +
                                        "\n".join(f"  • {f}" for f in existing) +
                                        "\n\n是否继续？"):
                return

        try:
            all_files = {}

            if mode in ("parallel-team", "loop-engineering", "parallel-claim"):
                # ── 并行模式：独立文件结构 ──
                all_files["AGENTS.md"] = generate_agents_md(mode, agent_a, agent_b, project_name, self.lang)
                all_files["README.md"] = generate_readme_md(mode, agent_a, agent_b, project_name, self.lang)
                all_files[f"agent-{a_name.lower()}.md"] = generate_agent_status_md(
                    a_name, agent_a['role'], b_name)
                all_files[f"agent-{b_name.lower()}.md"] = generate_agent_status_md(
                    b_name, agent_b['role'], a_name)
                all_files["board.md"] = generate_board_md(a_name, b_name, self.lang)
                all_files["GIT_WORKTREE.md"] = generate_git_worktree_guide()
                all_files["PARALLEL_GUIDE.md"] = generate_parallel_struct(a_name, b_name)

                # Agent C（如果启用）
                if agent_c:
                    c_name = agent_c['name']
                    all_files[f"agent-{c_name.lower()}.md"] = generate_agent_status_md(
                        c_name, agent_c['role'], f"{a_name} / {b_name}")

                tasks_dir = os.path.join(target, "tasks")
                specs_dir = os.path.join(target, "specs")
                os.makedirs(tasks_dir, exist_ok=True)
                os.makedirs(specs_dir, exist_ok=True)
                # 写入文件
                for name, content in all_files.items():
                    with open(os.path.join(target, name), "w", encoding="utf-8") as f:
                        f.write(content)
                # 创建示例任务文件
                with open(os.path.join(tasks_dir, "T001-example.md"), "w", encoding="utf-8") as f:
                    f.write(f"# T001: 示例任务\n\n- **状态**：📌待认领\n- **OWNER**：无\n"
                           f"- **模块**：src/example\n\n## 目标\n[待填写]\n\n## 验收标准\n- [ ] 待填写\n")

                # ── Loop-Engineering 额外文件 ──
                if mode == "loop-engineering":
                    all_files["loop-budget.md"] = generate_loop_budget_md(self.lang)
                    all_files["verify-template.md"] = generate_verify_template(self.lang)
                    with open(os.path.join(target, "loop-budget.md"), "w", encoding="utf-8") as f:
                        f.write(all_files["loop-budget.md"])
                    with open(os.path.join(target, "verify-template.md"), "w", encoding="utf-8") as f:
                        f.write(all_files["verify-template.md"])

                # ── Parallel-Claim 额外文件 ──
                if mode == "parallel-claim":
                    all_files["spec-claim-guide.md"] = generate_spec_claim_template(self.lang)
                    with open(os.path.join(target, "spec-claim-guide.md"), "w", encoding="utf-8") as f:
                        f.write(all_files["spec-claim-guide.md"])
                    # 创建示例 spec 文件
                    with open(os.path.join(specs_dir, "spec-example.md"), "w", encoding="utf-8") as f:
                        f.write("# Spec: 示例功能\nSpec claimed by agent: <unclaimed>\n\n## 目标\n[待填写]\n\n## 验收标准\n- [ ] 待填写\n")
            else:
                # ── 串行模式：原逻辑 ──
                all_files["AGENTS.md"] = generate_agents_md(mode, agent_a, agent_b, project_name, self.lang)
                all_files["COLLAB.md"] = generate_collab_md(mode, agent_a, agent_b, pipeline, self.lang)
                all_files["README.md"] = generate_readme_md(mode, agent_a, agent_b, project_name, self.lang)

                specs_active = os.path.join(target, "specs", "active")
                specs_review = os.path.join(specs_active, "review")
                specs_fix = os.path.join(specs_active, "fix-orders")
                specs_archive = os.path.join(target, "specs", "archive")

                spec_files = {
                    os.path.join(specs_active, "tasks.md"): generate_tasks_md(mode, self.lang),
                    os.path.join(specs_active, "acceptance.md"): generate_acceptance_md(self.lang),
                    os.path.join(specs_active, "escalation.md"):
                        T("tmpl.escalation_file", self.lang),
                    os.path.join(specs_review, "TEMPLATE.md"): generate_review_template(self.lang),
                    os.path.join(specs_fix, "TEMPLATE.md"): generate_fix_template(self.lang),
                }

                for d in [specs_active, specs_review, specs_fix, specs_archive]:
                    os.makedirs(d, exist_ok=True)

                for path, content in spec_files.items():
                    all_files[os.path.relpath(path, target)] = content
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)

                # 写入根文件
                for name in ["AGENTS.md", "COLLAB.md", "README.md"]:
                    with open(os.path.join(target, name), "w", encoding="utf-8") as f:
                        f.write(all_files[name])

            # .gitignore
            gitignore_path = os.path.join(target, ".gitignore")
            if not os.path.exists(gitignore_path):
                with open(gitignore_path, "w", encoding="utf-8") as f:
                    f.write(T("tmpl.gitignore_content", self.lang))

            # 生成报告
            report = f"已在 {target} 中生成以下文件：\n\n"
            report += "\n".join(f"  ✅ {p}" for p in sorted(all_files.keys()))

            self.preview_text.delete("1.0", tk.END)
            self.preview_text.insert("1.0", report)
            self.status_label.config(text=f"✅ 已生成 {len(all_files)} 个文件", foreground="green")
            messagebox.showinfo("生成完成", report)

        except Exception as e:
            messagebox.showerror("生成失败", str(e))
            self.status_label.config(text=f"❌ {str(e)[:60]}", foreground="red")


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    root = tk.Tk()
    app = BridgeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
