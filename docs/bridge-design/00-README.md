# Bridge 本地多 Agent 协调器设计文档

本文档包定义 Bridge 从“Markdown 模板生成器”演进为“本地多 Agent 协调器”的目标架构。Bridge 不直接调用模型，而是协调用户手动打开的 Codex、Reasonix 及其他 Agent，让昂贵强模型负责规划、难点突破、审查和验收，让低成本模型承担大量编码、测试、修复和文档工作。

## 已批准的产品决策

- Bridge 是本地控制平面，不自动操作 Agent 窗口，也不保存模型密钥。
- 用户手动打开 Agent、复制 Bridge 生成的提示词，并让 Agent 在指定工作区执行任务。
- SQLite 是权威状态源；Markdown/JSON 是 Agent 交换协议和人类可读视图。
- 项目可选择独立 worktree、共享目录或只读咨询；默认推荐独立 worktree。
- 多个 Agent 可以并行开发，但所有候选变更必须通过串行合并队列。
- 推进方式支持人工、自动和混合；默认推荐混合。
- Agent 路由由系统建议和用户覆盖共同决定。
- 安全确认支持 Strict、Balanced、Expert，并保留不可关闭的安全底线。
- 首版优先支持 Windows 10/11、Git 和 Python 3.11+。
- Agent 数量动态化，建议 2～8 个，不在数据模型中固定 Agent A/B/C。

## 阅读顺序

1. [产品边界](01-product-scope.md)
2. [系统架构](02-system-architecture.md)
3. [任务状态机](03-task-state-machine.md)
4. [Agent 路由与成本](04-agent-routing-and-cost.md)
5. [Agent 文件协议](05-agent-file-protocol.md)
6. [Git、Worktree 与冲突](06-git-worktree-and-conflicts.md)
7. [安全与确认](07-safety-and-confirmations.md)
8. [桌面 UI 与用户流程](08-desktop-ui-and-user-flow.md)
9. [数据库、事件与配置](09-database-events-and-config.md)
10. [适配器接口](10-adapter-interfaces.md)
11. [测试与验收](11-testing-and-acceptance.md)
12. [分阶段路线图](12-phased-roadmap.md)
13. [Reasonix 交付约定](REASONIX-HANDOFF.md)

## 协议与配置示例

- [项目配置示例](schemas/project.example.yaml)
- [Agent 配置示例](schemas/agent.example.yaml)
- [任务 manifest 示例](schemas/task-manifest.example.json)
- [完成回执示例](schemas/receipt.example.md)
- [产物清单示例](schemas/artifacts.example.json)

## 实施原则

- 每一阶段必须形成独立可运行、可测试、可验收的增量。
- 先建立测试和核心协议，再扩展 Git 自动化、GUI 和成本优化。
- 所有状态变更必须经过显式状态机，禁止 UI 直接改数据库字段。
- 所有外部输入均不可信，包括 Agent 回执、Git 状态、Markdown 和配置文件。
- 所有危险操作必须遵循项目确认策略和不可关闭的安全边界。
- 不允许通过添加特殊分支继续固化“第三 Agent”逻辑；必须从一开始支持动态 Agent 集合。
- 不以“测试通过”文字作为证据，必须独立运行验证命令并核对 Git diff。

## 核心术语

| 术语 | 定义 |
|---|---|
| Goal | 用户希望完成的项目级目标 |
| Task | 具有边界、验收标准、预算和依赖的最小工作单元 |
| Attempt | 一个 Task 的一次具体执行尝试 |
| Lease | Agent 对任务文件范围或全局资源的临时占用 |
| Task Package | Bridge 为 Agent 生成的只读任务材料 |
| Receipt | Agent 写回的结构化进度、完成或阻塞声明 |
| Validation Gate | Bridge 对协议、Git、范围、检查和验收证据的独立验证 |
| Review Package | 提供给审查 Agent 的最小上下文包 |
| Merge Queue | 对已批准候选变更进行串行集成、回归和合并的队列 |
| Managed View | 从 SQLite 生成的只读 Markdown 视图 |

## 文档权威性

本文档包是实现和验收基线。实现如果需要偏离，必须在提交中增加“设计偏差说明”，解释原因、替代方案、风险和迁移影响，并在继续下一阶段前获得批准。
