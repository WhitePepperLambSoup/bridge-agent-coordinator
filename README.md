# Bridge — GPT + Reasonix 双 Agent 协作框架

> GPT 做大脑，Reasonix 做双手。一套带质量门禁的开发流水线。

---

## 🏗️ 协作架构

```
                          ┌─────────────┐
                          │   你（人类）  │
                          └──────┬──────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                                     ▼
     ┌─────────────────┐                   ┌─────────────────┐
     │  GPT（架构师）    │                   │ Reasonix（工程师）│
     │                  │    specs/active/  │                  │
     │ ① 需求澄清       │◄─────────────────▶│ ④ 编码实现       │
     │ ② 架构设计       │    COLLAB.md      │ ⑤ 自测验证       │
     │ ③ 任务分解       │                   │ ⑦ 整改修复       │
     │ ⑥ 交付审查       │                   │ ⑧ 升级求助       │
     │ ⑧ 亲自修复       │                   │                  │
     │ ⑨ 最终验收       │                   │                  │
     └─────────────────┘                   └─────────────────┘
              │                                     │
              └──────────────────┬──────────────────┘
                                 │
                          共享 Git 仓库
```

## 🔄 完整流水线

```
需求澄清 ──→ 架构设计 ──→ 任务分解 ──→ 编码实现 ──→ 自测验证
  (GPT)       (GPT)        (GPT)      (Reasonix)   (Reasonix)
                                                 │
                    ┌────────────────────────────┘
                    ▼
              交付审查 (GPT)
                    │
            ┌───────┴───────┐
            ▼               ▼
         通过 ✅         不通过 ❌
            │               │
            ▼               ▼
        最终验收         整改指令 (GPT)
         (GPT)              │
            │               ▼
            ▼          整改修复 (Reasonix)
          归档              │
                    ┌───────┴───────┐
                    ▼               ▼
                通过 ✅         仍失败 ❌ (第2轮后)
                    │               │
                    ▼               ▼
               重新提交审查     🚨 升级：GPT亲自修复
                                   │
                                   ▼
                              最终验收 → 归档
```

## 📂 关键文件

| 文件 | 作用 | 谁来读写 |
|------|------|---------|
| `AGENTS.md` | 项目身份证 + 流水线定义 | 两个 agent 都读 |
| `COLLAB.md` | **唯一真相源**：当前状态、任务追踪、升级记录 | 两个 agent 都读写 |
| `specs/active/tasks.md` | 任务分解 + 状态机 | GPT 创建，Reasonix 更新状态 |
| `specs/active/overview.md` | 项目总览 | GPT 写，Reasonix 读 |
| `specs/active/architecture.md` | 架构设计 | GPT 写，Reasonix 读 |
| `specs/active/review/` | GPT 审查报告 | GPT 写 |
| `specs/active/fix-orders/` | GPT 整改指令 | GPT 写，Reasonix 执行 |
| `specs/active/escalation.md` | 升级记录 + GPT 修复日志 | 两人都写 |
| `specs/active/acceptance.md` | 最终验收清单 | GPT 签署 |
| `specs/GPT-QUICKREF.md` | GPT 操作手册 | GPT 启动时读 |
| `specs/REASONIX-QUICKREF.md` | Reasonix 操作手册 | Reasonix 启动时读 |

## 🚀 怎么开始

### 第一步：GPT 规划

把以下内容喂给 ChatGPT/Codex：
1. `AGENTS.md` 的内容
2. `specs/GPT-QUICKREF.md` 的内容
3. 你的需求

然后说：
> 请按流水线执行模式一（规划模式），创建 specs/active/ 下的规范文档。

### 第二步：Reasonix 编码

切到 Reasonix（本项目），说：
> 读 AGENTS.md → COLLAB.md → specs/active/tasks.md，开始编码。

### 第三步：GPT 审查

代码提交后切回 GPT，说：
> COLLAB.md 显示有待审查任务，请执行模式二（审查模式）。

### 第四步：循环

按流水线图走，直到所有任务通过验收。

---

## 🔑 核心设计原则

| 原则 | 说明 |
|------|------|
| **GPT 只在关键节点介入** | 规划 → 审查 → 验收。日常编码不消耗 GPT token |
| **失败不跳级** | 审查不通过必须修复，不允许绕过 |
| **最多 2 轮整改** | 第 3 次自动升级，GPT 亲自下场 |
| **无证据不签字** | 验收必须有可验证的测试结果/日志 |
| **COLLAB.md 是唯一真相源** | 不依赖记忆，文件即状态 |
