# 09. 数据库、事件、配置与错误模型

## 1. 设计目标

本地协调器必须做到：重启后可恢复、操作可追溯、状态不会因 Markdown 文件损坏而丢失、多个界面或文件监听事件不会重复推进任务。

SQLite 是运行时事实源；Markdown 和 JSON 是 Agent 交换协议与人类可读视图，不承担主数据库职责。

## 2. 运行目录

建议结构：

```text
.bridge/
├── project.yaml
├── agents/
│   ├── codex.yaml
│   └── reasonix.yaml
├── local.yaml                 # 不纳入版本控制
└── runtime/                   # 不纳入版本控制
    ├── bridge.db
    ├── backups/
    ├── logs/
    └── locks/
```

`.bridge/runtime/`、`.bridge/local.yaml` 和所有潜在凭据文件必须加入 `.gitignore`。V1 不设计独立 secrets 文件；需要凭据的未来适配器应从操作系统安全存储或进程环境中读取，但不得记录其值。

## 3. 核心数据实体

### 3.1 `projects`

保存项目身份与全局策略：

- `id`, `name`, `root_path`, `default_branch`
- `workspace_mode`, `progression_policy`, `confirmation_policy`
- `language`, `created_at`, `updated_at`

### 3.2 `agent_profiles`

保存可动态增加的 Agent，而不是写死“两个模型”：

- `id`, `project_id`, `display_name`, `provider`, `model`
- `capability_tier`, `cost_tier`
- `roles_json`, `strengths_json`, `permissions_json`, `limits_json`
- `workspace_settings_json`, `enabled`, `created_at`, `updated_at`

### 3.3 `goals`

- `id`, `project_id`, `title`, `description`, `status`
- `planner_agent_id`, `approved_plan_version`
- `created_at`, `updated_at`

### 3.4 `tasks`

- 内部稳定 ID 与人类可读编号（如 `TASK-014`）
- `goal_id`, `title`, `goal`, `state`, `risk`, `complexity`, `cost_tier`
- `owner_agent_id`, `reviewer_agent_id`
- `progression_policy`, `approval_gate`
- `allowed_paths_json`, `forbidden_paths_json`
- `acceptance_criteria_json`, `required_checks_json`
- `token_budget`, `time_budget_seconds`, `retry_budget`
- `created_at`, `updated_at`, `version`

`version` 用于乐观并发控制。更新必须携带读到的版本，版本不一致时拒绝覆盖并要求重新加载。

### 3.5 关联与运行表

- `task_dependencies`：任务依赖与依赖类型。
- `attempts`：每次派发的独立尝试、上下文摘要和最终结果。
- `leases`：任务所有权租约、心跳、到期时间和撤销原因。
- `workspaces`：分支、worktree、基线提交、路径与清理状态。
- `receipts`：回执路径、内容哈希、解析版本与导入结果；内容哈希用于去重。
- `validations`：命令、退出码、超时、输出摘要和证据路径。
- `reviews`：审查结论、问题级别、修复任务和审查 Agent。
- `merge_queue`：串行合并队列、顺序、目标分支与结果。
- `cost_records`：输入/输出 token 估算或供应商实际值、金额、缓存命中和归属任务。
- `events`：不可变审计事件。

## 4. 事件模型

最低事件集合：

- `ProjectInitialized`, `ProjectPolicyChanged`
- `GoalCreated`, `PlanImported`, `PlanApproved`
- `TaskCreated`, `TaskAssigned`, `TaskStateChanged`, `TaskCancelled`
- `AttemptStarted`, `AttemptCompleted`, `AttemptFailed`
- `LeaseAcquired`, `LeaseRenewed`, `LeaseExpired`, `LeaseRevoked`
- `ReceiptDetected`, `ReceiptImported`, `ReceiptRejected`
- `ValidationStarted`, `ValidationPassed`, `ValidationFailed`
- `ReviewRequested`, `ReviewApproved`, `ReviewChangesRequested`
- `MergeQueued`, `MergeStarted`, `MergeCompleted`, `MergeConflict`
- `RollbackRequested`, `RollbackCompleted`, `RollbackFailed`
- `WorkspaceCleanupRequested`, `WorkspaceCleanupCompleted`
- `BudgetThresholdReached`, `AgentEscalated`

