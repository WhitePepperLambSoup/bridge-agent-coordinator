# Changelog / 更新日志

All notable changes to this project are documented here.

本文件记录项目的重要变更。

## [0.1.0] - 2026-07-25

### Added

- Local SQLite-backed coordination for tasks, leases, attempts, reviews, and merge queues.
- Isolated task and integration Git worktrees.
- Receipt and artifact validation bound to the current attempt.
- Safety policies, recovery records, validation commands, and operation auditing.
- Scrollable Windows desktop UI with a single recommended action per task state.
- Chinese desktop workflow plus Chinese and English generated collaboration documents.

### Security

- Fail-closed commit, receipt, scope, reviewer identity, and validation checks.
- Dirty worktree confirmation and protection against destructive target-branch updates.
