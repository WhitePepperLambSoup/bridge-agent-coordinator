# Bridge — 本地多 Agent 协调器

[English](README.md) | 中文

一个本地控制平面，通过结构化任务包、回执、验证门禁和 Git worktree 隔离来协调
多个手动打开的 AI Agent。核心协调流程不调用模型；“工具”页中的可选 LLM 辅助只有
在用户主动配置并启用 API 后才会调用模型。

## 快速开始

### Windows EXE

从最新的 [GitHub Release](https://github.com/qq2080685752-jpg/bridge-agent-coordinator/releases)
下载 `Bridge-windows-x64.zip`，解压后运行 `Bridge.exe`，无需安装 Python。

当前 EXE 尚未进行代码签名，Windows SmartScreen 可能显示“未知发布者”。运行前请使用
Release 中的 `SHA256SUMS.txt` 核对文件哈希。

### 从源码运行

在项目根目录打开 PowerShell：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\bridge-gui.exe
```

运行要求：Python 3.11+、Git 2.40+、SQLite（标准库）、tkinter（标准库）。

安装、完整协调流程、安全模式、故障恢复和卸载方法见
[用户手册](docs/USER_GUIDE.md)。

## 功能

- **单步任务界面**：每个状态只显示一个合法的下一步，不需要搬运内部 ID 或 commit hash
- **Agent 档案**：默认规划/审查者 + 执行者，可增加第三个执行 Agent
- **结构化任务协议**：Manifest、任务包、回执、产物，含 Schema 验证
- **17 状态任务机**：Draft → Planning → Ready → Assigned → InProgress → Submitted → Validating → Approved → MergeQueued → Merging → Done（含 RevisionRequired、Escalated、Conflict、Blocked、Stale、Cancelled 分支）
- **租约管理**：文件/路径/全局资源租约，含冲突检测
- **审查系统**：独立 reviewer 指派、裁定追踪、升级路径
- **合并队列**：串行 FIFO 集成，含冲突处理
- **验证引擎**：在候选任务 worktree 中运行声明式检查，含超时、取消和证据哈希
- **操作日志**：Git 操作一致性，含崩溃恢复
- **SQLite 权威状态**：全部运行时状态持久化，可审计
- **7 种协作模式**：Architect-Engineer / Peer-Review / Spec-Driven / Quick-Start / Parallel-Team / Loop-Engineering / Parallel-Claim
- **中文桌面界面**：生成文档支持中英文模板
- **可选 LLM 集成**：接入 OpenAI 兼容 API，AI 分析需求并自动填充配置
- **流水线编辑器**：自定义流水线阶段的增删改查 + 排序

## 当前限制

- Bridge 不会启动或控制 Agent 客户端，Agent 交接仍由用户手动完成。
- 桌面界面目前为中文；生成的协作文档支持中文和英文。
- 当前版本为 Alpha。协调重要项目之前，请先在测试仓库中试用并保留备份。

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
├── bridgelib/         # 协调器核心和桌面 GUI
├── docs/              # 设计文档和用户手册
├── tests/             # 单元、回归和真实 Git 集成测试
├── README.md          # 英文版
├── README.zh-CN.md    # 中文版（本文件）
└── .gitignore
```

## 开发

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

发行包名称为 `bridge-agent-coordinator`；Python import 仍使用 `bridgelib`，桌面启动
命令仍为 `bridge-gui`。

## 文档

- [中文用户手册](docs/USER_GUIDE.md)
- [English user guide](docs/USER_GUIDE.en.md)
- [GitHub 上传指南 / GitHub upload guide](docs/GITHUB_UPLOAD_GUIDE.md)
- [设计文档（中文）](docs/bridge-design/00-README.md)
- [第三方声明](THIRD_PARTY_NOTICES.md)

## 许可证

MIT
