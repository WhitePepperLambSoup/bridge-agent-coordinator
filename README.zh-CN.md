# Bridge — AI Agent 协作桥接器

[English](README.md) | 中文

一个 GUI 工具，在目标项目文件夹中生成 AI Agent 桥接流程的 `.md` 文件，
让两个 AI Agent（如 GPT + Reasonix）通过文件系统高效协作。

## 运行

```bash
python bridge.py
```

零依赖，仅需 Python 3.8+ 标准库。

## 功能

- **6 种协作模式**：Architect-Engineer / Peer-Review / Spec-Driven / Quick-Start / Parallel-Team / 自定义
- **🌐 中英双语**：完整的中英文 GUI，一键切换。生成的 `.md` 文件跟随所选语言。
- **4 标签页 GUI**：项目设置 → Agent 配置 → LLM 辅助 → 流水线编辑
- **一键生成**：在目标项目文件夹生成 AGENTS.md、COLLAB.md（并行模式则为分离状态文件）、specs/ 等完整协作框架
- **可选 LLM 集成**：接入 OpenAI 兼容 API，AI 分析需求并自动填充配置
- **流水线编辑器**：自定义模式下增删改查 + 排序流水线阶段

## 协作模式一览

| 模式 | 并发方式 | 工序数 | 适用场景 |
|------|---------|--------|---------|
| Architect-Engineer | 串行 | 9 | GPT 架构师 + Reasonix 工程师 |
| Peer-Review | 串行 | 5 | 两个平等 Agent 互相审查 |
| Spec-Driven | 串行 | 8 | 规范先行，严格门禁 |
| Quick-Start | 串行 | 3 | 最小化设置，快速原型 |
| Parallel-Team | **并行** | 6 | 两个 Agent 同时工作 |
| Custom | 可配置 | 自定义 | 自定义流水线 |

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
├── bridge.py          # 主程序（单文件，零依赖）
├── README.md          # 英文版
├── README.zh-CN.md    # 中文版（本文件）
└── .gitignore
```

## 许可证

MIT
