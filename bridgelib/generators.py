"""Bridge file generation engine."""
from datetime import datetime
from bridgelib.i18n import T
from bridgelib.templates import TEMPLATES

# ═══════════════════════════════════════════════════════════════

def _stage_display(stage, lang):
    """Get a pipeline stage's display name with a custom-stage fallback."""
    key = f"stage.{stage['id']}"
    name = T(key, lang)
    if name == key:
        return stage.get('name', stage['id'])
    return name


def generate_agents_md(mode, agent_a, agent_b, project_name="未命名项目", lang="zh", agent_c=None):
    """Generate AGENTS.md content."""
    tmpl = TEMPLATES[mode]
    a_name = agent_a.get('name', 'Agent A')
    a_role = agent_a.get('role', '')
    a_model = agent_a.get('model', '')
    b_name = agent_b.get('name', 'Agent B')
    b_role = agent_b.get('role', '')
    b_model = agent_b.get('model', '')

    a_duties = "、".join([_stage_display(s, lang) for s in tmpl["pipeline"] if s['agent'] in ('Agent A', 'Both')])
    b_duties = "、".join([_stage_display(s, lang) for s in tmpl["pipeline"] if s['agent'] in ('Agent B', 'Both')])

    stages_str = ""
    for i, stage in enumerate(tmpl["pipeline"]):
        stages_str += "│  " + str(i+1) + ". " + _stage_display(stage, lang) + " (" + stage['agent'] + ")\n"

    # Select serial or parallel rules based on the mode.
    is_parallel = mode in ("parallel-team", "loop-engineering", "parallel-claim")
    rules_key = "tmpl.agents_rules_parallel" if is_parallel else "tmpl.agents_rules"
    rules = T(rules_key, lang).replace("{name}", a_name.lower())

    result = T("tmpl.agents_header", lang) + "\n\n" + \
             T("tmpl.agents_project_info", lang, name=project_name,
               time=datetime.now().strftime('%Y-%m-%d %H:%M'),
               mode=T(f"mode.{mode}.name", lang)) + "\n\n" + \
             T("tmpl.agents_tech_stack", lang) + "\n\n" + \
             T("tmpl.agents_roles", lang, a_name=a_name, a_role=a_role, a_model=a_model,
               a_duties=a_duties, b_name=b_name, b_role=b_role, b_model=b_model, b_duties=b_duties)

    # Agent C, if enabled.
    if agent_c:
        c_name = agent_c.get('name', 'Agent C')
        c_role = agent_c.get('role', '')
        c_model = agent_c.get('model', '')
        c_duties = "、".join([_stage_display(s, lang) for s in tmpl["pipeline"] if s['agent'] in ('Agent C', 'Both')])
        if lang == "zh":
            result += f"\n\n### Agent C — {c_name}\n- **角色**：{c_role}\n- **模型**：{c_model}\n- **职责**：{c_duties}"
        else:
            result += f"\n\n### Agent C — {c_name}\n- **Role**: {c_role}\n- **Model**: {c_model}\n- **Duties**: {c_duties}"

    result += "\n\n" + \
              T("tmpl.agents_pipeline", lang, n=len(tmpl["pipeline"]), stages=stages_str.strip()) + "\n\n" + \
              rules
    return result


def generate_collab_md(mode, agent_a, agent_b, pipeline_custom=None, lang="zh"):
    """Generate COLLAB.md content."""
    tmpl = TEMPLATES[mode]
    pipeline = pipeline_custom if pipeline_custom else tmpl["pipeline"]
    a_name = agent_a.get('name', 'Agent A')
    flow = " → ".join([_stage_display(s, lang) for s in pipeline])
    first = _stage_display(pipeline[0], lang)
    return T("tmpl.collab_header", lang) + "\n\n---\n\n" + \
           T("tmpl.collab_current_stage", lang, flow=flow, first_stage=first, a_name=a_name) + "\n\n---\n\n" + \
           T("tmpl.collab_task_table_header", lang, a_name=a_name) + "\n\n" + \
           T("tmpl.collab_sections", lang, a_name=a_name)


