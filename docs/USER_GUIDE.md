# Bridge 用户手册

Bridge 是一个本地多 Agent 协调台。你仍然手动打开 Codex、Reasonix 或其他
Agent；Bridge 负责安全分工、隔离工作区、验证、独立审查和串行合并。
Bridge 默认不会调用模型，也不会消耗 API Token。

## 启动

要求：Python 3.11+、Git 2.40+，以及带 tkinter 的 Python。

首次安装：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\bridge-gui.exe
```

以上命令需要在下载或克隆后的项目根目录执行。以后再次启动：

```powershell
.\.venv\Scripts\bridge-gui.exe
```

## 最短使用流程

整个主流程只有四步。任务状态、Agent ID、lease ID、attempt ID、merge ID 和
commit hash 都由 Bridge 内部管理。

### 1. 连接项目

在“项目”页选择目标 Git 项目，填写项目名，然后点击“连接项目”。目标项目必须：

- 已经是 Git 仓库；
- 至少有一次 commit；
- 工作区尽量保持干净。

默认推荐 `hybrid + balanced`。数据库自动保存在 `.bridge/bridge.db`，通常不需要
修改高级设置。

### 2. 保存 Agent

在“Agent”页填写两个角色：

- 规划与审查 Agent，例如 Codex/GPT；
- 执行 Agent，例如 Reasonix。

点击“保存 Agent 配置”。Bridge 自动登记内部 ID 和权限，不需要复制 ID。
执行者与审查者必须是不同 Agent。需要并行执行时可以添加第三个执行 Agent。

### 3. 创建并执行任务

进入“任务”页，点击“新建任务”，填写：

- 标题与目标；
- 允许修改的路径，每行一个；
- 验收标准，每行一个；
- 自动检查，例如 `unit-tests`。

选择任务后，右侧始终只显示当前合法的一个主操作。通常依次是：

```text
准备任务 → 分配并开始 → 检测 Agent 提交 → 运行验证
→ 记录审查结果 → 安全合并
```

点击“分配并开始”后，Bridge 自动完成 lease、attempt、Git worktree 和任务包创建，
并把执行提示词放入剪贴板。界面会显示两个辅助按钮：

- “打开工作区”：打开执行 Agent 应使用的 worktree；
- “复制提示词”：再次复制完整执行说明。

把提示词粘贴给执行 Agent。Agent 只需在指定 worktree 中修改代码、运行测试并创建
commit，然后更新 Bridge 生成的任务包中的 `RECEIPT.md` 和 `ARTIFACTS.json`。
任务包路径会明确写在提示词中，不需要手工寻找。

Agent 完成后点击“检测 Agent 提交”。Bridge 会自动读取 commit 和产物文件，不需要
填写 commit hash，也不需要选择 `ARTIFACTS.json`。

### 4. 审查并合并

点击“运行验证”。Bridge 会在任务 worktree 中运行检查，而不是在主项目旧代码上运行。
验证通过后，审查提示词会自动复制到剪贴板。

把提示词交给独立审查 Agent。审查完成后点击“记录审查结果”：

- Approved：任务进入可合并状态；
- Revision Required：Bridge 保留记录，并提供“开始修改”操作创建新 attempt。

点击“安全合并”并确认一次。Bridge 自动完成入队、integration worktree、串行合并、
任务完成、attempt/lease 关闭和干净 worktree 清理。中途失败时保留可恢复状态，界面会
显示具体原因；再次点击当前建议操作即可继续。

## 页面说明

- “项目”：连接 Git 项目和配置推进/安全策略。
- “Agent”：维护执行者与审查者的显示名称、职责和模型档案。
- “任务”：日常工作的唯一主页面，包含任务列表、进度、下一步和活动记录。
- “工具”：可选的 Markdown 协作文档、LLM 辅助和自定义流水线，不影响核心流程。

## 安全规则

- `strict`：写操作尽量要求确认。
- `balanced`：低风险步骤自动，高风险步骤确认，推荐。
- `expert`：减少确认，但不能突破硬安全底线。
- force push、删除默认分支、越权路径和密钥泄露等操作始终禁止。
- 验证和审查绑定当前 attempt，旧 attempt 的结果不能复用。
- 合并使用仓库外的 integration worktree，不执行 stash 或强制切换用户分支。
- 脏 worktree 清理必须明确确认，不要手工删除 Bridge 正在管理的目录。

## 验证命令

内置检查包括 `unit-tests`、`lint`、`typecheck` 和 `build`。任务默认使用
`unit-tests`；可在创建任务时修改。自定义命令必须先安全登记，任务回执不能替换已
登记的 executable 或参数。

## 故障恢复

重新连接同一项目后，Bridge 会从 SQLite 恢复任务、租约、attempt、worktree、审查和
合并队列。若操作中断，选择任务后按右侧建议操作继续。不要先手工删除 worktree 或
修改 Bridge 管理的分支，否则会破坏恢复证据。

## 开发验证

```powershell
python -m compileall -q bridgelib tests
python -m pytest -q
git diff --check
```

部分测试会创建真实临时 Git 仓库，因此系统临时目录必须可写且 `git` 命令可用。

## 卸载

```powershell
.\.venv\Scripts\python.exe -m pip uninstall bridge-agent-coordinator
```

卸载 Python 包不会删除目标项目中的 `.bridge/` 数据库，也不会删除项目同级的
`.bridge-task-worktrees/` 或 `.bridge-integration-worktrees/`。确认所有任务已完成、
worktree 中没有未提交内容并做好备份后，再手工清理这些运行数据。
