# Bridge — AI Agent Collaboration Hub

[中文](README.zh-CN.md) | English

A GUI tool that generates AI agent bridge workflow `.md` files in your project folder,
enabling two AI agents (e.g., GPT + Reasonix) to collaborate efficiently through the filesystem.

## Run

```bash
python bridge.py
```

Zero dependencies — Python 3.8+ standard library only.

## Features

- **6 collaboration modes**: Architect-Engineer / Peer-Review / Spec-Driven / Quick-Start / Parallel-Team / Custom
- **🌐 Bilingual**: Full Chinese + English UI, switch with one click. Generated `.md` files follow the selected language.
- **4-tab GUI**: Project Setup → Agent Config → LLM Assist → Pipeline Editor
- **One-click generation**: Produces AGENTS.md, COLLAB.md (or agent-specific status files for parallel mode), specs/, and more in your target folder
- **Optional LLM integration**: OpenAI-compatible API to analyze requirements and auto-fill configuration
- **Pipeline editor**: Add, remove, reorder, and edit pipeline stages in Custom mode

## Collaboration Modes

| Mode | Concurrency | Stages | Best For |
|------|-------------|--------|----------|
| Architect-Engineer | Serial | 9 | GPT plans, Reasonix codes |
| Peer-Review | Serial | 5 | Two equal agents cross-review |
| Spec-Driven | Serial | 8 | Spec-first with strict gates |
| Quick-Start | Serial | 3 | Minimal setup, rapid prototyping |
| Parallel-Team | **Parallel** | 6 | Two agents work simultaneously |
| Custom | Configurable | Custom | Define your own pipeline |

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
