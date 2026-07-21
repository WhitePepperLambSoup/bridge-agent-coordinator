# specs/active/ 目录说明

> 本目录存放**当前活跃的规范文档**，由 GPT（架构师）产出，Reasonix（工程师）消费。
> 规范完成后移入 `specs/archive/`。

## 文件结构

```
specs/active/
├── overview.md        # 项目总览：要做什么、为什么要做
├── architecture.md    # 架构设计：技术选型、模块划分、数据流、API 设计
└── tasks.md           # 任务分解：有序的任务列表，每个任务有验收标准
```

## 工作流

1. **用户**告诉 GPT 想做什么
2. **GPT** 按需创建上述文件（不一定要全部，小需求可能只需要 tasks.md）
3. **GPT** 更新 `COLLAB.md`，将当前任务指针指向 tasks.md 中的第一个任务
4. **用户**切换到 Reasonix，让 Reasonix 读取 spec 后开始编码
5. **Reasonix** 完成任务后更新 `COLLAB.md` 汇报进度
6. **GPT**（可选）review 代码，决定通过或返工
7. 循环直到所有任务完成
8. 完成后将 spec 文件移到 `specs/archive/`
