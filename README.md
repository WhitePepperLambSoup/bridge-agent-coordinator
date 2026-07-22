# Bridge — Local Multi-Agent Coordinator

[中文](README.zh-CN.md) | English

A local control plane that coordinates multiple manually-opened AI agents through
structured task packages, receipts, validation gates, and Git worktree isolation.
Bridge does NOT call models directly — it generates task materials for agents that
the user manually opens and instructs.

## Quick Start

```bash
pip install -e .
python bridge.py           # Launch GUI
python -m pytest tests/ -v # Run all tests
```

Requirements: Python 3.11+, Git 2.40+, SQLite (stdlib), tkinter (stdlib).

## Features

- **Dynamic Agent profiles**: 2–8 agents with per-agent capabilities, roles, costs, and permissions
- **Structured task protocol**: Manifest, task packages, receipts, artifacts with schema validation
- **17-state task machine**: Draft → Planning → Ready → Assigned → InProgress → Submitted → Validating → Approved → MergeQueued → Merging → Done (with RevisionRequired, Escalated, Conflict, Blocked, Stale, Cancelled branches)
- **Lease management**: File/path/global resource leases with conflict detection
- **Review system**: Independent reviewer assignment, verdict tracking, escalation paths
- **Merge queue**: Serial FIFO integration with conflict handling
- **Validation engine**: Declarative checks with timeout, cancellation, evidence hashing
- **Operation log**: Git operation consistency with crash recovery
- **SQLite authoritative state**: All runtime state persisted and auditable
- **7 collaboration modes**: Architect-Engineer / Peer-Review / Spec-Driven / Quick-Start / Parallel-Team / Loop-Engineering / Parallel-Claim
- **Bilingual**: Full Chinese + English UI and generated documents

## Collaboration Modes

| Mode | Concurrency | Stages | Best For |
|------|-------------|--------|----------|
| Architect-Engineer | Serial | 9 | GPT plans, Reasonix codes |
| Peer-Review | Serial | 5 | Two equal agents cross-review |
| Spec-Driven | Serial | 8 | Spec-first with strict gates |
| Quick-Start | Serial | 3 | Minimal setup, rapid prototyping |
| Parallel-Team | **Parallel** | 6 | Two agents work simultaneously |
| Loop-Engineering | **Parallel** | 5 | Goal→Execute→Verify→Settle closed loop |
| Parallel-Claim | **Parallel** | 5 | Spec claim lines, no worktree needed |

## Parallel Mode: How Two Agents Work Simultaneously

The key challenge: two agents writing to the same file would overwrite each other.

**Our solution** — split file ownership + git arbitration:

```
project/
├── agent-gpt.md        ← Only GPT writes
├── agent-reasonix.md   ← Only Reasonix writes
├── board.md            ← Shared, git-arbitrated
├── tasks/              ← Per-task files, git-arbitrated
└── GIT_WORKTREE.md     ← Optional physical isolation guide
```

Three rules prevent conflicts:
| Rule | Mechanism |
|------|-----------|
| 🔒 Mutex writes | Each agent has its own status file — physically impossible to conflict |
| 📋 Git arbitration | Shared files use "pull before edit, commit immediately after" |
| 🏝️ Worktree isolation | Optional: `git worktree` for complete physical separation |

## Project Structure

```
bridge/
├── bridge.py          # Main program (single file, zero deps)
├── README.md          # This file (English)
├── README.zh-CN.md    # Chinese version
└── .gitignore
```

## License

MIT
