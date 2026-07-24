# Bridge User Guide

Bridge is a local coordination console for multiple AI coding agents. You still open
Codex, Reasonix, or another agent manually. Bridge manages safe assignment, isolated
workspaces, validation, independent review, and serial merging. The core workflow does
not call a model or consume API tokens.

## Start Bridge

Requirements: Python 3.11+, Git 2.40+, and a Python installation with tkinter.

Run these commands from the repository root in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\bridge-gui.exe
```

For later launches:

```powershell
.\.venv\Scripts\bridge-gui.exe
```

## Shortest Workflow

The primary workflow has four stages. Bridge manages task states, agent IDs, lease IDs,
attempt IDs, merge IDs, and commit discovery internally.

### 1. Connect a Project

On the **Project** tab, select the target Git repository, enter a project name, and click
**Connect Project**. The target project must:

- already be a Git repository;
- contain at least one commit;
- preferably have a clean working tree.

The recommended policy is `hybrid + balanced`. Bridge stores its database at
`.bridge/bridge.db` by default.

### 2. Save Agent Profiles

On the **Agent** tab, configure two distinct roles:

- a planning and review agent, such as Codex or GPT;
- an implementation agent, such as Reasonix.

Save the profiles. Bridge records internal IDs and permissions automatically. The
implementer and reviewer must be different agents. A third implementation agent is
optional.

### 3. Create and Run a Task

Open the **Tasks** tab, choose **New Task**, and enter:

- a title and objective;
- allowed paths, one per line;
- acceptance criteria, one per line;
- automated checks, such as `unit-tests`.

After selecting a task, the right panel shows one legal primary action at a time. The
normal sequence is:

```text
Prepare Task -> Assign and Start -> Detect Agent Submission -> Run Validation
-> Record Review Result -> Safe Merge
```

When execution starts, Bridge creates the lease, attempt, Git worktree, and task package,
then copies the implementation prompt to the clipboard. Use **Open Workspace** to open the
directory the implementation agent must use, or **Copy Prompt** to copy the instructions
again.

The implementation agent modifies only allowed paths, runs tests, creates a Git commit,
and updates `RECEIPT.md` and `ARTIFACTS.json` in the generated task package. The prompt
contains the exact package path.

When the agent finishes, choose **Detect Agent Submission**. Bridge reads the commit and
artifact files automatically; no commit hash or file picker is required.

### 4. Review and Merge

Choose **Run Validation**. Bridge runs checks in the task worktree rather than against
stale code in the main checkout. After validation passes, Bridge copies an independent
review prompt to the clipboard.

Give that prompt to the review agent, then record one of these results:

- **Approved**: the task becomes mergeable;
- **Revision Required**: Bridge preserves the evidence and offers a new attempt.

Choose **Safe Merge** and confirm. Bridge queues the candidate, integrates it in an
isolated worktree, updates the target branch, completes the task, closes the attempt and
lease, and removes a clean task worktree. If a step fails, Bridge preserves recoverable
state and displays the reason.

## Pages

- **Project**: connect a Git repository and configure progression and safety policies.
- **Agent**: maintain implementer and reviewer profiles.
- **Tasks**: manage the task list, progress, next action, and activity log.
- **Tools**: optional Markdown generation, LLM assistance, and custom pipelines.

The current desktop labels are Chinese. This guide uses English descriptions of the same
controls; generated collaboration documents support Chinese and English.

## Safety Rules

- `strict`: request confirmation for most write operations.
- `balanced`: automate low-risk steps and confirm higher-risk operations; recommended.
- `expert`: reduce confirmations without bypassing hard safety rules.
- Force pushes, default-branch deletion, out-of-scope writes, and secret leakage remain blocked.
- Validation and review evidence is bound to the current attempt.
- Merge integration uses an external worktree and never stashes or forcibly switches the user branch.
- Removing a dirty worktree always requires explicit confirmation.

## Validation Commands

Built-in checks include `unit-tests`, `lint`, `typecheck`, and `build`. New tasks default to
`unit-tests`. Custom commands must be registered through the safety gate; a task receipt
cannot replace the registered executable or arguments.

## Recovery

Reconnecting the same project restores tasks, leases, attempts, worktrees, reviews, and the
merge queue from SQLite. If an operation was interrupted, select the task and continue with
the suggested action. Do not manually delete managed worktrees or branches before examining
the recovery state.

## Development Verification

```powershell
python -m compileall -q bridgelib tests
python -m pytest -q
git diff --check
```

Some tests create real temporary Git repositories. Git must be available and the system
temporary directory must be writable.

## Uninstall

```powershell
.\.venv\Scripts\python.exe -m pip uninstall bridge-agent-coordinator
```

Uninstalling the Python package does not remove `.bridge/` databases or sibling
`.bridge-task-worktrees/` and `.bridge-integration-worktrees/` directories. Back up and
inspect those directories before removing runtime data manually.