def generate_tasks_md(mode, lang="zh"):
    """Generate the tasks.md template."""
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
    """Generate README.md."""
    tmpl = TEMPLATES[mode]
    a_name = agent_a.get('name', 'Agent A')
    a_role = agent_a.get('role', '')
    b_name = agent_b.get('name', 'Agent B')
    b_role = agent_b.get('role', '')

    a_short = "、".join([_stage_display(s, lang) for s in tmpl["pipeline"] if s['agent'] in ('Agent A', 'Both')][:3])
    b_short = "、".join([_stage_display(s, lang) for s in tmpl["pipeline"] if s['agent'] in ('Agent B', 'Both')][:3])

    pipe_flow = ""
    for i, s in enumerate(tmpl["pipeline"]):
        arrow = "" if i == len(tmpl["pipeline"]) - 1 else " ──→"
        pipe_flow += "  " + _stage_display(s, lang) + " (" + s['agent'] + ")" + arrow + "\n"

    return T("tmpl.readme_title", lang, name=project_name, mode=T(f"mode.{mode}.name", lang),
             desc=T(f"mode.{mode}.desc", lang)) + "\n\n" + \
           T("tmpl.readme_arch", lang, a_name=a_name, a_role=a_role, b_name=b_name, b_role=b_role,
             a_duties=a_short, b_duties=b_short, pipeline_flow=pipe_flow.strip(),
             time=datetime.now().strftime('%Y-%m-%d %H:%M'))


# ═══════════════════════════════════════════════════════════════
# Generation functions specific to Parallel-Team mode
# ═══════════════════════════════════════════════════════════════

def generate_agent_status_md(agent_name, agent_role, counterpart_name, lang="zh"):
    """Generate an agent's independent status file, central to parallel mode."""
    if lang == "zh":
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
    else:
        return f"""# agent-{agent_name.lower()}.md — {agent_name} Status File

> ⚠️ **Only {agent_name} writes this file; {counterpart_name} reads only**. This is the key design for avoiding concurrency conflicts in parallel mode.
> After editing, immediately git commit. Before starting, git pull.

---

## Current Status

🔄 **In Progress** — [current stage]

## My Claimed Tasks

| Task ID | Name | Status | Latest commit | Notes |
|---------|------|--------|---------------|-------|
| - | Awaiting claim | - | - | - |

## My Completed Milestones

- [ ] None

## What I Need from {counterpart_name}

<!-- Write here; the other agent will see it after their next git pull -->
_None for now_

## Blockers I'm Facing

_None for now_

## Handoff

> **{agent_name} → {counterpart_name}**: Awaiting joint planning phase completion.
"""


def generate_board_md(agent_a_name, agent_b_name, lang="zh", agent_c_name=None):
    """Generate the shared task board, central to parallel mode."""
    table = f"""| 任务ID | 任务名称 | 状态 | OWNER | 复杂度 | 涉及模块 | 验收标准 |
|--------|---------|------|-------|--------|---------|---------|
| - | 等待规划 | - | - | - | - | - |""" if lang == "zh" else f"""| Task ID | Name | Status | OWNER | Tier | Module | Acceptance |
|--------|------|--------|-------|------|--------|------------|
| - | Awaiting plan | - | - | - | - | - |"""

    # Exclusive-write rules, including Agent C.
    c_mutex_zh = f"\n| 🔒 {'互斥写' if lang == 'zh' else 'Mutex Write'} | `agent-{agent_c_name.lower()}.md` {'只有' if lang == 'zh' else 'only'} {agent_c_name} {'写' if lang == 'zh' else 'writes'} |" if agent_c_name else ""
    c_mutex_en = f"\n| 🔒 {'互斥写' if lang == 'zh' else 'Mutex Write'} | `agent-{agent_c_name.lower()}.md` {'只有' if lang == 'zh' else 'only'} {agent_c_name} {'写' if lang == 'zh' else 'writes'} |" if agent_c_name else ""
    c_mutex = c_mutex_zh if lang == "zh" else c_mutex_en

    rules = f"""## {'成本路由规则' if lang == 'zh' else 'Cost Routing Rules'}

| {'复杂度' if lang == 'zh' else 'Tier'} | {'应由谁做' if lang == 'zh' else 'Assigned To'} | {'原因' if lang == 'zh' else 'Rationale'} |
|--------|---------|------|
| 🟢 LOW | {agent_b_name}（{'低成本 Agent' if lang == 'zh' else 'Low-cost Agent'}） | {'简单代码不消耗贵模型 token' if lang == 'zh' else 'Simple code, saves expensive model tokens'} |
| 🟡 MID | {agent_b_name} {'或' if lang == 'zh' else 'or'} {agent_a_name}{' 或 ' + agent_c_name if agent_c_name and lang == 'zh' else ' or ' + agent_c_name if agent_c_name else ''} | {'视任务紧要程度' if lang == 'zh' else 'Depends on urgency'} |
| 🔴 HIGH | {agent_a_name}（{'高能力 Agent' if lang == 'zh' else 'High-capability Agent'}） | {'需要深度推理，便宜模型可能做不对' if lang == 'zh' else 'Needs deep reasoning; cheap models may fail'} |

> {'此路由规则不绑定模型名称。根据你实际使用的模型调整。' if lang == 'zh' else 'These rules are model-agnostic. Adjust based on your actual agents.'}

## {'并行规则速查' if lang == 'zh' else 'Parallel Rules Quick Reference'}

| {'规则' if lang == 'zh' else 'Rule'} | {'说明' if lang == 'zh' else 'Description'} |
|------|------|
| 🔒 {'互斥写' if lang == 'zh' else 'Mutex Write'} | `agent-{agent_a_name.lower()}.md` {'只有' if lang == 'zh' else 'only'} {agent_a_name} {'写' if lang == 'zh' else 'writes'}{'；' if lang == 'zh' else '; '}`agent-{agent_b_name.lower()}.md` {'只有' if lang == 'zh' else 'only'} {agent_b_name} {'写' if lang == 'zh' else 'writes'}{c_mutex} |
| 📋 {'共享写' if lang == 'zh' else 'Shared Write'} | `board.md` {'和' if lang == 'zh' else 'and'} `tasks/*.md` {'都可以写，通过 Bridge 合并队列串行集成' if lang == 'zh' else 'shared; serial merge queue arbitration'} |
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


def generate_git_worktree_guide(lang="zh"):
    """Generate the Git worktree isolation guide."""
    if lang == "zh":
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
    else:
        return """# GIT_WORKTREE.md — Optional: Physical Isolation with Git Worktree

