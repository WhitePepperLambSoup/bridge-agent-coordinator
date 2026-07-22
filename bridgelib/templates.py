"""Bridge 协作模式模板定义。"""

TEMPLATES = TEMPLATES = {
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
        "agent_b": {"name": "Reasonix", "role": "工程师 / 执行者", "model": "deepseek-v4-flash"},
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
        "agent_b": {"name": "Reasonix", "role": "任务实现者", "model": "deepseek-v4-flash"},
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
        "agent_b": {"name": "Reasonix", "role": "执行者", "model": "deepseek-v4-flash"},
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
        "agent_b": {"name": "Reasonix", "role": "主力工程师", "model": "deepseek-v4-flash"},
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
        "agent_b": {"name": "Reasonix", "role": "Execute 执行者", "model": "deepseek-v4-flash"},
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

    "custom": {
        "name": "Custom",
        "description": "User-defined pipeline with custom stages and agent assignments.",
        "icon": "🛠️",
        "pipeline": [],
        "agent_a": {"name": "Agent A", "role": "Configurable", "model": ""},
        "agent_b": {"name": "Agent B", "role": "Configurable", "model": ""},
    },
}


