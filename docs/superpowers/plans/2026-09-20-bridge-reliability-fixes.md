# Bridge Reliability Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local multi-agent bridge reliable, fail-closed, persistent, and verifiable end to end without breaking existing user changes.

**Architecture:** Keep SQLite as the source of truth, make every agent-facing mutation transactional and identity-bound, and require provenance/validation evidence before submission. Preserve the existing coordinator abstractions while hardening Git, watcher, lifecycle, and packaging boundaries.

**Tech Stack:** Python 3.11+, SQLite, pytest, Git CLI adapter, MCP JSON-RPC server, Tk GUI, Ruff, Mypy, build/twine.

## Global Constraints

- Preserve all existing user modifications and public APIs unless a regression test requires a backward-compatible correction.
- Use SQLite transactions with rollback on every multi-step agent operation.
- Default to fail-closed for identity, allowed paths, commit provenance, artifact validation, and sensitive credential handling.
- Every production change must have a regression test that failed before the change.
- Keep tests deterministic and avoid network/model calls.

---

### Task 1: Receipt import idempotency and watcher retry

**Files:**
- Modify: `bridgelib/receipt_importer.py`
- Modify: `bridgelib/database.py`
- Modify: `bridgelib/file_watcher.py`
- Test: `tests/test_receipt_importer.py`, `tests/test_p0_p1_regression_v3.py`

**Interfaces:**
- Preserve `ReceiptImporter.scan_and_import(...) -> dict | None`.
- Preserve `FileWatcher` callbacks while retrying callbacks that raise.

- [ ] Write a concurrent duplicate-import test using two SQLite connections; assert one receipt row, no open transaction, and both connections remain writable.
- [ ] Run the focused test and observe the expected lock/unique failure.
- [ ] Add SQLite busy timeout/WAL configuration and make receipt insert an atomic `INSERT ... ON CONFLICT(content_hash) DO NOTHING`; rollback on every persistence exception.
- [ ] Move `_imported_hashes` marking after a successful insert and make watcher notifications occur only after callback success.
- [ ] Run focused receipt/watcher tests and confirm they pass.

### Task 2: Transactional MCP claim

**Files:**
- Modify: `bridgelib/mcp_server.py`
- Modify: `bridgelib/coordinator.py`
- Test: `tests/test_mcp_server.py`, `tests/test_p0_p1_regression_v3.py`

**Interfaces:**
- Preserve MCP `bridge/claim_task` response shape on success.
- On failure, leave task, lease, attempt, reviewer, and worktree state unchanged.

- [ ] Add a test injecting worktree creation failure and assert task remains claimable, no active lease/attempt remains, and no partial assignment persists.
- [ ] Run the test and observe partial state leakage.
- [ ] Wrap claim mutations in a database transaction/savepoint and explicitly compensate external worktree state when needed; rollback on any exception.
- [ ] Run claim tests and verify clean rollback.

### Task 3: Agent identity and lease authorization

**Files:**
- Modify: `bridgelib/mcp_server.py`
- Modify: `bridgelib/coordinator.py`
- Test: `tests/test_mcp_server.py`, `tests/test_p0_p1_regression_v3.py`

**Interfaces:**
- `report_progress` and `submit_task` must accept only the current task owner with an active lease/attempt.

- [ ] Add tests proving a non-owner cannot report progress, block a task, spend cost, or submit it.
- [ ] Run them and observe unauthorized mutations.
- [ ] Add one shared authorization helper that checks task owner, active lease agent, active attempt agent, and lease expiry.
- [ ] Apply it to progress, validation, and submit entry points.
- [ ] Run focused authorization tests.

### Task 4: Commit provenance, validation, and artifact gates

**Files:**
- Modify: `bridgelib/mcp_server.py`
- Modify: `bridgelib/receipt_importer.py`
- Modify: `bridgelib/git_adapter.py`
- Test: `tests/test_mcp_server.py`, `tests/test_e2e_real_experience.py`

**Interfaces:**
- Submission must use a full commit SHA that is reachable from the current attempt branch and based on the recorded base commit.
- Submission artifacts must contain changed files, required checks, and pass `validate_artifacts` before state becomes `SUBMITTED`.

- [ ] Add tests for symbolic refs, unrelated commits, missing required checks, and changed-file scope violations.
- [ ] Run focused tests and observe current fail-open behavior.
- [ ] Enforce full SHA/provenance, run required validation checks, validate artifacts, and reject Git/diff failures instead of substituting `README.md`.
- [ ] Run focused MCP end-to-end tests.

### Task 5: Merge conflict and project/worktree isolation

**Files:**
- Modify: `bridgelib/coordinator.py`
- Modify: `bridgelib/workspace.py`
- Modify: `bridgelib/utils.py`
- Test: `tests/test_merge.py`, `tests/test_workspace.py`, `tests/test_e2e_real_experience.py`

