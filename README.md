# Bridge — Local Multi-Agent Coordinator

[中文](README.zh-CN.md) | English

A local control plane that coordinates multiple manually-opened AI agents through
structured task packages, receipts, validation gates, and Git worktree isolation.
The core workflow does not call models. The optional LLM tool only calls a configured
API when the user explicitly enables and runs it.

## Quick Start

Run these commands from the repository root in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\bridge-gui.exe
```

Requirements: Python 3.11+, Git 2.40+, SQLite (stdlib), tkinter (stdlib).

See the [English user guide](docs/USER_GUIDE.en.md) for the complete workflow,
safety modes, recovery, and uninstall instructions. The
[Chinese user guide](docs/USER_GUIDE.md) is also available.

## Features

- **Single-next-step UI**: one legal primary action per task state, with internal IDs and commit discovery handled by Bridge
- **Agent profiles**: planner/reviewer and implementer by default, with an optional third implementer
- **Structured task protocol**: Manifest, task packages, receipts, artifacts with schema validation
- **17-state task machine**: Draft → Planning → Ready → Assigned → InProgress → Submitted → Validating → Approved → MergeQueued → Merging → Done (with RevisionRequired, Escalated, Conflict, Blocked, Stale, Cancelled branches)
- **Lease management**: File/path/global resource leases with conflict detection
- **Review system**: Independent reviewer assignment, verdict tracking, escalation paths
- **Merge queue**: Serial FIFO integration with conflict handling (real Git merge via integration worktree; requires Git repo)
- **Validation engine**: Declarative checks run in the candidate task worktree, with timeout, cancellation, evidence hashing, and a project-scoped command registry
- **Operation log**: Git operation consistency with crash recovery (SQLite-backed; recovery scanner for incomplete operations)
- **SQLite authoritative state**: Runtime state persisted for leases, reviews, merge queue, costs, operations, workspaces, and attempts
- **7 collaboration modes**: Architect-Engineer / Peer-Review / Spec-Driven / Quick-Start / Parallel-Team / Loop-Engineering / Parallel-Claim
- **Chinese desktop UI** with Chinese and English document templates
- **Optional LLM assistance** through a user-configured OpenAI-compatible API
- **Pipeline editor** for custom generated-document workflows

## Current Limitations

- Bridge does not launch or control agent applications; agent handoff is manual.
- The desktop UI is currently Chinese. Generated collaboration documents support Chinese and English.
- This is an alpha release. Use a test repository and keep backups before coordinating important work.

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
├── bridge.py          # Application entry point
├── bridgelib/         # Coordinator core and desktop GUI
├── docs/              # Design and user documentation
├── tests/             # Unit, regression, and real-Git integration tests
├── README.md          # This file (English)
├── README.zh-CN.md    # Chinese version
└── .gitignore
```

## Development

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

The distribution name is `bridge-agent-coordinator`; the import package remains
`bridgelib` and the desktop command remains `bridge-gui`.

## Documentation

- [English user guide](docs/USER_GUIDE.en.md)
- [中文用户手册](docs/USER_GUIDE.md)
- [GitHub upload guide / GitHub 上传指南](docs/GITHUB_UPLOAD_GUIDE.md)
- [Design documents (Chinese)](docs/bridge-design/00-README.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

MIT
