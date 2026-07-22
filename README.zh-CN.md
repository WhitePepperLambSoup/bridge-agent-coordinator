# Bridge — 本地多 Agent 协调器

[English](README.md) | 中文

一个本地控制平面，通过结构化任务包、回执、验证门禁和 Git worktree 隔离来协调
多个手动打开的 AI Agent。Bridge 不直接调用模型 — 它为用户手动打开和指导的 Agent
生成任务材料。

## 快速开始

```bash
pip install -e .
python bridge.py           # 启动 GUI
python -m pytest tests/ -v # 运行全部测试
```

运行要求：Python 3.11+、Git 2.40+、SQLite（标准库）、tkinter（标准库）。

## 功能

- **动态 Agent 档案**：2–8 个 Agent，各自有独立的能力、角色、成本和权限
- **结构化任务协议**：Manifest、任务包、回执、产物，含 Schema 验证
- **17 状态任务机**：Draft → Planning → Ready → Assigned → InProgress → Submitted → Validating → Approved → MergeQueued → Merging → Done（含 RevisionRequired、Escalated、Conflict、Blocked、Stale、Cancelled 分支）
- **租约管理**：文件/路径/全局资源租约，含冲突检测
- **审查系统**：独立 reviewer 指派、裁定追踪、升级路径
- **合并队列**：串行 FIFO 集成，含冲突处理
- **验证引擎**：声明式检查，含超时、取消、证据哈希
- **操作日志**：Git 操作一致性，含崩溃恢复
- **SQLite 权威状态**：全部运行时状态持久化，可审计
- **7 种协作模式**：Architect-Engineer / Peer-Review / Spec-Driven / Quick-Start / Parallel-Team / Loop-Engineering / Parallel-Claim
- **中英双语**：完整的中英文界面和生成文档
- **可选 LLM 集成**：接入 OpenAI 兼容 API，AI 分析需求并自动填充配置
- **流水线编辑器**：自定义流水线阶段的增删改查 + 排序

## 协作模式一览

| 模式 | 并发方式 | 工序数 | 适用场景 |
|------|---------|--------|---------|
| Architect-Engineer | 串行 | 9 | GPT 架构师 + Reasonix 工程师 |
| Peer-Review | 串行 | 5 | 两个平等 Agent 互相审查 |
| Spec-Driven | 串行 | 8 | 规范先行，严格门禁 |
| Quick-Start | 串行 | 3 | 最小化设置，快速原型 |
| Parallel-Team | **并行** | 6 | 两个 Agent 同时工作 |
| Loop-Engineering | **并行** | 5 | Goal→Execute→Verify→Settle 闭环 |
| Parallel-Claim | **并行** | 5 | Spec claim 行，无需 worktree |

## 并行模式：两个 Agent 如何同时工作

核心难题：两个 agent 同时写同一个文件会互相覆盖。

**解决方案** — 分离文件所有权 + git 仲裁：

```
project/
├── agent-gpt.md        ← 只有 GPT 写
├── agent-reasonix.md   ← 只有 Reasonix 写
├── board.md            ← 共享，git 仲裁
├── tasks/              ← 独立任务文件，git 仲裁
└── GIT_WORKTREE.md     ← 可选的物理隔离指南
```

三条规则防止冲突：
| 规则 | 机制 |
|------|------|
| 🔒 互斥写 | 每个 agent 有自己的状态文件 — 物理上不可能冲突 |
| 📋 Git 仲裁 | 共享文件遵循「改前 pull，改后立即 commit」 |
| 🏝️ Worktree 隔离 | 可选：`git worktree` 实现完全物理分离 |

## 项目结构

```
bridge/
├── bridge.py          # 程序入口
├── README.md          # 英文版
├── README.zh-CN.md    # 中文版（本文件）
└── .gitignore
```

## 许可证

MIT
