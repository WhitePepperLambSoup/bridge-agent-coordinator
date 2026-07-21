# Bridge — AI Agent 协作桥接器

一个 GUI 工具，在目标项目文件夹中生成 AI Agent 桥接流程的 `.md` 文件，
让两个 AI Agent（如 GPT + Reasonix）通过文件系统高效协作。

## 运行

```bash
python bridge.py
```

零依赖，仅需 Python 3.8+ 标准库。

## 功能

- **5 种协作模式**：Architect-Engineer / Peer-Review / Spec-Driven / Quick-Start / 自定义
- **4 标签页 GUI**：项目设置 → Agent 配置 → LLM 辅助 → 流水线编辑
- **一键生成**：在目标项目文件夹生成 AGENTS.md、COLLAB.md、specs/ 等完整协作框架
- **可选 LLM 集成**：接入 OpenAI 兼容 API，AI 分析需求并自动填充配置
- **流水线编辑器**：自定义模式下增删改查 + 排序流水线阶段

## 协作模式一览

| 模式 | 说明 | 工序数 |
|------|------|--------|
| Architect-Engineer | GPT 架构师 + Reasonix 工程师，含审查门和升级修复 | 9 |
| Peer-Review | 两个平等 Agent 并行开发 + 交叉审查 | 5 |
| Spec-Driven | 规范先行，严格门禁，逐任务审查 | 8 |
| Quick-Start | 最小化设置，快速开始 | 3 |
| Custom | 用户完全自定义流水线 | 自定义 |

## 项目结构

```
bridge/
├── bridge.py          # 主程序（单文件，零依赖）
├── .gitignore
└── README.md
```
