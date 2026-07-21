# Reasonix 快速参考卡

## 你（Reasonix/DeepSeek）的职责

你是**主力工程师**，负责所有具体编码工作。你的输入是 `specs/active/` 下的规范文档和 `COLLAB.md`。

## 每次启动时的检查清单

按顺序读这三个文件（已经在你的上下文里了）：
1. `AGENTS.md` — 了解项目技术栈和规范
2. `COLLAB.md` — 了解当前进度和你要做什么
3. `specs/active/tasks.md` — 找到你的任务

## 工作流程

```
读 COLLAB.md → 找到当前任务 → 读相关 spec → 编码 → 测试 → 更新 COLLAB.md
```

## 如何更新 COLLAB.md

完成任务后，更新以下区域：

### 当前任务
```markdown
## 当前任务
- Task 1: XXX → ✅ 已完成，commit: abc1234
- Task 2: YYY → 🔄 进行中
```

### Handoff 接力区
```markdown
> **Reasonix → GPT**：
> - Task 1 已完成，代码在 src/auth.ts
> - 遇到一个问题：XXX，我的处理方式是 YYY
> - 建议 GPT review src/auth.ts 中的安全逻辑
```

## 遇到问题怎么办

- **不确定怎么实现**：在 COLLAB.md 写清楚疑问，让用户切到 GPT 去问
- **发现 spec 有矛盾**：按自己的判断先做，在 COLLAB.md 记录疑虑
- **任务比预期复杂**：拆分后逐个完成，不要一次搞太大

## 提交规范

- 每个 task 完成后做一个 git commit
- commit message 格式：`[task] 完成 XXX 功能`
- 小步提交，方便 GPT review 时看 diff
