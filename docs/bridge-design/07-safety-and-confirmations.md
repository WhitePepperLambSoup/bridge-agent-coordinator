# 安全边界与危险操作确认

## 确认模式

| 模式 | 行为 |
|---|---|
| Strict | 所有写入、合并、清理和外部命令确认 |
| Balanced | 默认；可恢复低风险操作自动，高风险确认 |
| Expert | 可关闭大部分确认，但不能突破安全底线 |

动作策略值：

- `auto`
- `confirm_once`
- `confirm_session`
- `confirm_project`
- `always_confirm`
- `disabled`

用户可以按项目和动作覆盖默认值，所有覆盖写入审计事件并可撤销。

## 推荐默认策略

```yaml
safety:
  confirmation_mode: balanced
  actions:
    create_worktree: auto
    remove_clean_worktree: auto
    remove_dirty_worktree: always_confirm
    merge_low_risk: auto
    merge_medium_risk: confirm_once
    merge_high_risk: always_confirm
    overwrite_bridge_managed_files: always_confirm
    run_approved_checks: auto
    run_custom_command: always_confirm
    release_expired_clean_lease: confirm_once
    revert_merged_commit: always_confirm
    invoke_expensive_agent: always_confirm
```

## 可自动化操作

- 创建新任务分支和空 worktree；
- 删除干净且已合并的 Bridge worktree；
- 更新 managed views；
- 运行预先批准的检查；
- 导入有效回执；
- 重新生成任务包；
- 合并验证通过的 Low 风险任务；
- 释放干净且过期的租约；
- 清理 Bridge 自己创建的临时文件。

## Expert 可调整但默认确认

- Medium/High 风险合并；
- 更新 Bridge 托管入口区块；
- 自定义命令；
- 释放存在修改的租约；
- 删除未合并但干净的任务分支；
- 改派 InProgress 任务；
- 强模型升级；
- 超过预算；
- 修改路由和安全策略。

## 不可关闭的安全底线

Bridge 必须拒绝：

- 删除含未提交修改的非临时用户目录；
- 写入项目和配置工作区范围之外；
- reset hard、force push 或重写共享历史；
- 删除默认分支、主分支或未知归属分支；
- 输出、提交或复制检测到的密钥；
- 覆盖/删除无法确认归属的文件；
- 路径解析失败、符号链接逃逸或范围不明确时继续；
- 数据库与 Git 状态矛盾时自动猜测恢复；
- Agent 通过回执扩大任务权限。

用户可以离开 Bridge 自行执行，但 Bridge 本身不提供静默绕过。

## 确认对话框契约

必须显示：操作、风险、目标、候选 commit、影响文件、验证、审查、回滚方式、预计成本和触发原因。

可用操作：执行一次、当前会话记住、当前项目记住、禁用此类操作、取消并查看详情。Critical 操作不能提供“项目永久自动”。

默认焦点必须是取消。确认超时视为取消。

## 路径与文件保护

- 使用规范化绝对路径和 common-path 判断，不使用字符串前缀判断；
- 写入前解析符号链接；
- 临时文件与目标在同一文件系统并使用原子替换；
- 用户文件只允许新建或更新明确托管区块；
- 覆盖前显示 diff 和备份位置；
- 批量写入采用准备、验证、提交或回滚流程。

## 命令执行保护

- 使用 argv 数组；
- 不启用 shell，除非用户明确创建受信命令模板；
- 限制工作目录、超时、环境变量和输出大小；
- 默认继承最少环境；
- 不把秘密写入参数和日志；
- 支持取消并清理子进程树。

## 日志与诊断隐私

- 默认不记录文件全文和环境变量值；
- 敏感字段脱敏；
- 日志滚动和保留期限可配置；
- 导出诊断包前展示文件清单和脱敏预览；
- cost/token 数据标注真实或估算来源。