> If you encounter frequent git conflicts, you can use git worktree to let two agents
> work in physically isolated workspaces, completely avoiding file-level concurrency issues.

## How It Works

```
Main repo (main branch)
    │
    ├── worktree-gpt/       ← GPT's workspace (separate directory)
    │   └── works on feature/gpt branch
    │
    └── worktree-reasonix/  ← Reasonix's workspace (separate directory)
        └── works on feature/reasonix branch
```

Two agents commit independently in separate directories, no interference.
Merge is done by a human or Agent A on the main branch via git merge.

## Practical Steps

```bash
# 1. Create worktrees (one-time setup)
cd /path/to/project
git worktree add ../worktree-gpt feature/gpt
git worktree add ../worktree-reasonix feature/reasonix

# 2. GPT works under worktree-gpt/
cd ../worktree-gpt
# ... code, commit, push ...

# 3. Reasonix works under worktree-reasonix/
cd ../worktree-reasonix
# ... code, commit, push ...

# 4. Merge (by human)
cd /path/to/project   # back to main repo
git merge feature/gpt
git merge feature/reasonix
# resolve conflicts if any
git push

# 5. Cleanup
git worktree remove ../worktree-gpt
git worktree remove ../worktree-reasonix
git branch -d feature/gpt feature/reasonix
```

## When to Use Worktree?

| Scenario | Recommendation |
|----------|---------------|
| Non-overlapping tasks (different files) | Basic: separate status files + git |
| May edit same file | Use worktree isolation |
| Frequent conflicts | Must use worktree |
| Small project / rapid prototype | Serial mode (Architect-Engineer) is simpler |
"""


def generate_parallel_struct(agent_a_name, agent_b_name, agent_c_name=None, lang="zh"):
    """Generate the project structure description for parallel mode."""
    c_section_zh = ""
    c_section_en = ""
    c_flow_a_zh = ""
    c_flow_a_en = ""
    c_flow_c_zh = ""
    c_flow_c_en = ""
    if agent_c_name:
        c_section_zh = f"├── agent-{agent_c_name.lower()}.md        ← [{agent_c_name} 专写] {agent_c_name}的状态\n"
        c_section_en = f"├── agent-{agent_c_name.lower()}.md        ← [{agent_c_name} exclusive] {agent_c_name} status\n"
        c_flow_a_zh = f"\n6. 查看 agent-{agent_c_name.lower()}.md 的「我需要对方做的事」区"
        c_flow_a_en = f"\n6. Check agent-{agent_c_name.lower()}.md's \"What I Need from You\" section"
        c_flow_c_zh = f"""
## {agent_c_name} 启动流程

1. `git pull`
2. 读 `AGENTS.md` → `agent-{agent_c_name.lower()}.md` → `board.md`
3. 查看 agent-{agent_a_name.lower()}.md 和 agent-{agent_b_name.lower()}.md 的「我需要对方做的事」区
4. 认领任务 → 更新 board.md → commit → push
5. 写代码 → 更新自己的状态文件 → commit → push
"""
        c_flow_c_en = f"""
## {agent_c_name} Startup Flow

1. `git pull`
2. Read `AGENTS.md` → `agent-{agent_c_name.lower()}.md` → `board.md`
3. Check agent-{agent_a_name.lower()}.md and agent-{agent_b_name.lower()}.md's "What I Need from You" sections
4. Claim tasks → update board.md → commit → push
5. Write code → update own status file → commit → push
"""

    if lang == "zh":
        return f"""# 并行协作快速入门

