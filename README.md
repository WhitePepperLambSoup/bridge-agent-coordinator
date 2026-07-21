# 操作指南：GPT + Reasonix 双 Agent 协作

## 你的角色

你是**人类项目经理**——负责在两匹马之间传递接力棒。不需要写代码，只需要在不同阶段把任务交给不同的 agent。

## 完整流程

### 第一步：提需求（你 → GPT）

打开 ChatGPT/Codex，把 `AGENTS.md` 作为上下文喂给它，然后说：

> 我要做一个 XXX 项目。请阅读 specs/active/README.md 了解规范格式，
> 然后创建 specs/active/overview.md、architecture.md、tasks.md。
> 最后更新 COLLAB.md，标记第一个任务为「进行中」并写一段给 Reasonix 的 handoff。

### 第二步：编码（你 → Reasonix）

把 Reasonix 打开到这个项目目录，说：

> 请先读 AGENTS.md 和 COLLAB.md，然后读 specs/active/tasks.md，
> 按顺序实现第一个标记为「待开始」的任务。
> 完成后更新 COLLAB.md 汇报进度。

### 第三步：审核（你 → GPT，可选）

对于关键模块，切回 GPT 让它 review：

> 请读取最近 git commit 的改动和 COLLAB.md，review 代码质量，
> 如果通过就在 COLLAB.md 标记任务完成；否则写修改意见。

### 第四步：循环

重复第二步和第三步，直到 tasks.md 中所有任务完成。

### 第五步：收尾

让 GPT 把 `specs/active/` 移到 `specs/archive/`，更新 COLLAB.md 标记项目完成。

---

## 关键原则

| 原则 | 说明 |
|------|------|
| **GPT 只在关键节点介入** | 写 spec、任务分解、代码审核时才用 GPT，日常编码全部交给 Reasonix |
| **COLLAB.md 是唯一真相源** | 两个 agent 通过它了解"现在什么状态"，不依赖记忆 |
| **先读后写** | 每个 agent 启动时第一件事：读 AGENTS.md → COLLAB.md → specs/ |
| **任务粒度适中** | 每个 task 应该是 Reasonix 一次会话能完成的量（不要太大也不要太小） |
