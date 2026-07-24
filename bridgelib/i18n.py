"""Bridge i18n module with translation functions and dictionaries."""

# Internationalization (i18n)
# ═══════════════════════════════════════════════════════════════

LANG = "zh"  # Default language; the GUI can switch it.

def T(key, lang=None, **fmt):
    """Get translated text using a dotted key such as 'mode.architect.name'."""
    if lang is None:
        lang = LANG
    parts = key.split(".")
    d = _STR
    for p in parts:
        if isinstance(d, dict) and p in d:
            d = d[p]
        else:
            return key  # Fall back to the key itself.
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

# Complete translation dictionary; add new text here.
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

    # Mode metadata
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
        "loop-engineering": {
            "name": {"zh": "Loop-Engineering", "en": "Loop-Engineering"},
            "desc": {"zh": "借鉴 loop-engineering + loop.js 设计。Goal→Execute→Verify→Settle。独立 Verify agent，预算守卫。",
                     "en": "Inspired by loop-engineering + loop.js. Goal→Execute→Verify→Settle. Independent Verify agent with budget guards."},
        },
        "parallel-claim": {
            "name": {"zh": "Parallel-Claim", "en": "Parallel-Claim"},
            "desc": {"zh": "借鉴 LoopGate 的 Claim 认领机制。多 Agent 通过 spec claim 行无冲突并行，不依赖 worktree。",
                     "en": "Inspired by LoopGate's claim mechanism. Multi-agent parallel via spec claim lines, no worktree needed."},
        },
        "custom": {
            "name": {"zh": "自定义", "en": "Custom"},
            "desc": {"zh": "自定义流水线。自由编辑阶段和 Agent 分配。",
                     "en": "Custom pipeline. Edit stages and agent assignments freely."},
        },
    },

    # Pipeline stage names
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

    # Template content
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

1. **SQLite 是唯一真相源**：`.bridge/runtime/bridge.db` 是权威状态，所有变更通过 Bridge 状态机
2. **失败不跳级**：任何阶段不通过必须回到实现层重做
3. **先读后写**：每个 agent 启动时第一件事：读 AGENTS.md → 任务包 → CONTEXT.md
4. **无证据不签字**：验收必须基于可验证证据""",
            "en": """## Key Rules

1. **SQLite is the single source of truth**: `.bridge/runtime/bridge.db` holds authoritative state
2. **Failures don't skip stages**: Any stage failure must return to implementation
3. **Read before write**: Every agent reads AGENTS.md → task package → CONTEXT.md on startup
4. **No evidence, no sign-off**: Acceptance requires verifiable evidence"""
        },
        "agents_rules_parallel": {
            "zh": """## 关键规则

1. **隔离 worktree**：每个 Agent 在独立 worktree 中工作，互不干扰
2. **串行合并队列**：所有候选变更通过 Bridge 合并队列串行集成
3. **先读后写**：每个 agent 启动时：读 AGENTS.md → 自己的任务包 → board 视图
4. **不跨 worktree 写文件**：隔离 worktree 是防止并发冲突的基础
5. **写完就 commit**：原子操作完成后立即提交，不堆积改动""",
            "en": """## Key Rules

1. **Isolated worktrees**: Each agent works in an independent worktree
2. **Serial merge queue**: All candidate changes integrate through Bridge's merge queue
3. **Read before write**: Every agent reads: AGENTS.md → own task package → board view on startup
4. **Never write across worktrees**: Worktree isolation prevents concurrency conflicts
5. **Commit after each change**: Commit immediately after each atomic operation, don't batch"""
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
            "zh": "# OS\n.DS_Store\nThumbs.db\n\n# IDE\n.vscode/\n.idea/\n\n# Dependencies\nnode_modules/\n__pycache__/\n*.pyc\n\n# Build\ndist/\nbuild/\ntarget/\n\n# Env\n.env\n.env.local\n\n# Bridge runtime\n.bridge/runtime/\n.bridge/local.yaml\n",
            "en": "# OS\n.DS_Store\nThumbs.db\n\n# IDE\n.vscode/\n.idea/\n\n# Dependencies\nnode_modules/\n__pycache__/\n*.pyc\n\n# Build\ndist/\nbuild/\ntarget/\n\n# Env\n.env\n.env.local\n\n# Bridge runtime\n.bridge/runtime/\n.bridge/local.yaml\n"
        },
    },
}

