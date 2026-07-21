# specs/active/ — 当前活跃规范

> 本目录由 **GPT（架构师）** 产出，**Reasonix（工程师）** 消费。
> 规范完成后移入 `specs/archive/`。

## 标准文件结构

```
specs/active/
├── overview.md              # 项目总览：做什么、为什么、用户故事
├── architecture.md          # 架构设计：技术栈、模块划分、API、数据流
├── tasks.md                 # 任务分解 + 状态追踪（核心文件）
│
├── review/                  # GPT 审查产出
│   ├── review-T001.md       # 每个任务的审查报告
│   └── ...
│
├── fix-orders/              # GPT 整改指令
│   ├── fix-T001-round1.md   # 第 N 轮整改要求
│   └── ...
│
├── escalation.md            # 升级记录（Reasonix 无法解决 → GPT 亲自修复）
└── acceptance.md            # 最终验收报告（GPT 签字）
```

## 工作流

详见根目录 `AGENTS.md` 中的 9 道工序流水线。
