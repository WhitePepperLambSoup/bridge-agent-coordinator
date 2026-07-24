# Agent 文件交换协议

## 目标

任何能读写项目文件的 Agent，即使没有统一 API 或 CLI，也能参与 Bridge 协作。Markdown 负责可读内容，JSON/YAML front matter 负责机器校验。

协议版本字段为 `protocol_version`。首版值为 `1`。

## 任务包目录

```text
.bridge-task/
├── manifest.json
├── PROMPT.md
├── TASK.md
├── CONTEXT.md
├── CONSTRAINTS.md
├── CHECKS.md
├── RECEIPT.md
├── ARTIFACTS.json
└── HEARTBEAT.json
```

| 文件 | Bridge | Agent |
|---|---|---|
| manifest.json | 写 | 只读 |
| PROMPT.md | 写 | 只读 |
| TASK.md | 写 | 只读 |
| CONTEXT.md | 写 | 只读 |
| CONSTRAINTS.md | 写 | 只读 |
| CHECKS.md | 写 | 只读 |
| RECEIPT.md | 读 | 写 |
| ARTIFACTS.json | 读 | 写 |
| HEARTBEAT.json | 读 | 写 |

Bridge 分派前必须检查旧任务包是否有未导入回执。存在时禁止覆盖。

## manifest.json

必填字段：

```json
{
  "protocol_version": 1,
  "project_id": "project-001",
  "task_id": "TASK-014",
  "attempt": 2,
  "lease_id": "lease-8f27",
  "agent_id": "reasonix-worker",
  "role": "implementer",
  "base_commit": "abc1234",
  "branch": "bridge/reasonix-worker/TASK-014/a2",
  "created_at": "2026-07-22T00:00:00Z",
  "lease_expires_at": "2026-07-22T02:00:00Z",
  "allowed_paths": ["src/auth/**", "tests/auth/**"],
  "forbidden_paths": [".bridge/**", "deployment/**"],
  "required_outputs": ["receipt", "artifacts", "commit"]
}
```

Importer 必须验证版本、task、attempt、lease、agent、base commit、branch 和 required outputs。manifest 发生 Agent 修改时回执无效。

## PROMPT.md

只包含最短启动说明：按顺序读取任务、上下文、约束和检查；只修改允许范围；完成后提交代码并填写回执。Agent 适配器可改变措辞，不可改变权限、范围和验收标准。

## TASK.md

使用 YAML front matter 和固定章节：

```markdown
---
task_id: TASK-014
attempt: 2
owner: reasonix-worker
reviewer: codex-primary
risk: medium
complexity: medium
---

# 实现登录失败保护

## 目标

防止登录接口泄漏用户是否存在。

## 范围

- 修改认证服务错误响应。
- 增加对应测试。

## 验收标准

- [ ] AC-1 不存在用户和密码错误返回相同外部错误。
- [ ] AC-2 内部日志仍能区分真实原因。

## 非目标

- 不实现多因素认证。
```

验收标准必须有稳定 ID，供回执和验证结果引用。

## CONTEXT.md

只包含批准决定、相关接口、约束、文件和基线摘要。禁止默认写入完整聊天历史、无关源码、密钥或环境变量值。

## CONSTRAINTS.md

固定章节包括 Allowed、Forbidden、Git Rules、Safety Rules。必须明确禁止修改 manifest、其他 Agent 任务包、Bridge runtime、主分支和范围外文件。

## CHECKS.md

每个检查包含稳定 ID、参数数组、工作目录、超时、是否必需和验收映射。例如：

```yaml
checks:
  - id: auth-tests
    argv: [python, -m, pytest, tests/auth, -q]
    timeout_seconds: 300
    required: true
    supports: [AC-1, AC-2]
```

Agent 回执必须记录命令、退出码和摘要，但 Bridge 仍需独立执行。

## RECEIPT.md

```markdown
---
protocol_version: 1
task_id: TASK-014
attempt: 2
lease_id: lease-8f27
agent_id: reasonix-worker
status: completed
submission_commit: def5678
completed_at: 2026-07-22T01:20:00Z
---

# Completion Receipt

## Summary

统一了认证失败响应并增加回归测试。

## Acceptance Evidence

- AC-1: `test_unknown_user_matches_wrong_password` 通过。
- AC-2: 审计日志测试通过。

## Checks

| Check | Exit code | Result |
|---|---:|---|
| auth-tests | 0 | 42 passed |

## Files Changed

- src/auth/service.py
- tests/auth/test_login.py

## Risks

- 旧客户端不应依赖错误文本。

## Requests for Reviewer

- 确认监控规则不依赖旧错误文本。
```

允许的 `status`：`progress`、`blocked`、`completed`、`failed`、`abandoned`。

## ARTIFACTS.json

字段包括 `protocol_version`、`task_id`、`attempt`、`agent_id`、`base_commit`、`submission_commit`、`changed_files`、`checks`、`evidence_files`、`new_dependencies`、`migrations`、`known_failures` 和 `generated_at`。示例见 [schemas/artifacts.example.json](schemas/artifacts.example.json)。Bridge 必须用 Git 和独立验证交叉检查，不能把 Agent 声明的文件列表或检查结果当作事实。

## HEARTBEAT.json

字段包括 task、lease、status、progress percent、current step、updated at 和 blocker。心跳只用于显示进度和续租，不能作为完成证据。

## 导入算法

1. 监控器检测文件变化。
2. 等待大小和修改时间稳定。
3. 读取并限制最大文件大小。
4. 验证编码、格式和协议版本。
5. 核对 task、attempt、lease、agent、commit。
6. 计算内容哈希并检查重复。
7. 保存原始文件引用和解析结果。
8. 产生 ReceiptImported 事件。
9. 将任务推进到 Submitted/Validating，而不是直接 Approved。

过期 attempt、未知 lease、重复 hash、修改 manifest 或 commit 不一致均拒绝推进。

## 跨 Agent 交接

Agent 不直接向其他 Agent 分派任务。统一链路：

```text
实现回执 → Bridge 验证 → 审查包 → 审查回执
→ Bridge 生成修复/批准决定 → 新任务包
```

## Managed Views

BOARD、DECISIONS、COSTS、MERGE_QUEUE 和 AUDIT_LOG 顶部必须包含：

```markdown
> GENERATED VIEW — DO NOT EDIT
> Source of truth: `.bridge/runtime/bridge.db`
```

Markdown 质量检查必须验证表格列数、内部引用、语言一致性、动态 Agent 数量和协议字段一致性。
