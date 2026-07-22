# 10. 适配器边界与核心接口

## 1. 原则

协调器核心只理解任务、租约、工作区、验证、审查、成本和事件，不直接依赖某个模型的 CLI 文案、Git 命令细节或桌面通知实现。

适配器必须可替换、可测试，且不能扩大任务权限。

## 2. Agent 提示适配器

首批类型：

- `GenericFileAgentAdapter`
- `CodexPromptAdapter`
- `ReasonixPromptAdapter`
- `CustomTemplateAdapter`

输入：

- Agent profile
- 任务 manifest
- 语言和显示偏好
- 启动上下文与回执路径

输出：

- 可复制到目标 Agent 的启动提示
- 推荐启动目录
- 必须读取的文件列表
- 回执保存路径和完成规则

适配器只能改变表达方式与目标工具的使用提示，不能修改 `allowed_paths`、危险操作策略、验证要求、预算或任务身份。

模板变量采用白名单；未知变量报错，不静默保留。自定义模板不得执行脚本。

## 3. Repository 适配器

`GitRepositoryAdapter` 负责：

- 仓库检查与基线解析
- 分支、worktree 创建和状态查询
- diff、提交范围与文件范围检查
- cherry-pick、merge、revert 与冲突检测
- worktree 安全清理

业务核心不得散落调用 Git。每个会修改仓库的接口必须接收预期基线、操作 ID 和确认上下文，并返回结构化结果。

命令执行使用参数数组，不通过 shell 拼接字符串。任何路径先规范化并验证其属于项目或已登记 worktree。

## 4. Validation 适配器

验证规则为声明式配置：

- `id`, `display_name`
- `executable`, `args[]`, `working_directory`
- `timeout_seconds`, `output_limit_bytes`
- `required`, `risk_level`
- `evidence_paths[]`

执行要求：

- 禁止把验证命令作为任意 shell 文本运行。
- 支持超时、取消和子进程清理。
- 分别保存退出码、stdout/stderr 有界摘要和证据哈希。
- 输出被截断时明确标记，不能伪装成完整日志。
- 程序退出或取消后不得遗留孤儿验证进程。

## 5. 通知适配器

V1：

- `InAppNotificationAdapter`
- `DesktopNotificationAdapter`
- `LogNotificationAdapter`

未来可增加 webhook、邮件或聊天工具，但默认关闭。通知只传递任务编号、状态和简短摘要，不发送代码全文或敏感日志。

## 6. 导入导出适配器

导出：

- Markdown 任务包和汇总报告
- JSON manifest、artifacts 和事件摘要
- 诊断包
- 验收报告

导入比导出严格：所有输入必须进行 schema、版本、任务身份、attempt、lease、路径、哈希和状态迁移校验。不能因为“Markdown 看起来合理”就更新数据库。

## 7. 文件监听服务

文件监听只负责发现候选变化，不直接推进状态。处理顺序：

1. 等待文件在稳定窗口内大小和修改时间不再变化。
2. 读取到临时区域并计算哈希。
3. 完成协议校验和幂等检查。
4. 提交导入事务。
5. 发布事件，由状态机决定下一步。

必须兼容直接写入、临时文件替换、重复通知、应用重启和文件被短暂锁定等情况。

## 8. 接口测试契约

每个适配器需要：

- 正常路径测试
- 无效输入与边界测试
- 超时/取消/崩溃测试
- 幂等性测试
- 可序列化结构化结果
- 假实现或内存实现，供核心单元测试使用

适配器异常必须转换成稳定错误模型，不允许把原始堆栈直接作为用户错误信息。
