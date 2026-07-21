# 记账本App — Architect-Engineer 协作模式

> GPT 做架构师（规划/审查/验收），Reasonix 做工程师（编码/测试/修复）。9 道工序流水线，含交付审查门、整改闭环、GPT 升级修复机制。

## 协作架构

```
┌─────────────────┐         ┌─────────────────┐
│  GPT             │  specs/  │  Reasonix        │
│  架构师/审核员         │◄───────▶│  工程师/执行者         │
│                 │ COLLAB  │                 │
│  需求澄清、架构设计、任务分解│         │  编码实现、自测验证、整改修复│
└─────────────────┘         └─────────────────┘
```

## 流水线

```
需求澄清 (Agent A) ──→
  架构设计 (Agent A) ──→
  任务分解 (Agent A) ──→
  编码实现 (Agent B) ──→
  自测验证 (Agent B) ──→
  交付审查 (Agent A) ──→
  整改修复 (Agent B) ──→
  升级修复 (Agent A) ──→
  最终验收 (Agent A)
```

## 关键文件

| 文件 | 作用 |
|------|------|
| `AGENTS.md` | 项目身份证 + 流水线定义 |
| `COLLAB.md` | 唯一真相源：当前状态 |
| `specs/active/tasks.md` | 任务分解 + 状态追踪 |
| `specs/active/review/` | 审查报告 |
| `specs/active/fix-orders/` | 整改指令 |

## 快速开始

### Agent A 启动
读 `AGENTS.md` → `COLLAB.md` → 执行你的流水线阶段

### Agent B 启动
读 `AGENTS.md` → `COLLAB.md` → `specs/active/tasks.md` → 开始编码

---

*由 Bridge 生成于 2026-07-21 15:50*
