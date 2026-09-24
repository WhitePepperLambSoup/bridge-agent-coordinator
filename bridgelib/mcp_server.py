"""Model Context Protocol (MCP) Server for Bridge Coordinator.

Enables AI coding agents (Cursor, Claude Code, Windsurf, Roo Code, etc.)
to autonomously inspect, claim, execute, test, and submit Bridge tasks
without manual clipboard copy-pasting.

Protocol Standard: MCP 2024-11-05 (JSON-RPC 2.0 over stdio)
"""

import sys
import os
import json
import logging
import traceback
from typing import Any, Callable, Dict, List, Optional

from datetime import datetime, timezone
from bridgelib.database import Database, init_database
from bridgelib.coordinator import BridgeCoordinator, CoordinatorError
from bridgelib.state_machine import TaskState
from bridgelib.protocol import ReceiptStatus, parse_receipt


logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "bridge-agent-coordinator"
SERVER_VERSION = "0.2.0"


class BridgeMCPServer:
    """Standard MCP Server exposing Bridge Coordinator workflows to AI coding tools."""

    def __init__(self, coordinator: BridgeCoordinator, default_project_id: Optional[str] = None):
        self.coordinator = coordinator
        self.db = coordinator.db
        # Keep the MCP server scoped to one project.  Do not inherit the
        # coordinator active-project pointer when no project was configured:
        # that pointer is mutable and could move the server boundary.
        self.default_project_id = default_project_id or ""
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.tool_handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        self._register_tools()

    def close(self):
        """Release coordinator resources owned by this server."""
        self.coordinator.close()

    def _resolve_project_id(self, project_id: Optional[str] = None) -> str:
        if not self.default_project_id:
            projects = self.db.list_projects()
            if projects:
                self.default_project_id = projects[0]["id"]

        if project_id and self.default_project_id and project_id != self.default_project_id:
            raise CoordinatorError(
                f"Project '{project_id}' is outside this MCP server's configured project"
            )
        return self.default_project_id or ""

    @staticmethod
    def _token_count(args: Dict[str, Any], key: str) -> int:
        """Parse a token count before any handler side effect occurs."""
        raw = args.get(key, 0)
        if isinstance(raw, bool):
            raise CoordinatorError(f"{key} must be a non-negative integer")
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise CoordinatorError(f"{key} must be a non-negative integer") from exc
        if value < 0:
            raise CoordinatorError(f"{key} must be non-negative")
        return value

    def _require_project_task(self, task_id: str) -> dict:
        """Load a task and enforce this server's project boundary."""
        task = self.coordinator.get_task(task_id)
        if not task:
            raise CoordinatorError(f"Task '{task_id}' not found")
        project_id = self._resolve_project_id()
        if project_id and task.get("project_id") != project_id:
            raise CoordinatorError(
                f"Task '{task_id}' belongs to project '{task.get('project_id', '')}', "
                f"not configured project '{project_id}'"
            )
        return task

    def _register_tool(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable[[Dict[str, Any]], Any],
    ):
        self.tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": parameters,
        }
        self.tool_handlers[name] = handler

    def _register_tools(self):
        # 1. bridge_list_tasks
        self._register_tool(
            name="bridge_list_tasks",
            description="List tasks managed by Bridge. Supports filtering by state (ready, assigned, in_progress, submitted, approved, etc.) and project.",
            parameters={
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "description": "Optional state filter: ready, assigned, in_progress, submitted, approved, done, blocked",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Optional project ID. If omitted, uses active project.",
                    },
                },
            },
            handler=self._handle_list_tasks,
        )

        # 2. bridge_get_task
        self._register_tool(
            name="bridge_get_task",
            description="Get complete task details including goal, acceptance criteria, required checks, allowed/forbidden paths, active attempt, and worktree info.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The unique ID of the task to retrieve",
                    }
                },
                "required": ["task_id"],
            },
            handler=self._handle_get_task,
        )

        # 3. bridge_claim_task
        self._register_tool(
            name="bridge_claim_task",
            description="Claim a task for execution. Acquires a lease, assigns the agent, creates an attempt, creates a Git worktree, and sets state to in_progress.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The ID of the task to claim",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "The agent ID claiming the task (e.g. cursor, claude-code, windsurf)",
                    },
                    "reviewer_id": {
                        "type": "string",
                        "description": "Optional reviewer agent ID. If omitted, uses auto-routing recommendation.",
                    },
                },
                "required": ["task_id", "agent_id"],
            },
            handler=self._handle_claim_task,
        )

        # 4. bridge_report_progress
        self._register_tool(
            name="bridge_report_progress",
            description="Report execution progress and token usage for an in-progress task.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "The agent ID reporting progress",
                    },
                    "status": {
                        "type": "string",
                        "description": "Status update: progress, blocked",
                        "enum": ["progress", "blocked"],
                    },
                    "notes": {
                        "type": "string",
                        "description": "Description of work done so far or blocking reason",
                    },
                    "input_tokens": {
                        "type": "integer",
                        "description": "Estimated or exact input tokens consumed",
                    },
                    "output_tokens": {
                        "type": "integer",
                        "description": "Estimated or exact output tokens generated",
                    },
                },
                "required": ["task_id", "agent_id"],
            },
            handler=self._handle_report_progress,
        )

        # 5. bridge_run_validation
        self._register_tool(
            name="bridge_run_validation",
            description="Run required validation checks (e.g. unit tests, linters) in the task's worktree for the current task owner.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to validate",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "The agent ID running validation",
                    },
                },
                "required": ["task_id", "agent_id"],
            },
            handler=self._handle_run_validation,
        )

        # 6. bridge_submit_task
        self._register_tool(
            name="bridge_submit_task",
            description="Submit completed task attempt. Generates RECEIPT.md and ARTIFACTS.json, imports them into Bridge, and transitions task to submitted state.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "The agent ID submitting the task",
                    },
                    "submission_commit": {
                        "type": "string",
                        "description": "Git commit SHA of the changes in the worktree",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Summary of changes and implementation details",
                    },
                    "test_summary": {
                        "type": "string",
                        "description": "Summary of tests executed and results",
                    },
                    "input_tokens": {
                        "type": "integer",
                        "description": "Final total input tokens consumed",
                    },
                    "output_tokens": {
                        "type": "integer",
                        "description": "Final total output tokens generated",
                    },
                },
                "required": ["task_id", "agent_id", "submission_commit"],
            },
            handler=self._handle_submit_task,
        )

        # 7. bridge_get_ai_pre_review
        self._register_tool(
            name="bridge_get_ai_pre_review",
            description="Run AI pre-review on the current task worktree/submission, checking against criteria, forbidden paths, and code hygiene.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to review",
                    }
                },
                "required": ["task_id"],
            },
            handler=self._handle_get_ai_pre_review,
        )

        # 8. bridge_diagnose_failure
        self._register_tool(
            name="bridge_diagnose_failure",
            description="Run automated failure diagnosis on failed checks/tests, generating root-cause analysis and remediation instructions.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to diagnose",
                    }
                },
                "required": ["task_id"],
            },
            handler=self._handle_diagnose_failure,
        )

        # 9. bridge_add_dependency
        self._register_tool(
            name="bridge_add_dependency",
            description="Add a blocking DAG dependency between two tasks (task_id depends on depends_on_task_id). Validates for cycles.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The downstream task ID that will be blocked",
                    },
                    "depends_on_task_id": {
                        "type": "string",
                        "description": "The upstream task ID that must complete first",
                    },
                },
                "required": ["task_id", "depends_on_task_id"],
            },
            handler=self._handle_add_dependency,
        )

        # 10. bridge_get_dag_plan
        self._register_tool(
            name="bridge_get_dag_plan",
            description="Get the multi-stage topological execution layers of project tasks for parallel and serial agent coordination.",
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project ID. Defaults to active project.",
                    }
                },
            },
            handler=self._handle_get_dag_plan,
        )

        # 11. bridge_get_task_diff
        self._register_tool(
            name="bridge_get_task_diff",
            description="Inspect the full Git diff, files modified, and shortstat summary for a task attempt.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to inspect",
                    }
                },
                "required": ["task_id"],
            },
            handler=self._handle_get_task_diff,
        )

    # ── Tool Implementations ─────────────────────────────────



    def _handle_list_tasks(self, args: Dict[str, Any]) -> Dict[str, Any]:
        state = args.get("state")
        tasks = self.coordinator.list_tasks(state=state)
        # Filter by project_id if provided
        pid = self._resolve_project_id(args.get("project_id"))
        if pid:
            tasks = [t for t in tasks if t.get("project_id") == pid]

        results = []
        for t in tasks:
            results.append({
                "id": t["id"],
                "title": t.get("title", ""),
                "state": t.get("state", ""),
                "priority": t.get("priority", 0),
                "risk": t.get("risk", "low"),
                "owner_agent_id": t.get("owner_agent_id", ""),
                "reviewer_agent_id": t.get("reviewer_agent_id", ""),
                "created_at": t.get("created_at", ""),
            })
        return {"tasks": results, "count": len(results)}

    def _handle_get_task(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_id = args["task_id"]
        task = self._require_project_task(task_id)

        current_attempt = self.coordinator.get_current_attempt(task_id)
        worktree = self.coordinator.get_worktree(task_id)
        validations = self.db.list_validations_by_task(task_id)

        try:
            allowed_paths = json.loads(task.get("allowed_paths_json") or "[]")
        except Exception:
            allowed_paths = []
        try:
            forbidden_paths = json.loads(task.get("forbidden_paths_json") or "[]")
        except Exception:
            forbidden_paths = []
        try:
            acceptance_criteria = json.loads(task.get("acceptance_criteria_json") or "[]")
        except Exception:
            acceptance_criteria = []
        try:
            required_checks = json.loads(task.get("required_checks_json") or "[]")
        except Exception:
            required_checks = []

        context_summary = ""
        try:
            context_summary = self.coordinator.generate_task_context(task_id)
        except Exception:
            pass

        return {
            "task": {
                "id": task["id"],
                "project_id": task.get("project_id", ""),
                "title": task.get("title", ""),
                "goal": task.get("goal", ""),
                "state": task.get("state", ""),
                "risk": task.get("risk", "low"),
                "complexity": task.get("complexity", "medium"),
                "cost_tier": task.get("cost_tier", "low"),
                "owner_agent_id": task.get("owner_agent_id", ""),
                "reviewer_agent_id": task.get("reviewer_agent_id", ""),
                "allowed_paths": allowed_paths,
                "forbidden_paths": forbidden_paths,
                "acceptance_criteria": acceptance_criteria,
                "required_checks": required_checks,
            },
            "current_attempt": current_attempt,
            "worktree": worktree,
            "validation_results": validations,
            "context_summary": context_summary,
        }

    def _handle_claim_task(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_id = args["task_id"]
        agent_id = args["agent_id"]
        reviewer_id = args.get("reviewer_id", "")

        task = self._require_project_task(task_id)
        project_id = task.get("project_id", "")

        # A task package must carry an explicit scope and acceptance contract.
        # Never silently widen a missing scope to the entire repository.
        try:
            allowed = json.loads(task.get("allowed_paths_json") or "[]")
            criteria = json.loads(task.get("acceptance_criteria_json") or "[]")
        except (TypeError, json.JSONDecodeError) as exc:
            raise CoordinatorError(f"Task {task_id} has invalid scope metadata: {exc}") from exc
        if not allowed:
            raise CoordinatorError(
                f"Task {task_id} must declare at least one allowed_path before claim"
            )
        if not criteria:
            raise CoordinatorError(
                f"Task {task_id} must declare at least one acceptance_criteria before claim"
            )

        original_task = dict(task)
        original_lease_ids = {row["id"] for row in self.db.list_leases_by_task(task_id)}
        original_attempt_ids = {row["id"] for row in self.coordinator.list_attempts(task_id)}
        original_workspace_ids = {
            row["id"]
            for row in self.db.conn.execute(
                "SELECT id FROM workspaces WHERE task_id = ?", (task_id,)
            ).fetchall()
        }
        original_agent_ids = {row["id"] for row in self.coordinator.list_agents(project_id)}
        created_agent_ids: set[str] = set()

        try:
            # 1. Ensure claiming agent is registered.
            agent = self.coordinator.get_agent(agent_id)
            if not agent:
                aid = self.coordinator.add_agent(
                    project_id=project_id,
                    display_name=agent_id,
                    id=agent_id,
                    role="implementer",
                )
                agent_id = aid
                if aid not in original_agent_ids:
                    created_agent_ids.add(aid)

            # 2. Pick reviewer if not specified.
            if not reviewer_id:
                reviewer_id = task.get("reviewer_agent_id", "")
                if not reviewer_id or reviewer_id == agent_id:
                    rec = self.coordinator.recommend_agent_for_task(task_id, role="reviewer")
                    recommended = rec.get("recommended_agent_id")
                    if recommended and recommended != agent_id:
                        reviewer_id = recommended
                    else:
                        agents = self.coordinator.list_agents(project_id)
                        # The fallback must apply the same hard permission
                        # filter as routing.  Picking an arbitrary agent can
                        # select an implementer without ``can_review`` and
                        # make an otherwise valid claim fail during assignment.
                        cand = []
                        for candidate in agents:
                            if candidate.get("id") == agent_id or not candidate.get("enabled", 1):
                                continue
                            try:
                                permissions = json.loads(
                                    candidate.get("permissions_json", "{}") or "{}"
                                )
                            except (TypeError, json.JSONDecodeError):
                                permissions = {}
                            if permissions.get("can_review"):
                                cand.append(candidate["id"])
                        if cand:
                            reviewer_id = cand[0]
                        else:
                            reviewer_id = f"reviewer-{agent_id}"
                            self.coordinator.add_agent(
                                project_id=project_id,
                                display_name="Bridge Reviewer",
                                id=reviewer_id,
                                role="reviewer",
                                can_review=True,
                            )
                            created_agent_ids.add(reviewer_id)

            # 3. Transition to ready following the state machine.
            current_state = task.get("state")
            if current_state in (TaskState.DRAFT.value, TaskState.PLANNING.value):
                if current_state == TaskState.DRAFT.value:
                    self.coordinator.transition_task(
                        task_id, TaskState.PLANNING, actor="mcp", confirmed=True
                    )
                self.coordinator.transition_task(
                    task_id, TaskState.READY, actor="mcp", confirmed=True
                )
                refreshed_task = self.coordinator.get_task(task_id)
                if refreshed_task is None:
                    raise CoordinatorError(f"Task '{task_id}' disappeared during claim")
                task = refreshed_task

            # 4. Assign task if ready.
            if task and task.get("state") == TaskState.READY.value:
                self.coordinator.assign_task(
                    task_id, owner_agent_id=agent_id, reviewer_agent_id=reviewer_id,
                    actor="mcp", confirmed=True,
                )

            # 5. Acquire lease, create attempt, and worktree.
            lease = self.coordinator.acquire_lease(task_id, agent_id, ttl_seconds=7200)
            attempt_id = self.coordinator.create_attempt(task_id, agent_id, lease.lease_id)
            attempt = self.coordinator.get_current_attempt(task_id)
            attempt_num = attempt["attempt_number"] if attempt else 1
            git_status = self.coordinator.check_git_repo(project_id)
            head_commit = git_status.get("head_commit", "")
            ws = self.coordinator.create_worktree(
                task_id, agent_id, attempt=attempt_num,
                base_commit=head_commit, confirmed=True,
            )

            # 6. Transition to IN_PROGRESS only after all resources exist.
            self.coordinator.transition_task(
                task_id, TaskState.IN_PROGRESS, actor=agent_id, confirmed=True,
            )

            return {
                "status": "claimed",
                "task_id": task_id,
                "attempt_id": attempt_id,
                "attempt_number": attempt_num,
                "lease_id": lease.lease_id,
                "worktree_path": ws.get("worktree_path", ""),
                "branch": ws.get("branch", ""),
                "base_commit": head_commit,
            }
        except Exception as exc:
            # Coordinator methods commit independently, so compensate all records
            # created by this claim instead of relying on one outer transaction.
            new_workspaces = self.db.conn.execute(
                "SELECT id, worktree_path, branch FROM workspaces WHERE task_id = ?",
                (task_id,),
            ).fetchall()
            for row in new_workspaces:
                if row["id"] in original_workspace_ids:
                    continue
                path = row["worktree_path"] or ""
                if path and os.path.isdir(path):
                    project = self.db.get_project(project_id) or {}
                    root = project.get("root_path", "")
                    if root:
                        try:
                            from bridgelib.git_adapter import GitRepositoryAdapter
                            adapter = GitRepositoryAdapter(root)
                            adapter._run(["git", "worktree", "remove", "--force", path])
                            if row["branch"]:
                                adapter._run(["git", "branch", "-D", row["branch"]])
                        except Exception:
                            logger.exception("Failed to clean up claim worktree %s", path)
                self.db.conn.execute("DELETE FROM workspaces WHERE id = ?", (row["id"],))

            current_attempt_ids = {
                row["id"] for row in self.coordinator.list_attempts(task_id)
            }
            current_lease_ids = {
                row["id"] for row in self.db.list_leases_by_task(task_id)
            }
            for lease_id in current_lease_ids - original_lease_ids:
                self.db.conn.execute("DELETE FROM leases WHERE id = ?", (lease_id,))
                self.coordinator.leases._leases.pop(lease_id, None)
            for attempt_id in current_attempt_ids - original_attempt_ids:
                self.db.conn.execute("DELETE FROM attempts WHERE id = ?", (attempt_id,))
            self.db.conn.execute(
                """UPDATE tasks SET state = ?, owner_agent_id = ?,
                   reviewer_agent_id = ?, version = ? WHERE id = ?""",
                (
                    original_task.get("state"),
                    original_task.get("owner_agent_id"),
                    original_task.get("reviewer_agent_id"),
                    original_task.get("version", 1),
                    task_id,
                ),
            )
            for aid in created_agent_ids:
                self.db.conn.execute("DELETE FROM agent_profiles WHERE id = ?", (aid,))
            self.db.conn.commit()
            current_ws = self.coordinator.workspaces.get_by_task(task_id)
            if current_ws and current_ws.workspace_id not in original_workspace_ids:
                self.coordinator.workspaces._workspaces.pop(current_ws.workspace_id, None)
                self.coordinator.workspaces._by_task.pop(task_id, None)
            raise CoordinatorError(f"Task claim failed and was rolled back: {exc}") from exc

    def _authorize_active_agent(self, task_id: str, agent_id: str) -> tuple[dict, dict, dict]:
        """Require the caller to own the task and its current lease/attempt."""
        task = self._require_project_task(task_id)
        if task.get("owner_agent_id") != agent_id:
            raise CoordinatorError(
                f"Agent '{agent_id}' is not authorized for task '{task_id}'"
            )
        attempt = self.coordinator.get_current_attempt(task_id)
        if not attempt or attempt.get("agent_id") != agent_id:
            raise CoordinatorError(
                f"Agent '{agent_id}' has no active attempt for task '{task_id}'"
            )
        lease_id = attempt.get("lease_id") or ""
        lease = self.db.get_lease(lease_id) if lease_id else None
        if not lease or lease.get("status") != "active" or lease.get("agent_id") != agent_id:
            raise CoordinatorError(
                f"Agent '{agent_id}' has no active lease for task '{task_id}'"
            )
        expires_at = lease.get("expires_at") or ""
        try:
            if expires_at and datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc):
                raise CoordinatorError(f"Lease '{lease_id}' has expired")
        except ValueError as exc:
            raise CoordinatorError(f"Lease '{lease_id}' has invalid expiry") from exc
        return task, attempt, lease

    def _record_submission_token_delta(
        self,
        task_id: str,
        agent_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> dict[str, int]:
        """Record only the unreported portion of final cumulative MCP usage.

        ``bridge_report_progress`` accepts incremental usage, while
        ``bridge_submit_task`` accepts final cumulative totals.  Keeping the
        two sources separate makes the audit trail explicit and lets retries
        remain idempotent: a repeated submission only records any newly
        observed increase.
        """
        if input_tokens < 0 or output_tokens < 0:
            raise CoordinatorError("Token counts must be non-negative")

        prior_mcp = self.coordinator.costs.records_by_task(task_id)
        recorded_input = sum(
            record.input_tokens
            for record in prior_mcp
            if record.agent_id == agent_id and record.source in {"mcp", "mcp_submit"}
        )
        recorded_output = sum(
            record.output_tokens
            for record in prior_mcp
            if record.agent_id == agent_id and record.source in {"mcp", "mcp_submit"}
        )
        delta_input = max(0, input_tokens - recorded_input)
        delta_output = max(0, output_tokens - recorded_output)
        if delta_input or delta_output:
            self.coordinator.record_task_cost(
                task_id=task_id,
                agent_id=agent_id,
                input_tokens=delta_input,
                output_tokens=delta_output,
                source="mcp_submit",
            )
        return {
            "input_tokens": delta_input,
            "output_tokens": delta_output,
        }

    def _rollback_submission_records(
        self,
        task_id: str,
        receipt_ids_before: set[str],
        cost_ids_before: set[str],
        cost_object_ids_before: set[int],
    ) -> None:
        """Remove side effects created by a failed final submission step.

        Receipt import and token accounting each commit independently of the
        subsequent task transition.  If that transition fails, leave the task
        in a retryable state without stale receipt/cost rows or in-memory cost
        records from the abandoned submission.
        """
        try:
            receipt_rows = self.db.list_receipts(task_id)
            new_receipt_ids = [
                row["id"] for row in receipt_rows if row.get("id") not in receipt_ids_before
            ]
            cost_rows = self.db.conn.execute(
                "SELECT id FROM cost_records WHERE task_id = ?", (task_id,)
            ).fetchall()
            new_cost_ids = [
                row["id"] for row in cost_rows if row["id"] not in cost_ids_before
            ]
            if new_receipt_ids or new_cost_ids:
                if new_receipt_ids:
                    self.db.conn.executemany(
                        "DELETE FROM receipts WHERE id = ?",
                        [(receipt_id,) for receipt_id in new_receipt_ids],
                    )
                if new_cost_ids:
                    self.db.conn.executemany(
                        "DELETE FROM cost_records WHERE id = ?",
                        [(cost_id,) for cost_id in new_cost_ids],
                    )
                self.db.conn.commit()

            for record in list(self.coordinator.costs.records_by_task(task_id)):
                if id(record) not in cost_object_ids_before:
                    self.coordinator.costs.discard(record)
        except Exception:
            # Preserve the original submission error.  The database remains
            # usable because any failed cleanup transaction is rolled back.
            try:
                self.db.conn.rollback()
            except Exception:
                pass
            logger.exception("Failed to roll back MCP submission side effects for %s", task_id)

    def _handle_report_progress(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_id = args["task_id"]
        agent_id = args["agent_id"]
        status = args.get("status", "progress")
        notes = args.get("notes", "")
        if status not in {"progress", "blocked"}:
            raise CoordinatorError(
                "status must be one of progress or blocked"
            )
        in_tokens = self._token_count(args, "input_tokens")
        out_tokens = self._token_count(args, "output_tokens")

        task, _attempt, _lease = self._authorize_active_agent(task_id, agent_id)
        if task.get("state") != TaskState.IN_PROGRESS.value:
            raise CoordinatorError(
                f"Task '{task_id}' must be in_progress to report progress, "
                f"got {task.get('state')}"
            )

        if in_tokens or out_tokens:
            self.coordinator.record_task_cost(
                task_id=task_id,
                agent_id=agent_id,
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                source="mcp",
            )

        if status == "blocked":
            self.coordinator.record_task_failure(
                task_id, f"Agent reported blocked: {notes}"
            )
            self.coordinator.transition_task(
                task_id, TaskState.BLOCKED, actor=agent_id, confirmed=True
            )

        return {
            "task_id": task_id,
            "status": status,
            "notes": notes,
            "recorded": True,
        }

    def _handle_run_validation(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_id = args["task_id"]
        agent_id = args["agent_id"]
        task, _attempt, _lease = self._authorize_active_agent(task_id, agent_id)
        if task.get("state") != TaskState.IN_PROGRESS.value:
            raise CoordinatorError(
                f"Task '{task_id}' must be in_progress to run validation, "
                f"got {task.get('state')}"
            )

        required_checks = json.loads(task.get("required_checks_json") or "[]")
        if not required_checks:
            required_checks = ["unit-tests"]

        checks = [{"check_id": cid} for cid in required_checks]
        results = self.coordinator.run_validation(task_id, checks)
        all_passed = all(r.get("status") == "passed" for r in results)
        return {
            "task_id": task_id,
            "all_passed": all_passed,
            "results": results,
        }

    def _handle_submit_task(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_id = args["task_id"]
        agent_id = args["agent_id"]
        submission_commit = args["submission_commit"]
        summary = args.get("summary", "Task implementation complete")
        test_summary = args.get("test_summary", "Tests verified")
        in_tokens = self._token_count(args, "input_tokens")
        out_tokens = self._token_count(args, "output_tokens")

        task, attempt, lease = self._authorize_active_agent(task_id, agent_id)
        if task.get("state") == TaskState.SUBMITTED.value:
            # MCP clients may retry after losing the first response.  Once a
            # completed receipt was imported for this attempt, return the
            # original result without writing another receipt, cost row, or
            # state transition.  A different commit is still rejected.
            existing_ws = self.coordinator.get_worktree(task_id) or {}
            worktree_path = existing_ws.get("worktree_path", "")
            project = self.db.get_project(task.get("project_id", "")) or {}
            root_path = project.get("root_path", "")
            if not worktree_path or not os.path.isdir(worktree_path):
                raise CoordinatorError(
                    f"Cannot verify idempotent submission for task '{task_id}': worktree unavailable"
                )
            from bridgelib.git_adapter import GitRepositoryAdapter, GitAdapterError
            try:
                adapter = GitRepositoryAdapter(root_path)
                valid, reason, canonical_commit = adapter.verify_commit_provenance(
                    existing_ws.get("base_commit", ""), submission_commit, worktree_path
                )
            except GitAdapterError as exc:
                raise CoordinatorError(
                    f"Git submission verification failed: {exc}"
                ) from exc
            if not valid:
                raise CoordinatorError(f"Invalid submission commit provenance: {reason}")

            existing_receipt = next(
                (
                    row for row in self.db.list_receipts(task_id)
                    if row.get("attempt_id") == attempt.get("id")
                    and row.get("agent_id") == agent_id
                    and "status=completed" in (row.get("import_result") or "")
                ),
                None,
            )
            if existing_receipt is None:
                raise CoordinatorError(
                    f"Task '{task_id}' is submitted but has no completed receipt for the current attempt"
                )
            return {
                "status": "submitted",
                "task_id": task_id,
                "submission_commit": canonical_commit,
                "receipt_imported": {
                    "receipt_id": existing_receipt.get("id", ""),
                    "status": "completed",
                    "idempotent": True,
                },
                "recorded_tokens": {"input_tokens": 0, "output_tokens": 0},
                "package_dir": f"{worktree_path}.bridge-task-a{attempt['attempt_number']}",
                "idempotent": True,
            }
        if task.get("state") != TaskState.IN_PROGRESS.value:
            raise CoordinatorError(
                f"Task '{task_id}' must be in_progress before submit, got {task.get('state')}"
            )

        workspace = self.coordinator.get_worktree(task_id) or {}
        worktree_path = workspace.get("worktree_path", "")
        if not worktree_path or not os.path.isdir(worktree_path):
            raise CoordinatorError(f"Worktree directory not accessible for task '{task_id}'")

        project = self.db.get_project(task.get("project_id", "")) or {}
        root_path = project.get("root_path", "")
        if not root_path or not os.path.isdir(root_path):
            raise CoordinatorError(f"Project root is not accessible for task '{task_id}'")
        from bridgelib.git_adapter import GitRepositoryAdapter, GitAdapterError
        try:
            adapter = GitRepositoryAdapter(root_path)
            if not adapter.check_repo().is_repo:
                raise CoordinatorError("Submission requires a Git repository")
            base_commit = workspace.get("base_commit", "")
            if not base_commit:
                raise CoordinatorError("Current attempt has no recorded base_commit")
            valid, reason, canonical_commit = adapter.verify_commit_provenance(
                base_commit, submission_commit, worktree_path
            )
            if not valid:
                raise CoordinatorError(f"Invalid submission commit provenance: {reason}")
            submission_commit = canonical_commit
            diff = adapter.get_diff(base_commit, submission_commit)
            if diff.error:
                raise CoordinatorError(f"Cannot inspect submission diff: {diff.error}")
            if not diff.files_changed:
                raise CoordinatorError("Submission must contain at least one changed file")
            changed_files = [{"path": path, "change": "modified"} for path in diff.files_changed]
        except GitAdapterError as exc:
            raise CoordinatorError(f"Git submission verification failed: {exc}") from exc

        # Required checks must have a passed result for this attempt.  If the
        # agent has not run them yet, execute the registered commands now.
        try:
            required_check_ids = json.loads(task.get("required_checks_json") or "[]")
        except (TypeError, json.JSONDecodeError) as exc:
            raise CoordinatorError(f"Task has invalid required_checks metadata: {exc}") from exc
        current_validations = [
            row for row in self.db.list_validations_by_task(task_id)
            if row.get("attempt_id") == attempt.get("id")
        ]
        passed_ids = {row.get("check_id") for row in current_validations if row.get("status") == "passed"}
        missing_checks = [cid for cid in required_check_ids if cid not in passed_ids]
        if missing_checks:
            self.coordinator.run_validation(
                task_id,
                [{"check_id": cid, "required": True} for cid in missing_checks],
            )
            current_validations = [
                row for row in self.db.list_validations_by_task(task_id)
                if row.get("attempt_id") == attempt.get("id")
            ]
            passed_ids = {row.get("check_id") for row in current_validations if row.get("status") == "passed"}
            missing_checks = [cid for cid in required_check_ids if cid not in passed_ids]
            if missing_checks:
                raise CoordinatorError(
                    f"Required validation checks did not pass: {', '.join(missing_checks)}"
                )

        # 1. Generate the protocol package beside (not inside) the worktree.
        # Protocol files are coordinator metadata and must never dirty the
        # agent's Git branch or affect commit provenance.
        package_dir = f"{worktree_path}.bridge-task-a{attempt['attempt_number']}"
        os.makedirs(package_dir, exist_ok=True)

        now_iso = datetime.now(timezone.utc).isoformat()
        lease_id = attempt.get("lease_id") or ""
        checks = [
            {"id": row.get("check_id", ""), "status": row.get("status", "")}
            for row in current_validations
            if row.get("check_id")
        ]

        # 2. Write RECEIPT.md
        receipt_content = f"""---
protocol_version: 1
task_id: {task_id}
attempt: {attempt['attempt_number']}
lease_id: {lease_id}
agent_id: {agent_id}
status: completed
submission_commit: '{submission_commit}'
completed_at: '{now_iso}'
---

# Completed Receipt

## Summary
{summary}

## Test Summary
{test_summary}
"""
        receipt_path = os.path.join(package_dir, "RECEIPT.md")
        with open(receipt_path, "w", encoding="utf-8") as f:
            f.write(receipt_content)

        # 3. Write ARTIFACTS.json
        artifacts_content = json.dumps({
            "protocol_version": 1,
            "task_id": task_id,
            "attempt": attempt["attempt_number"],
            "agent_id": agent_id,
            "base_commit": base_commit,
            "submission_commit": submission_commit,
            "changed_files": changed_files,
            "checks": checks,
            "evidence_files": [],
            "new_dependencies": [],
            "migrations": [],
            "known_failures": [],
            "generated_at": now_iso,
        }, indent=2)
        artifacts_path = os.path.join(package_dir, "ARTIFACTS.json")
        with open(artifacts_path, "w", encoding="utf-8") as f:
            f.write(artifacts_content)

        artifact_result = self.coordinator.validate_artifacts(task_id, artifacts_path)
        if not artifact_result.get("valid"):
            issues = "; ".join(artifact_result.get("issues", []))
            raise CoordinatorError(f"Artifact validation failed: {issues}")

        # 4-5. Import, account, and transition as one compensating operation.
        # These subsystems use independent commits, so explicitly undo records
        # created in this attempt if a later step fails.
        receipt_ids_before = {row["id"] for row in self.db.list_receipts(task_id)}
        cost_rows_before = self.db.conn.execute(
            "SELECT id FROM cost_records WHERE task_id = ?", (task_id,)
        ).fetchall()
        cost_ids_before = {row["id"] for row in cost_rows_before}
        cost_object_ids_before = {
            id(record) for record in self.coordinator.costs.records_by_task(task_id)
        }
        try:
            import_res = self.coordinator.import_receipt(
                task_dir=package_dir,
                task_id=task_id,
                attempt=attempt["attempt_number"],
                lease_id=lease_id,
                agent_id=agent_id,
                confirmed=True,
                record_estimated_cost=False,
            )
            recorded_tokens = self._record_submission_token_delta(
                task_id, agent_id, in_tokens, out_tokens
            )

            self.coordinator.transition_task(
                task_id, TaskState.SUBMITTED, actor=agent_id, confirmed=True,
            )
        except Exception as exc:
            self._rollback_submission_records(
                task_id,
                receipt_ids_before,
                cost_ids_before,
                cost_object_ids_before,
            )
            if isinstance(exc, CoordinatorError):
                raise
            raise CoordinatorError(f"Task submission failed: {exc}") from exc

        return {
            "status": "submitted",
            "task_id": task_id,
            "submission_commit": submission_commit,
            "receipt_imported": import_res,
            "recorded_tokens": recorded_tokens,
            "package_dir": package_dir,
        }

    def _handle_get_ai_pre_review(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_id = args["task_id"]
        self._require_project_task(task_id)
        report = self.coordinator.generate_ai_pre_review(task_id)
        return {
            "task_id": task_id,
            "report": report.to_dict(),
            "markdown": report.to_markdown(),
        }

    def _handle_diagnose_failure(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_id = args["task_id"]
        self._require_project_task(task_id)
        diagnosis = self.coordinator.diagnose_task_failure(task_id)
        return {
            "task_id": task_id,
            "diagnosis": diagnosis.to_dict(),
            "markdown": diagnosis.to_markdown(),
        }

    def _handle_add_dependency(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_id = args["task_id"]
        depends_on_task_id = args["depends_on_task_id"]
        self._require_project_task(task_id)
        self._require_project_task(depends_on_task_id)
        success = self.coordinator.add_task_dependency(task_id, depends_on_task_id)
        return {
            "task_id": task_id,
            "depends_on_task_id": depends_on_task_id,
            "added": success,
        }

    def _handle_get_dag_plan(self, args: Dict[str, Any]) -> Dict[str, Any]:
        project_id = self._resolve_project_id(args.get("project_id"))
        layers = self.coordinator.get_dag_execution_plan(project_id)
        return {
            "project_id": project_id,
            "stage_count": len(layers),
            "execution_layers": layers,
        }

    def _handle_get_task_diff(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_id = args["task_id"]
        self._require_project_task(task_id)
        diff_data = self.coordinator.get_task_diff(task_id)
        return {
            "task_id": task_id,
            "base_commit": diff_data.get("base_commit", ""),
            "files_changed": diff_data.get("files_changed", []),
            "stats": diff_data.get("stats", ""),
            "diff_text": diff_data.get("diff_text", ""),
        }

    # ── MCP Resources & Prompts Handlers ─────────────────────

    def _handle_resources_list(self) -> Dict[str, Any]:
        pid = self._resolve_project_id()
        resources = [
            {
                "uri": "bridge://project/summary",
                "name": "Project Summary",
                "description": "High-level summary of tasks, agents, and pending reviews",
                "mimeType": "application/json",
            },
            {
                "uri": "bridge://audit/events",
                "name": "Audit Events",
                "description": "Recent immutable audit event stream",
                "mimeType": "application/json",
            },
        ]
        tasks = self.coordinator.list_tasks()
        for t in tasks:
            if not pid or t.get("project_id") == pid:
                resources.append({
                    "uri": f"bridge://tasks/{t['id']}/context",
                    "name": f"Task Context: {t.get('title', t['id'])}",
                    "description": f"State, bounds, worktree, and acceptance criteria for {t['id']}",
                    "mimeType": "application/json",
                })
        return {"resources": resources}

    def _handle_resources_read(self, uri: str) -> Dict[str, Any]:
        if uri == "bridge://project/summary":
            pid = self._resolve_project_id()
            summary = self.coordinator.get_project_summary(pid)
            text = json.dumps({
                "project_id": summary.project_id,
                "project_name": summary.project_name,
                "total_tasks": summary.total_tasks,
                "total_agents": summary.total_agents,
                "pending_reviews": summary.pending_reviews,
                "active_leases": summary.active_leases,
                "queued_merges": getattr(summary, "queued_merges", 0),
            }, indent=2, ensure_ascii=False)
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

        elif uri == "bridge://audit/events":
            pid = self._resolve_project_id()
            events = self.db.list_events(limit=50)
            if pid:
                events = [
                    event for event in events
                    if event.get("project_id") == pid
                    or (not event.get("project_id") and not event.get("task_id"))
                ]
            text = json.dumps(events, indent=2, ensure_ascii=False)
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

        elif uri.startswith("bridge://tasks/") and uri.endswith("/context"):
            task_id = uri[len("bridge://tasks/"):-len("/context")]
            task = self._require_project_task(task_id)
            ws = self.coordinator.get_worktree(task_id) or {}
            lease = self.coordinator.get_active_lease(task_id)
            dep_status = self.coordinator.get_task_dependency_status(task_id)
            lease_dict = None
            if lease:
                lease_dict = lease.to_dict() if hasattr(lease, "to_dict") else dict(lease)
            text = json.dumps({
                "task": task,
                "worktree": ws,
                "lease": lease_dict,
                "dependencies": dep_status,
            }, indent=2, ensure_ascii=False)
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}

        else:
            raise CoordinatorError(f"Resource URI '{uri}' not found")

    def _handle_prompts_list(self) -> Dict[str, Any]:
        return {
            "prompts": [
                {
                    "name": "implementer_start_task",
                    "description": "Standard startup instructions for an implementation agent claiming a task",
                    "arguments": [
                        {"name": "task_id", "description": "The task ID to implement", "required": True}
                    ],
                },
                {
                    "name": "reviewer_inspect_task",
                    "description": "Standard inspection instructions for an independent reviewer agent",
                    "arguments": [
                        {"name": "task_id", "description": "The task ID to review", "required": True}
                    ],
                },
                {
                    "name": "healer_fix_failure",
                    "description": "Auto-fix prompt generated when tests or validations fail",
                    "arguments": [
                        {"name": "task_id", "description": "The task ID that failed validation", "required": True}
                    ],
                },
            ]
        }

    def _handle_prompts_get(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        task_id = arguments.get("task_id", "")
        task = self._require_project_task(task_id) if task_id else None
        if not task:
            raise CoordinatorError(f"Task '{task_id}' required and not found")

        if name == "implementer_start_task":
            ws = self.coordinator.get_worktree(task_id) or {}
            prompt_text = (
                f"# Start Implementing Task {task_id}: {task.get('title')}\n\n"
                f"**Goal**: {task.get('goal', '')}\n"
                f"**Worktree Path**: {ws.get('worktree_path', 'Not created yet')}\n"
                f"**Allowed Paths**: {task.get('allowed_paths_json', '[]')}\n\n"
                "## Instructions:\n"
                "1. Work strictly inside your isolated worktree.\n"
                "2. Write code and tests according to acceptance criteria.\n"
                "3. Run validation before submitting.\n"
                "4. When finished, call `bridge_submit_task`."
            )
            return {
                "description": f"Startup instructions for {task_id}",
                "messages": [{"role": "user", "content": {"type": "text", "text": prompt_text}}],
            }

        elif name == "reviewer_inspect_task":
            diff = self.coordinator.get_task_diff(task_id)
            prompt_text = (
                f"# Review Task {task_id}: {task.get('title')}\n\n"
                f"**Objective**: {task.get('goal', '')}\n"
                f"**Files Changed**: {', '.join(diff.get('files_changed', []))}\n\n"
                "## Instructions:\n"
                "1. Inspect the diff against requirements and scope.\n"
                "2. Verify test evidence.\n"
                "3. Approve or request revisions."
            )
            return {
                "description": f"Review instructions for {task_id}",
                "messages": [{"role": "user", "content": {"type": "text", "text": prompt_text}}],
            }

        elif name == "healer_fix_failure":
            diag = self.coordinator.diagnose_task_failure(task_id)
            return {
                "description": f"Remediation instructions for {task_id}",
                "messages": [{"role": "user", "content": {"type": "text", "text": diag.next_attempt_prompt}}],
            }

        else:
            raise CoordinatorError(f"Prompt '{name}' not found")

    # ── JSON-RPC Protocol Dispatcher ─────────────────────────

    def handle_request(self, request: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(request, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: expected JSON object"},
            }

        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params")
        if not isinstance(params, dict):
            params = {}

        # Notifications (no id)
        if req_id is None:
            if method == "notifications/initialized":
                logger.info("Client completed initialization handshake")
            return None

        # RPC calls
        try:
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {
                            "tools": {},
                            "resources": {},
                            "prompts": {},
                        },
                        "serverInfo": {
                            "name": SERVER_NAME,
                            "version": SERVER_VERSION,
                        },
                    },
                }

            elif method == "ping":
                return {"jsonrpc": "2.0", "id": req_id, "result": {}}

            elif method == "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": list(self.tools.values()),
                    },
                }

            elif method == "resources/list":
                res = self._handle_resources_list()
                return {"jsonrpc": "2.0", "id": req_id, "result": res}

            elif method == "resources/read":
                uri = params.get("uri", "")
                res = self._handle_resources_read(uri)
                return {"jsonrpc": "2.0", "id": req_id, "result": res}

            elif method == "prompts/list":
                res = self._handle_prompts_list()
                return {"jsonrpc": "2.0", "id": req_id, "result": res}

            elif method == "prompts/get":
                name = params.get("name", "")
                args = params.get("arguments") or {}
                res = self._handle_prompts_get(name, args)
                return {"jsonrpc": "2.0", "id": req_id, "result": res}

            elif method == "tools/call":
                tool_name = params.get("name") or ""
                arguments = params.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = {}
                handler = self.tool_handlers.get(tool_name)
                if not handler:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": f"Unknown tool '{tool_name}'"}],
                            "isError": True,
                        },
                    }

                res = handler(arguments)
                text_content = json.dumps(res, indent=2, ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": text_content}],
                        "isError": False,
                    },
                }

            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{method}' not found",
                    },
                }

        except Exception as e:
            logger.error(f"Error handling MCP method {method}: {e}", exc_info=True)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                    "isError": True,
                },
            }

    def run_stdio(self):
        """Run MCP JSON-RPC 2.0 loop over stdin and stdout."""
        # Ensure utf-8 text encoding for stdio
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")

        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception as e:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {e}"},
                    }
                    try:
                        sys.stdout.write(json.dumps(resp) + "\n")
                        sys.stdout.flush()
                    except (BrokenPipeError, OSError):
                        break
                    continue

                resp = self.handle_request(msg)
                if resp is not None:
                    try:
                        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                        sys.stdout.flush()
                    except (BrokenPipeError, OSError):
                        break
        finally:
            self.close()


def create_mcp_server(db_path: str, project_id: Optional[str] = None) -> BridgeMCPServer:
    """Factory helper to instantiate a BridgeMCPServer from a DB path."""
    db = init_database(db_path)
    if not db.acquire_lock():
        db.close()
        raise CoordinatorError(
            f"Another Bridge instance is already using database '{db_path}'"
        )
    try:
        coord = BridgeCoordinator(database=db)
        return BridgeMCPServer(coordinator=coord, default_project_id=project_id)
    except Exception:
        db.release_lock()
        db.close()
        raise


def main():
    """Main CLI entrypoint for Bridge MCP server."""
    import argparse

    parser = argparse.ArgumentParser(description="Bridge MCP Server for AI Agent Coordination")
    parser.add_argument("--db", default=os.path.join(".bridge", "bridge.db"), help="Path to bridge.db")
    parser.add_argument("--project-id", default=None, help="Target project ID")
    args = parser.parse_args()

    server = create_mcp_server(args.db, args.project_id)
    server.run_stdio()


if __name__ == "__main__":
    main()
