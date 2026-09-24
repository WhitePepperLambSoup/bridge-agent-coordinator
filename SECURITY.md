# Security Policy / 安全策略

## Supported Version

Bridge is currently an alpha project. Security fixes are applied to the latest code on
the default branch.

Bridge 当前处于 Alpha 阶段，安全修复仅应用于默认分支的最新版本。

## Reporting a Vulnerability

Do not publish secrets, exploit details, or vulnerable repository contents in a public
issue. Use GitHub Private Vulnerability Reporting after it is enabled for the repository.
Include the affected version, reproduction steps, impact, and whether data loss is possible.

请勿在公开 Issue 中发布密钥、利用细节或存在漏洞的项目内容。仓库启用 GitHub Private
Vulnerability Reporting 后，请通过该渠道报告，并提供受影响版本、复现步骤、影响范围
以及是否存在数据丢失风险。

## Scope

High-priority reports include path traversal, unsafe Git mutations, command injection,
secret exposure, reviewer identity bypass, stale-attempt evidence reuse, and SQLite/Git
recovery inconsistencies.

高优先级问题包括路径逃逸、不安全 Git 修改、命令注入、密钥泄露、审查身份绕过、旧
attempt 证据复用，以及 SQLite/Git 恢复状态不一致。