每个事件至少包含：`event_id`, `project_id`, `task_id`, `event_type`, `actor_type`, `actor_id`, `payload_json`, `created_at_utc`, `correlation_id`。

任务状态变更和事件写入必须处于同一个 SQLite 事务中，避免“状态已经改变但审计记录不存在”。

## 5. 外部 Git 操作一致性

Git 操作无法与 SQLite 形成同一事务，因此使用操作日志模式：

1. 写入 `OperationPrepared`，记录目标、预期基线和幂等键。
2. 执行 Git 操作。
3. 成功写入 `OperationCompleted`；失败写入 `OperationFailed`。
4. 重启时扫描未完成操作，检查真实 Git 状态，再决定补记完成、重试或进入人工恢复。

不得在不知道 Git 实际结果时盲目重复 cherry-pick、revert 或 worktree 删除。

## 6. 配置分层

### 6.1 `.bridge/project.yaml`

团队共享并纳入版本控制，包含项目策略、默认分支、验证规则、安全模式和协议版本。

### 6.2 `.bridge/agents/*.yaml`

团队共享并纳入版本控制，描述 Agent 能力、成本、角色和权限。不得包含 API key、用户目录绝对路径或设备专有信息。

### 6.3 `.bridge/local.yaml`

仅本机使用且不纳入版本控制，包含 worktree 根目录、编辑器命令、桌面通知和本机性能限制。

运行时覆盖顺序为：内置安全默认值 → `project.yaml` → Agent 配置 → `local.yaml` 中允许覆盖的本机项。`local.yaml` 不得降低项目规定的硬安全底线。

## 7. 版本与迁移

数据库、配置和交换协议分别维护版本号，不得共用一个模糊的“应用版本”。

升级流程：

1. 检查当前版本与目标版本。
2. 在 `runtime/backups/` 创建数据库备份。
3. 在事务中迁移并执行完整性检查。
4. 成功后记录迁移事件；失败则回滚并恢复备份。

程序遇到高于自身支持范围的数据库、配置或协议版本时必须拒绝写入，并给出升级提示，不能尝试“尽量解析”。

所有持久化时间使用 UTC；界面按用户本地时区显示。

## 8. 单实例与锁

同一个项目默认只允许一个可写协调器实例。第二个实例可以：

- 以只读方式打开；或
- 在确认原实例失活后执行显式接管。

接管必须结合进程存活、锁更新时间和数据库租约判断。禁止仅因看到旧锁文件就直接删除锁。

## 9. 错误模型

错误类别：

- `CONFIG`, `PROTOCOL`, `STATE`, `WORKSPACE`, `GIT`
- `LEASE`, `VALIDATION`, `SECURITY`, `BUDGET`
- `STORAGE`, `INTERNAL`

稳定错误对象至少包含：

```json
{
  "code": "LEASE_OWNER_MISMATCH",
  "category": "LEASE",
  "message": "Human-readable summary",
  "retryable": false,
  "task_id": "TASK-014",
  "operation_id": "op_...",
  "details": {},
  "suggested_action": "Regenerate the task package",
  "created_at_utc": "2026-01-01T00:00:00Z"
}
```

错误代码必须稳定，界面文案可以本地化。

## 10. 隐私与诊断包

默认日志不得保存：文件全文、完整 diff、环境变量值、提示词中的秘密、API key 或用户主目录清单。

诊断包只包含经脱敏的配置摘要、版本、事件摘要、错误代码、有限长度日志和用户明确选择的证据。导出前展示内容预览。