**Interfaces:**
- Git cherry-pick conflicts remain `CONFLICT` in memory and SQLite.
- Worktree paths are unique across projects for identical agent/task IDs.

- [ ] Add a real temporary Git conflict test and a two-project same-ID worktree test.
- [ ] Run them and observe queued fallback/path collision.
- [ ] Preserve conflict state in exception handling and include a stable sanitized project identifier in worktree paths/branch names.
- [ ] Run focused merge/workspace tests.

### Task 6: Secure worktree configuration and path scope

**Files:**
- Modify: `bridgelib/coordinator.py`
- Modify: `bridgelib/mcp_server.py`
- Test: `tests/test_coordinator.py`, `tests/test_mcp_server.py`

**Interfaces:**
- Sensitive files are never copied by default.
- MCP task creation/claim rejects missing allowed paths unless an explicit user confirmation policy opts in.

- [ ] Add tests for `.env`, `.env.local`, `.env.test`, `.npmrc`, `.pypirc` exclusion and empty allowed paths rejection.
- [ ] Run them and observe current copy/fail-open behavior.
- [ ] Add an explicit allowlist/opt-in for configuration inheritance and remove automatic `['**']`/generic acceptance criteria injection.
- [ ] Run focused security/scope tests.

### Task 7: Cost tracking and receipt-watcher lifecycle

**Files:**
- Modify: `bridgelib/coordinator.py`
- Modify: `bridgelib/cost.py`
- Modify: `bridgelib/file_watcher.py`
- Test: `tests/test_cost.py`, `tests/test_coordinator.py`

**Interfaces:**
- Receipt import records estimated costs without swallowing signature errors.
- Stop/start creates a fresh watcher/importer bound to the current database.

- [ ] Add tests for estimated cost import and watcher restart database identity.
- [ ] Run them and observe zero cost records/reused importer.
- [ ] Align `record_task_cost` parameters and clear watcher/importer references during stop.
- [ ] Run focused cost/lifecycle tests.

### Task 8: MCP startup, summary resource, migration, lock, and shutdown hardening

**Files:**
- Modify: `bridgelib/mcp_server.py`
- Modify: `bridgelib/database.py`
- Modify: `bridgelib/gui.py`
- Modify: `bridgelib/coordinator.py`
- Test: `tests/test_mcp_server.py`, `tests/test_database.py`, `tests/test_gui_coordinator_workflows.py`

**Interfaces:**
- Default `.bridge/bridge.db` startup creates its parent directory.
- MCP project summary uses fields present in `ProjectSummary`.
- Migration failures abort startup and release locks/resources on shutdown.

- [ ] Add tests for empty-directory startup, summary resource retrieval, migration failure, lock acquisition, and close lifecycle.
- [ ] Run them and observe current failures/leaks.
- [ ] Implement parent-directory creation, summary field correction, strict migration failure handling, startup lock integration, and unified close hooks.
- [ ] Run focused lifecycle tests.

### Task 9: Static quality, version consistency, and user documentation

**Files:**
- Modify: `bridgelib/coordinator.py`
- Modify: `pyproject.toml`
- Modify: `packaging/windows-version.txt`
- Modify: `agent bridge/pyproject.toml`
- Modify: `README.md`, `README.zh-CN.md`, `docs/USER_GUIDE.md`, `docs/USER_GUIDE.en.md`
- Test/verify: Ruff, Mypy, build, twine, diff check

**Interfaces:**
- No undefined logger or nonexistent database method calls remain.
- Package and MCP versions are documented and consistent.

- [ ] Add/adjust tests for `get_task_diff` receipt lookup and importability.
- [ ] Run targeted checks to capture current failures.
- [ ] Fix logger/database API usage, type errors, formatting, and version/documentation drift without unrelated refactors.
- [ ] Run all static/build checks.

### Task 10: Complete end-to-end acceptance suite

**Files:**
- Create: `tests/test_e2e_bridge_lifecycle.py`
- Modify: `tests/test_e2e_real_experience.py` if reusable fixtures are needed

**Interfaces:**
- Exercise the public coordinator/MCP flows, not private implementation details.

- [ ] Write tests for claim → worktree → progress → validation → submit → review/merge, concurrent receipt imports, conflicts, multi-project isolation, unauthorized agents, invalid commits, watcher restart, and empty-directory MCP startup.
- [ ] Run the new suite and fix only production defects exposed by it.
- [ ] Run the final full verification command set and record exact results.

---

## Final Verification Commands

```powershell
python -m pytest -q
python -m compileall -q bridgelib tests
python -m pip check
python -m ruff check bridgelib tests bridge.py
python -m mypy bridgelib
git diff --check
python -m build
python -m twine check dist/*
```