## 文件分工

```
项目根目录/
├── AGENTS.md                  ← [只读] 项目元信息
├── agent-{agent_a_name.lower()}.md        ← [{agent_a_name} 专写] {agent_a_name}的状态
├── agent-{agent_b_name.lower()}.md     ← [{agent_b_name} 专写] {agent_b_name}的状态
{c_section_zh}├── board.md                   ← [Bridge 视图] 任务看板（从 SQLite 生成）
├── tasks/
│   ├── T001-xxx.md           ← [任务包] 任务定义
│   └── T002-yyy.md
├── specs/
│   ├── overview.md           ← [只读] 项目总览
│   └── architecture.md       ← [只读] 架构设计
├── GIT_WORKTREE.md            ← [参考] worktree 隔离指南
└── src/                       ← [隔离 worktree] 实际代码
```

## {agent_a_name} 启动流程

1. `git pull`
2. 读 `AGENTS.md` → `agent-{agent_a_name.lower()}.md` → `board.md`
3. 认领任务 → 更新 board.md → `git commit` → `git push`
4. 写代码 → 更新自己的 agent-{agent_a_name.lower()}.md → commit → push
5. 需要 {agent_b_name} 做的事写在 agent-{agent_a_name.lower()}.md 的「我需要对方做的事」区{c_flow_a_zh}

## {agent_b_name} 启动流程

1. `git pull`
2. 读 `AGENTS.md` → `agent-{agent_b_name.lower()}.md` → `board.md`
3. 查看 agent-{agent_a_name.lower()}.md 的「我需要对方做的事」区
4. 认领任务 → 更新 board.md → commit → push
5. 写代码 → 更新自己的状态文件 → commit → push
{c_flow_c_zh}
## 关键原则

| 原则 | 说明 |
|------|------|
| 写完就 commit | 不要攒一堆改动再提交 |
| 开始前先 pull | 看到最新状态再动手 |
| 不写对方的文件 | 互斥写是防止冲突的基础 |
| 冲突不慌 | git rebase 后手动解决，在 board.md 记录 |
"""
    else:
        return f"""# Parallel Collaboration Quick Start

## File Division

```
Project root/
├── AGENTS.md                  ← [Read-Only] Project meta info
├── agent-{agent_a_name.lower()}.md        ← [{agent_a_name} exclusive] {agent_a_name} status
├── agent-{agent_b_name.lower()}.md     ← [{agent_b_name} exclusive] {agent_b_name} status
{c_section_en}├── board.md                   ← [Bridge View] Task board (from SQLite)
├── tasks/
│   ├── T001-xxx.md           ← [Task Package] Task definitions
│   └── T002-yyy.md
├── specs/
│   ├── overview.md           ← [Read-Only] Project overview
│   └── architecture.md       ← [Read-Only] Architecture design
├── GIT_WORKTREE.md            ← [Reference] Worktree isolation guide
└── src/                       ← [Isolated Worktree] Actual code
```

## {agent_a_name} Startup Flow

1. `git pull`
2. Read `AGENTS.md` → `agent-{agent_a_name.lower()}.md` → `board.md`
3. Claim tasks → update board.md → `git commit` → `git push`
4. Write code → update own agent-{agent_a_name.lower()}.md → commit → push
5. Write what you need from {agent_b_name} in your status file's "What I Need from You" section{c_flow_a_en}

## {agent_b_name} Startup Flow

1. `git pull`
2. Read `AGENTS.md` → `agent-{agent_b_name.lower()}.md` → `board.md`
3. Check agent-{agent_a_name.lower()}.md's "What I Need from You" section
4. Claim tasks → update board.md → commit → push
5. Write code → update own status file → commit → push
{c_flow_c_en}
## Key Principles

| Principle | Description |
|-----------|-------------|
| Commit after every change | Don't batch multiple changes before committing |
| Pull before starting | See the latest state before you act |
| Never write another agent's file | Exclusive write is the foundation of conflict prevention |
| Don't panic on conflicts | Resolve manually after git rebase, record in board.md |
"""


# ═══════════════════════════════════════════════════════════════
# Functions specific to Loop-Engineering and Parallel-Claim
# ═══════════════════════════════════════════════════════════════

def generate_loop_budget_md(lang="zh"):
    """Generate loop-budget.md to keep the supervisory loop under control."""
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
    """Generate verify-template.md for independent Verify agent verdicts."""
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
    """Generate the spec claim template based on LoopGate's claim mechanism."""
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


