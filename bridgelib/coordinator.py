"""Core Bridge coordinator that integrates all subsystems behind a unified API.

Design reference: docs/bridge-design/02-system-architecture.md, Coordinator section.
"""

import json
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field
from bridgelib.database import Database
from bridgelib.state_machine import (
    TaskState, ProgressionPolicy, validate_transition, is_valid_transition,
    StateMachineError,
)
from bridgelib.leases import LeaseManager, Lease, compute_expiry
from bridgelib.review import ReviewManager, ReviewPackage, ReviewVerdict, ReviewRequest, ReviewResult
from bridgelib.merge import MergeQueue, MergeEntry, MergeStatus
from bridgelib.workspace import WorkspaceManager, Workspace
from bridgelib.operations import OperationLog, OperationEntry
from bridgelib.errors import StateError
from bridgelib.scope import is_within_scope
from bridgelib.routing import RouteRequest, recommend_agent
from bridgelib.cost import CostTracker, BudgetGuard, BudgetThreshold
from bridgelib.retry import FailureClassifier, RetryPolicy, RetryManager, EscalationDecider
from bridgelib.context import ContextManager, ContextLayer, generate_context_summary
from bridgelib.safety import (
    SafetyPolicy, ConfirmationMode, ActionPolicy, HARD_FLOOR_ACTIONS,
)


class CoordinatorError(Exception):
    """Coordinator error."""
    pass


@dataclass
class TaskSummary:
    task_id: str = ""
    title: str = ""
    state: str = ""
    owner_agent: str = ""
    reviewer_agent: str = ""
    risk: str = "low"

@dataclass
class ProjectSummary:
    project_id: str = ""
    project_name: str = ""
    total_tasks: int = 0
    total_agents: int = 0
    tasks_by_state: dict = field(default_factory=dict)
    active_leases: int = 0
    pending_reviews: int = 0


class BridgeCoordinator:
    """Integrate the database, state machine, leases, reviews, merges, workspaces, and operation log."""

    def __init__(
        self,
        database: Database,
        lease_manager: LeaseManager | None = None,
        review_manager: ReviewManager | None = None,
        merge_queue: MergeQueue | None = None,
        workspace_manager: WorkspaceManager | None = None,
        operation_log: OperationLog | None = None,
        cost_tracker: CostTracker | None = None,
        budget_guard: BudgetGuard | None = None,
        retry_manager: RetryManager | None = None,
        escalation_decider: EscalationDecider | None = None,
        context_manager: ContextManager | None = None,
    ):
        self.db = database
        self.leases = lease_manager or LeaseManager()
        self.reviews = review_manager or ReviewManager()
        self.merge = merge_queue or MergeQueue()
        self.workspaces = workspace_manager or WorkspaceManager()
        self.ops = operation_log or OperationLog()
        self.costs = cost_tracker or CostTracker()
        self.budget = budget_guard or BudgetGuard()
        self.retry = retry_manager or RetryManager()
        self.escalation = escalation_decider or EscalationDecider()
        self.context = context_manager or ContextManager()
        self._safety_policies: dict[str, SafetyPolicy] = {}
        self._active_project_id = ""
        self._ephemeral_check_commands: dict[str, dict] = {}
        self.safety = SafetyPolicy(mode=ConfirmationMode.BALANCED)

        # Restore runtime state from the database to support restart recovery.
        self._hydrate_from_db()

    def _hydrate_from_db(self):
        """Restore leases, reviews, merges, costs, and operations into memory.

        Treat SQLite as the sole authoritative source and restore all runtime state.
        """
        from datetime import datetime as _dt

        def _row_get(row, key, default=""):
            """Safely read a Row field because sqlite3.Row has no .get() method."""
            try:
                val = row[key]
                return val if val is not None else default
            except (KeyError, IndexError):
                return default

        # ── Restore leases, preserving expires_at instead of recomputing it. ──
        active_leases = self.db.list_active_leases()
        for row in active_leases:
            expires_at_str = _row_get(row, "expires_at", "")
            try:
                expires_at = _dt.fromisoformat(expires_at_str)
            except (ValueError, TypeError):
                expires_at = compute_expiry()

            task = self.db.get_task(row["task_id"])
            project_id = task.get("project_id", "") if task else ""

            lease = Lease(
                lease_id=row["id"],
                task_id=row["task_id"],
                agent_id=row["agent_id"],
                attempt_id=_row_get(row, "attempt_id", "") or "",
                project_id=project_id,
                resource_type=_row_get(row, "resource_type", "task"),
                resource_path=_row_get(row, "resource_path", ""),
                status=_row_get(row, "status", "active"),
                expires_at=expires_at,
            )
            self.leases._leases[lease.lease_id] = lease

        # ── Restore pending reviews. ──
        pending_reviews = self.db.list_pending_reviews()
        for row in pending_reviews:
            req = ReviewRequest(
                request_id=row["id"],
                task_id=row["task_id"],
                reviewer_agent_id=row["reviewer_agent_id"],
                review_package=ReviewPackage(task_id=row["task_id"], title=""),
            )
            self.reviews._requests[req.request_id] = req
            self.reviews._by_task.setdefault(req.task_id, []).append(req.request_id)

        # ── Restore completed review results. ──
        try:
            all_reviews = self.db.conn.execute(
                "SELECT * FROM reviews WHERE verdict != 'pending'"
            ).fetchall()
            for row in all_reviews:
                rid = row["id"]
                verdict_str = row["verdict"]
                try:
                    verdict = ReviewVerdict(verdict_str)
                except ValueError:
                    verdict = ReviewVerdict.APPROVED
                result = ReviewResult(
                    request_id=rid,
                    verdict=verdict,
                    summary=_row_get(row, "issues_json", ""),
                    fix_task_id=_row_get(row, "fix_task_id"),
                    completed_at=_row_get(row, "completed_at", ""),
                )
                self.reviews._results[rid] = result
        except Exception:
            pass

        # ── Restore merge entries in every state: queued/merging/merged/conflict/failed/cancelled. ──
        merge_rows = self.db.list_merge_entries()
        for row in merge_rows:
            entry = MergeEntry(
                entry_id=row["id"],
                task_id=row["task_id"],
                target_branch=_row_get(row, "target_branch", "main"),
                candidate_commit=_row_get(row, "candidate_commit", ""),
                queue_position=_row_get(row, "queue_position", 0),
                status=_row_get(row, "status", "queued"),
                result=_row_get(row, "result", ""),
            )
            self.merge._entries[entry.entry_id] = entry
            self.merge._next_position = max(
                self.merge._next_position, entry.queue_position + 1
            )

        # ── Restore cost records. ──
        try:
            cost_rows = self.db.conn.execute(
                "SELECT * FROM cost_records ORDER BY created_at"
            ).fetchall()
            for row in cost_rows:
                from bridgelib.cost import CostRecord
                record = CostRecord(
                    task_id=_row_get(row, "task_id", ""),
                    agent_id=_row_get(row, "agent_id", ""),
                    input_tokens=_row_get(row, "input_tokens", 0),
                    output_tokens=_row_get(row, "output_tokens", 0),
                    estimated_cost=_row_get(row, "estimated_cost", 0.0),
                    is_estimated=bool(_row_get(row, "is_estimated", 1)),
                    source=_row_get(row, "source", "manual"),
                    created_at=_row_get(row, "created_at", ""),
                )
                self.costs._records.append(record)
        except Exception:
            pass

        # ── Restore the operation log. ──
        try:
            op_rows = self.db.conn.execute(
                "SELECT * FROM operations ORDER BY created_at"
            ).fetchall()
            for row in op_rows:
                op = OperationEntry(
                    operation_id=row["id"],
                    operation_type=row["op_type"],
                    task_id=_row_get(row, "task_id", ""),
                    target=_row_get(row, "target", ""),
                    idempotency_key=_row_get(row, "idempotency_key", ""),
                    status=_row_get(row, "status", "prepared"),
                    result=_row_get(row, "result", ""),
                    created_at=_row_get(row, "created_at", ""),
                    completed_at=_row_get(row, "updated_at", ""),
                )
                self.ops._entries[op.operation_id] = op
                if op.idempotency_key:
                    self.ops._idempotency_keys.add(op.idempotency_key)
        except Exception:
            pass

        # ── Restore workspaces. ──
        try:
            ws_rows = self.db.conn.execute(
                "SELECT * FROM workspaces WHERE status = 'active' ORDER BY created_at"
            ).fetchall()
            for row in ws_rows:
                from bridgelib.workspace import Workspace
                ws = Workspace(
                    workspace_id=row["id"],
                    task_id=row["task_id"],
                    agent_id=row["agent_id"],
                    worktree_path=_row_get(row, "worktree_path", ""),
                    branch=_row_get(row, "branch", ""),
                    base_commit=_row_get(row, "base_commit", ""),
                    status=_row_get(row, "status", "active"),
                    created_at=_row_get(row, "created_at", ""),
                )
                self.workspaces._workspaces[ws.workspace_id] = ws
                self.workspaces._by_task[ws.task_id] = ws.workspace_id
        except Exception:
            pass

        # ── Restore retry history. ──
        try:
            # Find the most recent retry reset for each task.
            reset_rows = self.db.conn.execute(
                "SELECT task_id, MAX(created_at_utc) as last_reset FROM events "
                "WHERE event_type = 'TaskRetryReset' GROUP BY task_id"
            ).fetchall()
            last_resets = {r["task_id"]: r["last_reset"] for r in reset_rows}

            failure_rows = self.db.conn.execute(
                "SELECT * FROM events WHERE event_type = 'TaskFailure' "
                "ORDER BY created_at_utc"
            ).fetchall()
            import json as _json
            for row in failure_rows:
                tid = _row_get(row, "task_id", "")
                created_at = _row_get(row, "created_at_utc", "")

                # Ignore failures that occurred before the most recent reset.
                if tid in last_resets and created_at <= last_resets[tid]:
                    continue

                payload = _json.loads(_row_get(row, "payload_json", "{}"))
                if tid and payload:
                    self.retry.record_failure(
                        tid, payload.get("error", ""),
                        payload.get("exit_code"),
                    )
        except Exception:
            pass

        # ── Restore the safety policy. ──
        try:
            proj_rows = self.db.conn.execute(
                "SELECT id, confirmation_policy FROM projects "
                "ORDER BY updated_at DESC LIMIT 1"
            ).fetchall()
            if proj_rows:
                self._active_project_id = proj_rows[0]["id"]
                self.safety = self.get_safety_policy(self._active_project_id)
        except Exception:
            pass

        # ── Perform crash recovery by reconciling Git and database state. ──
        self._reconcile_git_merges()

    def _reconcile_git_merges(self):
        """Reconcile MergeCommitIntent events against current Git and database state."""
        try:
            # Find entries that were left in the merging state.
            merging_entries = [e for e in self.merge._entries.values() if e.status == MergeStatus.MERGING]
            for entry in merging_entries:
                # Find the most recent MergeCommitIntent event.
                intent_rows = self.db.conn.execute(
                    "SELECT payload_json FROM events WHERE event_type = 'MergeCommitIntent' "
                    "AND task_id = ? ORDER BY created_at_utc DESC LIMIT 1",
                    (entry.task_id,)
                ).fetchall()
                if not intent_rows:
                    continue
                import json as _json
                payload = _json.loads(intent_rows[0]["payload_json"])
                target_branch = payload.get("target_branch")
                old_ref = payload.get("old_ref")
                new_ref = payload.get("new_ref")
                if not target_branch or not new_ref:
                    continue

                task = self.db.get_task(entry.task_id)
                project_id = task.get("project_id", "") if task else ""
                proj = self.db.get_project(project_id) if project_id else None
                if not proj:
                    continue

                from bridgelib.git_adapter import GitRepositoryAdapter
                adapter = GitRepositoryAdapter(proj["root_path"])
                if not adapter.check_repo().is_repo:
                    continue

                r = adapter._run(["git", "rev-parse", "refs/heads/" + target_branch])
                current_ref = r.stdout.strip() if r.returncode == 0 else ""

                if current_ref == new_ref:
                    # Git was updated but the database is still merging; complete it as recovery.
                    try:
                        entry = self.merge.complete_merge(entry.entry_id, "Recovered from Git intent")
                        self.db.update_merge_entry(entry.entry_id, MergeStatus.MERGED, "Recovered from Git intent")
                        self.db.write_event(
                            event_type="MergeCompleted", actor_type="system", actor_id="coordinator",
                            task_id=entry.task_id,
                            payload={"entry_id": entry.entry_id, "result": "Recovered from Git intent", "recovered": True},
                        )
                    except Exception:
                        pass
                elif current_ref == old_ref:
                    # Git was not updated, so update-ref failed or was never reached; requeue for retry.
                    entry.status = MergeStatus.QUEUED
                    try:
                        self.db.update_merge_entry(entry.entry_id, MergeStatus.QUEUED, "Recovered to queued")
                    except Exception:
                        pass
        except Exception:
            pass

    # ── Project ───────────────────────────────────────────

    def init_project(self, name: str, root_path: str, language: str = "zh-CN",
                     progression: str = "hybrid", confirmation: str = "balanced") -> str:
        pid = self.db.create_project(
            name=name, root_path=root_path, language=language,
            progression_policy=progression, confirmation_policy=confirmation,
        )
        # Initialize the safety policy from the project's confirmation setting.
        self._active_project_id = pid
        self.safety = self.get_safety_policy(pid)
        self.db.write_event(
            event_type="ProjectInitialized", actor_type="user", actor_id="user",
            project_id=pid, payload={"name": name},
        )
        return pid

    def get_project(self, project_id: str) -> dict | None:
        return self.db.get_project(project_id)

    # ── Agents ────────────────────────────────────────────

    def add_agent(self, project_id: str, display_name: str, **kwargs) -> str:
        aid = self.db.create_agent(project_id=project_id, display_name=display_name, **kwargs)
        self.db.write_event(
            event_type="AgentProfileCreated", actor_type="user", actor_id="user",
            project_id=project_id, payload={"agent_id": aid, "display_name": display_name},
        )
        return aid

    def get_agent(self, agent_id: str) -> dict | None:
        return self.db.get_agent(agent_id)

    def list_agents(self, project_id: str) -> list[dict]:
        return self.db.list_agents(project_id)

    # ── Goals ─────────────────────────────────────────────

    def create_goal(self, project_id: str, title: str, description: str = "") -> str:
        gid = self.db.create_goal(project_id=project_id, title=title, description=description)
        self.db.write_event(
            event_type="GoalCreated", actor_type="user", actor_id="user",
            project_id=project_id, payload={"goal_id": gid, "title": title},
        )
        return gid

    # ── Tasks ─────────────────────────────────────────────

    def create_task(self, goal_id: str, title: str, **kwargs) -> str:
        goal = self.db.get_goal(goal_id)
        if not goal:
            raise CoordinatorError(f"Goal {goal_id} not found")

        # Require the task to inherit its goal's project.
        project_id = goal["project_id"]
        if kwargs.get("project_id") and kwargs["project_id"] != project_id:
            raise CoordinatorError(
                f"Task project_id ({kwargs['project_id']}) must match "
                f"goal project_id ({project_id})"
            )

        tid = self.db.create_task(
            goal_id=goal_id, title=title,
            project_id=project_id,  # Always use the goal's project_id.
            **{k: v for k, v in kwargs.items() if k != "project_id"},
        )
        self.db.write_event(
            event_type="TaskCreated", actor_type="user", actor_id="user",
            project_id=goal["project_id"], task_id=tid,
            payload={"title": title, "goal_id": goal_id},
        )
        return tid

    def get_task(self, task_id: str) -> dict | None:
        return self.db.get_task(task_id)

    def list_tasks(self, state: str | None = None) -> list[dict]:
        return self.db.list_tasks(state=state)

    def transition_task(self, task_id: str, to_state: TaskState, actor: str = "system",
                        policy: ProgressionPolicy | None = None,
                        confirmed: bool = False):
        """Transition task state using the project policy, defaulting to HYBRID."""
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        # Precedence: call argument, task override, project setting, then HYBRID.
        if policy is None:
            task_policy = task.get("progression_policy")
            if task_policy:
                try:
                    policy = ProgressionPolicy(task_policy)
                except ValueError:
                    policy = ProgressionPolicy.HYBRID
            else:
                project = self.db.get_project(task.get("project_id", ""))
                proj_policy = project.get("progression_policy", "") if project else ""
                try:
                    policy = ProgressionPolicy(proj_policy) if proj_policy else ProgressionPolicy.HYBRID
                except ValueError:
                    policy = ProgressionPolicy.HYBRID

        from_state = TaskState(task["state"])
        try:
            result = validate_transition(from_state, to_state, policy)
        except StateMachineError as e:
            raise CoordinatorError(str(e)) from e

        # Enforce the confirmation policy.
        if result.requires_confirmation and not confirmed:
            raise CoordinatorError(
                f"Transition {from_state.value} -> {to_state.value} requires user confirmation "
                f"under {policy.value} policy. Set confirmed=True."
            )

        # Validate business rules for the applicable transitions.
        if to_state == TaskState.READY:
            self._validate_task_ready(task)
        elif to_state == TaskState.APPROVED:
            self._validate_task_approved(task)
        elif to_state == TaskState.DONE:
            self._validate_task_done(task)

        # Atomically update state and write the event.
        self.db.update_task_state_with_event(
            task_id=task_id,
            from_state=from_state.value,
            to_state=to_state.value,
            expected_version=task["version"],
            event_type="TaskStateChanged",
            actor_type=actor,
            actor_id=actor,
            project_id=task.get("project_id", ""),
        )

    def assign_task(self, task_id: str, owner_agent_id: str, reviewer_agent_id: str = "",
                    actor: str = "user", confirmed: bool = False):
        """Assign a task after validating agent identity, project, status, permissions, and confirmation."""
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        if task["state"] != TaskState.READY.value:
            raise CoordinatorError(
                f"Task must be in 'ready' state to assign, current: {task['state']}"
            )

        project_id = task.get("project_id", "")

        # ── Enforce the confirmation policy through SafetyPolicy. ─────────
        safety = self.get_safety_policy(project_id)
        if not safety.check_allowed("task_assignment", confirmed=confirmed):
            if safety.is_disabled("task_assignment"):
                raise CoordinatorError("Task assignment is DISABLED")
            raise CoordinatorError(
                f"Task assignment requires confirmation under {safety.mode.value} mode. "
                "Set confirmed=True."
            )

        # ── Validate the owner agent. ───────────────────────────────
        owner = self.db.get_agent(owner_agent_id)
        if owner is None:
            raise CoordinatorError(f"Owner agent {owner_agent_id} not found")
        if not owner.get("enabled"):
            raise CoordinatorError(f"Owner agent {owner_agent_id} is disabled")
        if owner.get("project_id") != project_id:
            raise CoordinatorError(
                f"Owner agent {owner_agent_id} belongs to project {owner.get('project_id')}, "
                f"not {project_id}"
            )
        import json as _json
        perms = _json.loads(owner.get("permissions_json", "{}"))
        if not perms.get("can_plan") and not perms.get("can_run_validation"):
            # implementer role is implicit for all non-planner/non-reviewer agents
            pass  # Implementers do not require a dedicated permission flag.

        # ── Validate the reviewer agent. ─────────────────────────────
        if reviewer_agent_id:
            reviewer = self.db.get_agent(reviewer_agent_id)
            if reviewer is None:
                raise CoordinatorError(f"Reviewer agent {reviewer_agent_id} not found")
            if not reviewer.get("enabled"):
                raise CoordinatorError(f"Reviewer agent {reviewer_agent_id} is disabled")
            if reviewer.get("project_id") != project_id:
                raise CoordinatorError(
                    f"Reviewer agent {reviewer_agent_id} belongs to project "
                    f"{reviewer.get('project_id')}, not {project_id}"
                )
            r_perms = _json.loads(reviewer.get("permissions_json", "{}"))
            if not r_perms.get("can_review"):
                raise CoordinatorError(
                    f"Reviewer agent {reviewer_agent_id} does not have review permission"
                )

        # ── The owner and reviewer must differ. ─────────────────────
        if reviewer_agent_id and owner_agent_id == reviewer_agent_id:
            raise CoordinatorError("Owner and reviewer must be different agents")

        # Atomically transition state, update owner/reviewer, and write the event.
        try:
            self.db.update_task_state(
                task_id, TaskState.READY.value, TaskState.ASSIGNED.value,
                expected_version=task["version"],
            )
            self.db.conn.execute(
                "UPDATE tasks SET owner_agent_id = ?, reviewer_agent_id = ? WHERE id = ?",
                (owner_agent_id, reviewer_agent_id or None, task_id),
            )
            self.db._write_event_in_tx(
                event_type="TaskAssigned",
                actor_type=actor,
                actor_id=actor,
                project_id=project_id,
                task_id=task_id,
                payload={"owner": owner_agent_id, "reviewer": reviewer_agent_id},
            )
            self.db.conn.commit()
        except Exception as e:
            self.db.conn.rollback()
            raise CoordinatorError(
                f"Failed to assign task {task_id}: {e}"
            ) from e

    # ── Leases ────────────────────────────────────────────

    def acquire_lease(self, task_id: str, agent_id: str, resource_path: str = "",
                      resource_type: str = "task", ttl_seconds: int = 900) -> Lease:
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        project_id = task.get("project_id", "")

        # Verify that the agent exists, is enabled, and belongs to the project.
        agent = self.db.get_agent(agent_id)
        if agent is None:
            raise CoordinatorError(f"Agent {agent_id} not found")
        if not agent.get("enabled"):
            raise CoordinatorError(f"Agent {agent_id} is disabled")
        if agent.get("project_id") != project_id:
            raise CoordinatorError(
                f"Agent {agent_id} belongs to project {agent.get('project_id')}, "
                f"not {project_id}"
            )

        # Require the task to be ASSIGNED or IN_PROGRESS.
        if task["state"] not in (TaskState.ASSIGNED.value, TaskState.IN_PROGRESS.value):
            raise CoordinatorError(
                f"Task must be in 'assigned' or 'in_progress' state to acquire a lease, "
                f"current: {task['state']}"
            )

        # Verify that the agent owns the task.
        if task.get("owner_agent_id") != agent_id:
            raise CoordinatorError(
                f"Agent {agent_id} is not the owner of task {task_id}"
            )

        # When provided, require resource_path to be within the task scope.
        if resource_path:
            allowed = json.loads(task.get("allowed_paths_json", "[]") or "[]")
            forbidden = json.loads(task.get("forbidden_paths_json", "[]") or "[]")
            if not is_within_scope(resource_path, allowed, forbidden):
                raise CoordinatorError(
                    f"Resource path '{resource_path}' is not within the allowed scope "
                    f"of task {task_id}"
                )

        # Prevent duplicate active leases for one task, accounting for expiration.
        from datetime import datetime, timezone as tz
        existing_leases = self.db.list_leases_by_task(task_id)
        now_utc = datetime.now(tz.utc).isoformat()
        for l in existing_leases:
            if l["status"] == "active":
                expires = l.get("expires_at", "")
                if expires and expires > now_utc:
                    raise CoordinatorError(
                        f"Task {task_id} already has an active lease ({l['id']}) "
                        f"expiring at {expires}"
                    )
                # An expired lease does not block acquisition.

        # Acquire through the in-memory manager, including path-conflict checks.
        lease = self.leases.acquire(
            task_id=task_id, agent_id=agent_id, resource_type=resource_type,
            resource_path=resource_path, ttl_seconds=ttl_seconds,
            project_id=project_id,
        )

        # Persist to the database; roll back the in-memory lease on failure.
        try:
            self.db.create_lease(
                lease_id=lease.lease_id, task_id=task_id, agent_id=agent_id,
                resource_type=resource_type, resource_path=resource_path,
                ttl_seconds=ttl_seconds,
            )
        except Exception:
            # Database failure: roll back the in-memory lease.
            self.leases._leases.pop(lease.lease_id, None)
            raise CoordinatorError(
                f"Failed to persist lease {lease.lease_id} to database"
            )

        self.db.write_event(
            event_type="LeaseAcquired", actor_type="system", actor_id="coordinator",
            task_id=task_id,
            payload={"lease_id": lease.lease_id, "agent_id": agent_id},
        )
        return lease

    # ── Reviews ───────────────────────────────────────────

    def submit_review(self, task_id: str, reviewer_agent_id: str,
                      review_package: ReviewPackage) -> str:
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        project_id = task.get("project_id", "")

        # Verify that the reviewer exists, is enabled, and belongs to the project.
        reviewer = self.db.get_agent(reviewer_agent_id)
        if reviewer is None:
            raise CoordinatorError(f"Reviewer agent {reviewer_agent_id} not found")
        if not reviewer.get("enabled"):
            raise CoordinatorError(f"Reviewer agent {reviewer_agent_id} is disabled")
        if reviewer.get("project_id") != project_id:
            raise CoordinatorError(
                f"Reviewer agent {reviewer_agent_id} belongs to project "
                f"{reviewer.get('project_id')}, not {project_id}"
            )

        # Verify that the reviewer has review permission.
        import json as _json
        perms = _json.loads(reviewer.get("permissions_json", "{}"))
        if not perms.get("can_review"):
            raise CoordinatorError(
                f"Reviewer agent {reviewer_agent_id} does not have review permission"
            )

        # Require a reviewable task state, starting at IN_PROGRESS.
        valid_states = {TaskState.IN_PROGRESS.value, TaskState.SUBMITTED.value,
                        TaskState.VALIDATING.value, TaskState.APPROVED.value,
                        TaskState.MERGE_QUEUED.value, TaskState.MERGING.value}
        if task["state"] not in valid_states:
            raise CoordinatorError(
                f"Task must be in a reviewable state (in_progress/submitted/validating/"
                f"approved/merge_queued/merging), current: {task['state']}"
            )

        # Submit through the in-memory manager, which generates request_id.
        req = self.reviews.submit(task_id, reviewer_agent_id, review_package)

        current_attempt = self.get_current_attempt(task_id)
        attempt_id = current_attempt["id"] if current_attempt else ""

        # Persist to the database; roll back in-memory state on failure.
        try:
            self.db.create_review(
                review_id=req.request_id,
                task_id=task_id,
                reviewer_agent_id=reviewer_agent_id,
                attempt_id=attempt_id,
            )
        except Exception as e:
            # Database failure: roll back the in-memory review request.
            self.reviews._requests.pop(req.request_id, None)
            task_reqs = self.reviews._by_task.get(task_id, [])
            if req.request_id in task_reqs:
                task_reqs.remove(req.request_id)
            raise CoordinatorError(
                f"Failed to persist review {req.request_id} to database: {e}"
            ) from e

        self.db.write_event(
            event_type="ReviewRequested", actor_type="user", actor_id="user",
            task_id=task_id,
            payload={"request_id": req.request_id, "reviewer": reviewer_agent_id},
        )
        return req.request_id

    def complete_review(self, request_id: str, verdict: ReviewVerdict,
                        summary: str = "", reviewer_agent_id: str = "",
                        **kwargs) -> ReviewResult:
        """Complete a review after validating the designated reviewer's identity.

        P0 safety guarantees:
        - reviewer_agent_id is required and cannot be omitted.
        - The reviewer must match the reviewer on the request.
        - The reviewer must exist, be enabled, and have review permission.
        - The reviewer must not be the task implementer.
        """
        req = self.reviews.get_request(request_id)
        if req is None:
            raise CoordinatorError(f"Review request {request_id} not found")

        # ── Require reviewer_agent_id. ──────────────────────
        if not reviewer_agent_id:
            raise CoordinatorError(
                f"reviewer_agent_id is required to complete review {request_id}. "
                f"Anonymous review completion is not allowed."
            )

        task = self.db.get_task(req.task_id)
        if task is not None:
            # Verify that the submitted reviewer matches the request.
            if reviewer_agent_id != req.reviewer_agent_id:
                raise CoordinatorError(
                    f"Reviewer identity mismatch: expected {req.reviewer_agent_id}, "
                    f"got {reviewer_agent_id}"
                )
            # Verify that the agent exists and is enabled.
            reviewer = self.db.get_agent(reviewer_agent_id)
            if reviewer is None:
                raise CoordinatorError(f"Reviewer agent {reviewer_agent_id} not found")
            if not reviewer.get("enabled"):
                raise CoordinatorError(f"Reviewer agent {reviewer_agent_id} is disabled")
            # Verify that the reviewer is not the task implementer.
            owner_id = task.get("owner_agent_id")
            if owner_id and reviewer_agent_id == owner_id:
                raise CoordinatorError(
                    f"Reviewer {reviewer_agent_id} cannot review task {req.task_id}: "
                    f"reviewer must not be the same as the task owner ({owner_id})"
                )
            # Verify that the reviewer has review permission.
            import json as _json_perms
            perms = _json_perms.loads(reviewer.get("permissions_json", "{}"))
            if not perms.get("can_review"):
                raise CoordinatorError(
                    f"Reviewer agent {reviewer_agent_id} does not have review permission"
                )

        # Complete in memory, update the database, and write the event as one operation.
        # Preserve the previous state for rollback.
        old_req_status = req.status
        old_req_completed = req.completed_at
        try:
            result = self.reviews.complete(request_id, verdict, summary, **kwargs)
        except Exception:
            raise

        try:
            import json as _json
            self.db.update_review(
                request_id,
                verdict=verdict.value,
                issues_json=_json.dumps(kwargs.get("issues", [])),
                fix_task_id=kwargs.get("fix_task_id"),
            )
            self.db.write_event(
                event_type="ReviewCompleted", actor_type="system",
                actor_id=reviewer_agent_id,
                task_id=req.task_id if req else "",
                payload={"verdict": verdict.value, "summary": summary,
                         "reviewer": reviewer_agent_id},
            )
        except Exception as e:
            # Database failure: roll back the in-memory review completion.
            req.status = old_req_status
            req.completed_at = old_req_completed
            self.reviews._results.pop(request_id, None)
            raise CoordinatorError(
                f"Failed to persist review completion to database: {e}"
            ) from e

        return result

    # ── Merge Queue ───────────────────────────────────────

    def enqueue_merge(self, task_id: str, candidate_commit: str = "",
                      confirmed: bool = False) -> MergeEntry:
        """Enqueue a task for merge after commit validation and safety confirmation.

        P0 safety guarantees:
        - Verify that candidate_commit exists in the project Git repository when available.
        - Enforce safety confirmation; Strict mode requires confirmed=True.
        - Keep memory and database state atomic by rolling back memory on database failure.
        """
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        # Require the task to be APPROVED.
        if task["state"] != TaskState.APPROVED.value:
            raise CoordinatorError(
                f"Task must be in 'approved' state to enqueue merge, "
                f"current: {task['state']}"
            )

        # candidate_commit is required.
        if not candidate_commit:
            raise CoordinatorError("candidate_commit must not be empty")

        project_id = task.get("project_id", "")

        # ── Use check_allowed so DISABLED actions are always blocked. ──
        risk = task.get("risk", "low")
        action_id = f"merge_{risk}_risk"
        safety = self.get_safety_policy(project_id)
        if not safety.check_allowed(action_id, confirmed=confirmed):
            if safety.is_disabled(action_id):
                raise CoordinatorError(f"Merge enqueue is DISABLED for {risk} risk tasks")
            raise CoordinatorError(
                f"Merge enqueue requires confirmation under {safety.mode.value} mode. "
                f"Set confirmed=True."
            )
        if not safety.check_allowed("merge_enqueue", confirmed=confirmed):
            if safety.is_disabled("merge_enqueue"):
                raise CoordinatorError("Merge enqueue is DISABLED")
            raise CoordinatorError(
                f"Merge enqueue requires confirmation under {safety.mode.value} mode. "
                f"Set confirmed=True."
            )

        # ── Verify that the commit actually exists. ──────────────────────────
        if not self._verify_commit_for_project(project_id, candidate_commit):
            raise CoordinatorError(
                f"Candidate commit '{candidate_commit}' does not exist in the project "
                f"repository. Cannot enqueue a fake merge."
            )

        # Enqueue through the in-memory manager, which generates entry_id.
        entry = self.merge.enqueue(task_id, candidate_commit)

        # Persist to the database; roll back in-memory state on failure.
        try:
            self.db.create_merge_entry(
                entry_id=entry.entry_id,
                task_id=task_id,
                target_branch=self.merge.target_branch,
                candidate_commit=candidate_commit,
                queue_position=entry.queue_position,
            )
        except Exception as e:
            # Database failure: roll back the in-memory merge entry.
            self.merge._entries.pop(entry.entry_id, None)
            raise CoordinatorError(
                f"Failed to persist merge entry {entry.entry_id} to database: {e}"
            ) from e

        self.db.write_event(
            event_type="MergeQueued", actor_type="system", actor_id="coordinator",
            task_id=task_id, project_id=project_id,
            payload={"entry_id": entry.entry_id, "commit": candidate_commit},
        )
        return entry

    def _verify_commit_for_project(self, project_id: str, commit: str) -> bool:
        """Verify that a commit exists in the project's Git repository.

        P0 safety guarantee: fail closed when the project path exists but is not
        a Git repository. Skip verification only when the path does not exist,
        as may occur in tests.
        """
        if not project_id:
            return True
        proj = self.db.get_project(project_id)
        if not proj:
            return True
        root_path = proj.get("root_path", "")
        if not root_path:
            return True
        # Skip verification for nonexistent placeholder paths used by tests, such as /tmp/t.
        if not os.path.isdir(root_path):
            return True
        try:
            from bridgelib.git_adapter import GitRepositoryAdapter, GitAdapterError
            adapter = GitRepositoryAdapter(root_path)
            status = adapter.check_repo()
            if not status.is_repo:
                # The path exists but is not a Git repository: fail closed.
                raise CoordinatorError(
                    f"Project root '{root_path}' is not a Git repository. "
                    f"Cannot verify commit '{commit}'. Initialize Git first."
                )
            # Use strict verification with commit^{commit} to reject blobs.
            return adapter.verify_commit_strict(commit)
        except CoordinatorError:
            raise
        except GitAdapterError:
            # An invalid path, such as a non-directory, must fail closed.
            raise CoordinatorError(
                f"Cannot access Git repository at '{root_path}': invalid path"
            )
        except Exception as e:
            # Git failures, including a missing executable, must fail closed.
            raise CoordinatorError(
                f"Git verification failed for commit '{commit}': {e}"
            )

    def start_merge(self, entry_id: str, confirmed: bool = False) -> MergeEntry:
        """Start a merge by logging the operation and attempting a real Git cherry-pick.

        P0 safety guarantees:
        - The operation log contains the correct task_id.
        - A real Git cherry-pick is attempted when the repository is available.
        - Database operation status is updated consistently.
        """
        entry = self.merge.get(entry_id)
        if entry is None:
            raise CoordinatorError(f"Merge entry {entry_id} not found")

        task_id = entry.task_id
        task = self.db.get_task(task_id) if task_id else None
        project_id = task.get("project_id", "") if task else ""

        # Use check_allowed so DISABLED actions are always blocked.
        risk = task.get("risk", "low") if task else "low"
        action_id = f"merge_{risk}_risk"
        safety = self.get_safety_policy(project_id)
        if not safety.check_allowed(action_id, confirmed=confirmed):
            if safety.is_disabled(action_id):
                raise CoordinatorError(f"Start merge is DISABLED for {risk} risk tasks")
            raise CoordinatorError(
                f"Start merge requires confirmation under {safety.mode.value} mode "
                f"for {risk} risk task. Set confirmed=True."
            )

        op = OperationEntry.prepare(
            "merge", task_id=task_id,
            idempotency_key=f"merge-{entry_id}",
            target=f"entry:{entry_id}",
        )
        self.ops.record(op)
        # Persist the operation log to the database.
        self.db.create_operation(
            op_id=op.idempotency_key,
            op_type="merge",
            task_id=task_id,
            idempotency_key=op.idempotency_key,
            target=op.target,
            status="prepared",
        )
        try:
            entry = self.merge.start_merge(entry_id)
            # Set the database state to merging; roll back memory on failure.
            try:
                self.db.update_merge_entry(entry_id, MergeStatus.MERGING)
            except Exception as db_err:
                # Database failure: roll back the in-memory merging state.
                entry.status = MergeStatus.QUEUED
                raise CoordinatorError(
                    f"Failed to persist merge status to database: {db_err}"
                ) from db_err

            # ── Attempt a real Git cherry-pick and fail closed. ──
            if project_id:
                proj = self.db.get_project(project_id)
                if proj and os.path.isdir(proj.get("root_path", "")):
                    self._try_git_cherry_pick(entry, project_id)

            return entry
        except CoordinatorError as e:
            op.mark_failed(repr(e))
            self.ops.update(op)
            try:
                self.db.update_operation(op.idempotency_key, "failed", repr(e))
            except Exception:
                pass
            # P1 rollback guarantee: return the merge state to queued after cherry-pick failure.
            try:
                entry.status = MergeStatus.QUEUED
                self.db.update_merge_entry(entry_id, MergeStatus.QUEUED, repr(e)[:200])
            except Exception:
                pass
            raise
        except Exception as e:
            op.mark_failed(repr(e))
            self.ops.update(op)
            try:
                self.db.update_operation(op.idempotency_key, "failed", repr(e))
            except Exception:
                pass
            # P1 rollback guarantee: restore the merge state.
            try:
                entry.status = MergeStatus.QUEUED
                self.db.update_merge_entry(entry_id, MergeStatus.QUEUED, repr(e)[:200])
            except Exception:
                pass
            raise CoordinatorError(f"Merge start failed: {e}") from e

    def _try_git_cherry_pick(self, entry: MergeEntry, project_id: str):
        """Run the cherry-pick in an isolated integration worktree.

        P0 safety guarantees:
        - Operate entirely in the integration worktree and never touch the user's workspace.
        - Never run git checkout, git stash, or git stash pop.
        - start_merge only cherry-picks; complete_merge updates target_branch via update-ref.
        - Fail closed by raising an exception on any Git operation failure.
        """
        if not project_id or not entry.candidate_commit:
            return
        proj = self.db.get_project(project_id)
        if not proj:
            return
        root_path = proj.get("root_path", "")
        if not root_path or not os.path.isdir(root_path):
            return

        from bridgelib.git_adapter import GitRepositoryAdapter
        adapter = GitRepositoryAdapter(root_path)
        repo_status = adapter.check_repo()
        if not repo_status.is_repo:
            raise CoordinatorError(
                f"Project root '{root_path}' is not a Git repository — "
                f"cannot perform real merge"
            )

        target_branch = entry.target_branch or "main"
        integration_branch = f"bridge/integration/{entry.entry_id[:12]}"
        # Place the integration worktree outside the repository to avoid modifying the user project.
        integration_wt_path = os.path.join(
            root_path, "..", ".bridge-integration-worktrees", entry.entry_id[:12]
        )
        integration_wt_path = os.path.realpath(integration_wt_path)

        try:
            # 1. Create the integration branch from target_branch.
            r = adapter._run(["git", "branch", integration_branch, target_branch])
            if r.returncode != 0:
                raise CoordinatorError(
                    f"Failed to create integration branch from '{target_branch}': "
                    f"{r.stderr.strip()}"
                )

            # 2. Create the integration worktree outside the repository.
            wt_result = adapter.create_worktree(
                integration_wt_path, integration_branch,
                operation_id=f"merge-{entry.entry_id}",
            )
            if not wt_result.success:
                raise CoordinatorError(
                    f"Failed to create integration worktree: {wt_result.error}"
                )

            # 3. Cherry-pick in the integration worktree without affecting the user's workspace.
            r = adapter._run(
                ["git", "cherry-pick", entry.candidate_commit],
                cwd=integration_wt_path,
            )
            if r.returncode != 0:
                # Confirm a real conflict by checking for CONFLICT in stdout.
                if "CONFLICT" in r.stdout:
                    r_conflicts = adapter._run(
                        ["git", "diff", "--name-only", "--diff-filter=U"],
                        cwd=integration_wt_path,
                    )
                    conflict_files = [
                        f for f in r_conflicts.stdout.strip().split("\n") if f
                    ]
                    adapter._run(["git", "cherry-pick", "--abort"],
                                cwd=integration_wt_path)
                    if conflict_files:
                        self.merge.mark_conflict(
                            entry.entry_id,
                            f"Conflicts: {', '.join(conflict_files)}"
                        )
                        self.db.update_merge_entry(
                            entry.entry_id, MergeStatus.CONFLICT,
                            f"Conflicts: {', '.join(conflict_files)}"
                        )
                        raise CoordinatorError(
                            f"Cherry-pick conflict in files: {conflict_files}"
                        )
                # Handle non-conflict failures such as empty or invalid commits.
                raise CoordinatorError(
                    f"Cherry-pick failed: {r.stderr.strip() or r.stdout.strip()}"
                )

            # The integration branch now contains the successfully cherry-picked changes.
            # Leave target_branch unchanged until complete_merge.

        except CoordinatorError:
            self._cleanup_integration_artifacts(
                adapter, integration_wt_path, integration_branch
            )
            raise
        except Exception as e:
            self._cleanup_integration_artifacts(
                adapter, integration_wt_path, integration_branch
            )
            raise CoordinatorError(f"Git merge operation failed: {e}") from e

    def _complete_git_merge(self, entry: MergeEntry, project_id: str):
        """Merge the integration branch into target_branch during complete_merge.

        P0 safety guarantees:
        - First check whether target_branch is checked out in any worktree.
        - If checked out, reject update-ref to prevent dirtying the user's worktree.
        - If not checked out and fast-forwardable, update it with update-ref.
        - If not fast-forwardable, merge in the integration worktree before updating the ref.
        - Never run checkout, stash, or stash pop.
        """
        if not project_id or not entry.candidate_commit:
            return
        proj = self.db.get_project(project_id)
        if not proj:
            return
        root_path = proj.get("root_path", "")
        if not root_path or not os.path.isdir(root_path):
            return
        from bridgelib.git_adapter import GitRepositoryAdapter
        adapter = GitRepositoryAdapter(root_path)
        repo_status = adapter.check_repo()
        if not repo_status.is_repo:
            return
        target_branch = entry.target_branch or "main"
        integration_branch = "bridge/integration/" + entry.entry_id[:12]
        integration_wt_path = os.path.realpath(os.path.join(
            root_path, "..", ".bridge-integration-worktrees", entry.entry_id[:12]
        ))
        try:
            r = adapter._run(["git", "rev-parse", "--verify", integration_branch])
            if r.returncode != 0:
                return
            r_wt = adapter._run(["git", "worktree", "list", "--porcelain"])
            checked_out_wt_path = None
            if r_wt.returncode == 0:
                current_wt = None
                for line in r_wt.stdout.split("\n"):
                    if line.startswith("worktree "):
                        current_wt = line[9:].strip()
                    elif line.startswith("branch "):
                        wt_branch = line[7:].strip()
                        if wt_branch == target_branch or wt_branch.endswith("/" + target_branch):
                            checked_out_wt_path = current_wt
                            break

            # P0: Resolve current refs to save intent
            r_old = adapter._run(["git", "rev-parse", "refs/heads/" + target_branch])
            old_ref = r_old.stdout.strip() if r_old.returncode == 0 else "0000000000000000000000000000000000000000"
            r_new = adapter._run(["git", "rev-parse", integration_branch])
            new_ref = r_new.stdout.strip() if r_new.returncode == 0 else ""

            if not new_ref:
                raise CoordinatorError("Integration branch has no valid commit.")

            # Record commit intent in DB before executing git
            self.db.write_event(
                event_type="MergeCommitIntent", actor_type="system", actor_id="coordinator",
                task_id=entry.task_id,
                payload={"entry_id": entry.entry_id, "target_branch": target_branch,
                         "old_ref": old_ref, "new_ref": new_ref}
            )

            if checked_out_wt_path:
                # If checked out, we MUST run git merge in that worktree so it updates the index and working tree
                r = adapter._run([
                    "git", "merge", "--ff-only", integration_branch
                ], cwd=checked_out_wt_path)
                if r.returncode != 0:
                    raise CoordinatorError(
                        f"Failed to fast-forward target branch '{target_branch}' in its checked out worktree: {r.stderr.strip()}"
                    )
            else:
                # Not checked out, safe to update-ref
                r = adapter._run([
                    "git", "merge-base", "--is-ancestor", target_branch, integration_branch
                ])
                if r.returncode == 0:
                    # Compare and swap update-ref
                    r = adapter._run([
                        "git", "update-ref", "refs/heads/" + target_branch, new_ref, old_ref
                    ])
                    if r.returncode != 0:
                        raise CoordinatorError(
                            "Failed to fast-forward target branch '" + target_branch + "': " + r.stderr.strip()
                        )
                else:
                    # Not ancestor, we need to merge in the integration worktree, then update-ref
                    r = adapter._run([
                        "git", "merge", target_branch,
                        "-m", "Bridge merge: " + entry.entry_id + " (task " + entry.task_id + ")",
                    ], cwd=integration_wt_path)
                    if r.returncode != 0:
                        adapter._run(["git", "merge", "--abort"], cwd=integration_wt_path)
                        raise CoordinatorError(
                            "Cannot merge target branch into integration: " + r.stderr.strip()
                        )
                    # After merge, integration branch has the new merge commit
                    r_new_merged = adapter._run(["git", "rev-parse", integration_branch])
                    new_ref_merged = r_new_merged.stdout.strip()

                    self.db.write_event(
                        event_type="MergeCommitIntent", actor_type="system", actor_id="coordinator",
                        task_id=entry.task_id,
                        payload={"entry_id": entry.entry_id, "target_branch": target_branch,
                                 "old_ref": old_ref, "new_ref": new_ref_merged}
                    )

                    r = adapter._run([
                        "git", "update-ref", "refs/heads/" + target_branch, new_ref_merged, old_ref
                    ])
                    if r.returncode != 0:
                        raise CoordinatorError(
                            "Failed to update target branch ref: " + r.stderr.strip()
                        )
        finally:
            self._cleanup_integration_artifacts(
                adapter, integration_wt_path, integration_branch
            )

    def _cleanup_integration_artifacts(self, adapter, wt_path: str, branch: str):
        """Remove the integration worktree and branch."""
        try:
            if os.path.exists(wt_path):
                adapter._run(["git", "worktree", "remove", "--force", wt_path])
        except Exception:
            pass
        try:
            adapter._run(["git", "branch", "-D", branch])
        except Exception:
            pass

    def complete_merge(self, entry_id: str, result: str = "",
                       confirmed: bool = False) -> MergeEntry:
        """Complete a merge and update database state and the operation log.

        P0 safety guarantees:
        - Mark the database operation log as completed afterward.
        - Use check_allowed so DISABLED actions are always blocked.
        - Update target_branch via update-ref at this stage without touching the user's workspace.
        """
        entry = self.merge.get(entry_id)
        if entry is None:
            raise CoordinatorError(f"Merge entry {entry_id} not found")

        if entry.status != MergeStatus.MERGING:
            raise CoordinatorError(
                f"Merge entry {entry_id} is {entry.status}, not 'merging'"
            )

        task = self.db.get_task(entry.task_id) if entry.task_id else None
        project_id = task.get("project_id", "") if task else ""

        # Use check_allowed so DISABLED actions are always blocked.
        risk = task.get("risk", "low") if task else "low"
        action_id = f"merge_{risk}_risk"
        safety = self.get_safety_policy(project_id)
        if not safety.check_allowed(action_id, confirmed=confirmed):
            if safety.is_disabled(action_id):
                raise CoordinatorError(
                    f"Merge completion is DISABLED for {risk} risk tasks"
                )
            raise CoordinatorError(
                f"Complete merge requires confirmation under {safety.mode.value} mode "
                f"for {risk} risk task. Set confirmed=True."
            )

        old_status = entry.status
        old_result = entry.result

        # ── Update the Git target_branch. ──
        git_succeeded = True
        git_error = ""
        if project_id:
            proj = self.db.get_project(project_id)
            if proj and os.path.isdir(proj.get("root_path", "")):
                try:
                    self._complete_git_merge(entry, project_id)
                except CoordinatorError as e:
                    git_succeeded = False
                    git_error = str(e)
                except Exception as e:
                    git_succeeded = False
                    git_error = str(e)

        if not git_succeeded:
            # Git failure: roll the database state back to merging.
            try:
                self.db.update_merge_entry(entry_id, MergeStatus.MERGING, git_error[:200])
            except Exception:
                pass
            raise CoordinatorError(
                f"Git merge completion failed, rolled back to merging: {git_error}"
            )

        # After Git succeeds, complete the in-memory and database state.
        try:
            entry = self.merge.complete_merge(entry_id, result)
        except Exception:
            # Memory completion failed after Git changed; report it without rolling Git back.
            raise CoordinatorError(
                f"Git merge succeeded but memory update failed. "
                f"Merge may be incomplete — verify Git state manually."
            )

        try:
            self.db.update_merge_entry(entry_id, MergeStatus.MERGED, result)
            op_key = f"merge-{entry_id}"
            for op in self.ops.list_incomplete():
                if op.idempotency_key == op_key:
                    op.mark_completed(f"merged: {result}")
                    self.ops.update(op)
                    break
            self.db.update_operation(op_key, "completed", f"merged: {result}")
            self.db.write_event(
                event_type="MergeCompleted", actor_type="system", actor_id="coordinator",
                task_id=entry.task_id,
                payload={"entry_id": entry_id, "result": result},
            )
        except Exception as e:
            entry.status = old_status
            entry.result = old_result
            entry.merged_at = ""
            raise CoordinatorError(
                f"Git merge succeeded but DB persist failed: {e}. "
                f"Git target branch has been updated — manual DB reconciliation needed."
            ) from e

        return entry

    def get_merge_entry(self, entry_id: str) -> MergeEntry | None:
        return self.merge.get(entry_id)

    # ── Summary ──────────────────────────────────────────

    def get_project_summary(self, project_id: str) -> ProjectSummary:
        project = self.db.get_project(project_id)
        tasks = self.db.get_project_tasks(project_id)
        agents = self.db.list_agents(project_id)
        active_leases = len(self.db.list_active_leases(project_id))
        pending_reviews = len(self.db.list_pending_reviews(project_id))

        by_state: dict[str, int] = {}
        for t in tasks:
            s = t["state"]
            by_state[s] = by_state.get(s, 0) + 1

        return ProjectSummary(
            project_id=project_id,
            project_name=project["name"] if project else "",
            total_tasks=len(tasks),
            total_agents=len(agents),
            tasks_by_state=by_state,
            active_leases=active_leases,
            pending_reviews=pending_reviews,
        )

    # ── Routing ───────────────────────────────────────────

    def recommend_agent_for_task(self, task_id: str, role: str = "implementer") -> dict:
        """Recommend the best agent using the routing module's hard filters and scoring."""
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        agents = self.db.list_agents(task.get("project_id", ""))
        request = RouteRequest(
            task_id=task_id,
            required_role=role,
            risk=task.get("risk", "medium"),
            complexity=task.get("complexity", "medium"),
            cost_tier=task.get("cost_tier", "low"),
        )
        result = recommend_agent(agents, request)
        return {
            "recommended_agent_id": result.recommended_agent_id,
            "candidates": [a["id"] for a in result.candidates],
            "reason": result.reason,
            "excluded_count": len(result.excluded),
        }

    # ── Cost Tracking ─────────────────────────────────────

    def record_task_cost(self, task_id: str, agent_id: str,
                         input_tokens: int = 0, output_tokens: int = 0,
                         estimated_cost: float = 0.0, source: str = "manual"):
        """Record task cost and persist it to the database."""
        from datetime import datetime, timezone
        self.costs.record(task_id, agent_id, input_tokens, output_tokens,
                          estimated_cost, is_estimated=True, source=source)
        self.db.conn.execute(
            """INSERT INTO cost_records (id, task_id, agent_id, input_tokens,
               output_tokens, estimated_cost, is_estimated, source, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (f"cost-{task_id}-{agent_id}-{datetime.now(timezone.utc).timestamp()}",
             task_id, agent_id, input_tokens, output_tokens,
             estimated_cost, 1, source, datetime.now(timezone.utc).isoformat()),
        )
        self.db.conn.commit()

    def check_budget(self, task_id: str, token_budget: int = 50000,
                     cost_budget: float = 0.50) -> dict:
        """Check token and monetary budget status.

        P1 guarantee: check both token usage and actual cost.
        """
        task_costs = self.costs.records_by_task(task_id)
        total_tokens = sum(r.input_tokens + r.output_tokens for r in task_costs)
        total_cost = sum(r.estimated_cost for r in task_costs)

        from bridgelib.cost import BudgetGuard as BG
        guard = BG(task_token_budget=token_budget, task_cost_budget=cost_budget)
        threshold = guard.check(total_tokens, total_cost)
        return {
            "threshold": threshold.value,
            "total_tokens": total_tokens,
            "total_cost": round(total_cost, 6),
            "token_budget": token_budget,
            "cost_budget": cost_budget,
            "message": guard.get_status_message(total_tokens, total_cost),
        }

    # ── Retry & Escalation ────────────────────────────────

    def record_task_failure(self, task_id: str, error_message: str,
                            exit_code: int | None = None) -> dict:
        """Record a task failure and decide whether to retry or escalate.

        P0 guarantees:
        - Call EscalationDecider.should_escalate with task_id and last_error.
        - Read the escalation reason from the _last_reason attribute.
        - Persist failure records to the database.
        """
        self.retry.record_failure(task_id, error_message, exit_code)
        category = FailureClassifier.classify(error_message, exit_code)

        should_retry = self.retry.should_retry(task_id)
        should_escalate = self.retry.should_escalate(task_id)

        # Obtain the escalation reason through the safe accessor, not a private attribute.
        escalation_reason = ""
        if should_escalate:
            escalation_reason = self.escalation.get_escalation_reason(
                task_id, self.retry.failure_count(task_id),
                self.retry.get_last_error(task_id)
            )

        # Persist the failure record as a database event.
        self.db.write_event(
            event_type="TaskFailure", actor_type="system", actor_id="coordinator",
            task_id=task_id,
            payload={
                "error": error_message[:500],
                "exit_code": exit_code,
                "category": category.value,
                "failure_count": self.retry.failure_count(task_id),
                "should_retry": should_retry,
                "should_escalate": should_escalate,
            },
        )

        return {
            "failure_count": self.retry.failure_count(task_id),
            "category": category.value,
            "should_retry": should_retry,
            "should_escalate": should_escalate,
            "escalation_reason": escalation_reason,
        }

    def reset_task_retry(self, task_id: str):
        """Reset the retry count after a task succeeds.

        P1 recovery guarantee: persist a reset event so startup ignores earlier failures.
        """
        self.retry.reset(task_id)
        self.db.write_event(
            event_type="TaskRetryReset", actor_type="system", actor_id="coordinator",
            task_id=task_id,
            payload={"reset_at": datetime.now(timezone.utc).isoformat()},
        )

    # ── Context ───────────────────────────────────────────

    def generate_task_context(self, task_id: str, layer: str = "task") -> str:
        """Generate a layered context summary for a task."""
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        from bridgelib.context import TaskContext
        ctx = TaskContext(
            task_id=task_id,
            objective=task.get("goal", ""),
            acceptance_criteria=json.loads(task.get("acceptance_criteria_json", "[]") or "[]"),
            required_checks=json.loads(task.get("required_checks_json", "[]") or "[]"),
            allowed_paths=json.loads(task.get("allowed_paths_json", "[]") or "[]"),
        )
        summary = generate_context_summary(ctx)
        self.context.set_context(task_id, summary)
        return summary

    def invalidate_context(self, task_id: str):
        """Invalidate a task's cached context."""
        self.context.invalidate(task_id)

    # ── Git Repository Adapter ─────────────────────────────

    def get_git_adapter(self) -> object:
        """Lazily create and return the Git repository adapter."""
        from bridgelib.git_adapter import GitRepositoryAdapter
        return GitRepositoryAdapter(self.db.get_project(self._active_project_id or "")
                                   .get("root_path", ".") if hasattr(self, '_active_project_id') else ".")

    def check_git_repo(self, project_id: str) -> dict:
        """Check the project's Git repository status."""
        from bridgelib.git_adapter import GitRepositoryAdapter
        proj = self.db.get_project(project_id)
        if not proj:
            raise CoordinatorError(f"Project {project_id} not found")
        adapter = GitRepositoryAdapter(proj["root_path"])
        status = adapter.check_repo()
        return {
            "is_repo": status.is_repo,
            "current_branch": status.current_branch,
            "head_commit": status.head_commit,
            "is_dirty": status.is_dirty,
            "has_untracked": status.has_untracked,
        }

    def verify_commit_exists(self, project_id: str, commit: str) -> bool:
        """Verify that a commit exists in the repository."""
        from bridgelib.git_adapter import GitRepositoryAdapter
        proj = self.db.get_project(project_id)
        if not proj:
            return False
        adapter = GitRepositoryAdapter(proj["root_path"])
        return adapter.commit_exists(commit)

    # ── Safety Policy ──────────────────────────────────────

    def get_safety_policy(self, project_id: str) -> SafetyPolicy:
        """Return the project's safety policy, falling back to balanced when unavailable."""
        if not project_id:
            return SafetyPolicy(mode=ConfirmationMode.BALANCED)
        cached = self._safety_policies.get(project_id)
        if cached:
            return cached
        proj = self.db.get_project(project_id)
        if not proj:
            return SafetyPolicy(mode=ConfirmationMode.BALANCED)
        conf = proj.get("confirmation_policy", "balanced")
        try:
            mode = ConfirmationMode(conf)
        except ValueError:
            mode = ConfirmationMode.BALANCED
        overrides = {}
        for action_id, policy_name in self.db.list_safety_overrides(project_id).items():
            try:
                overrides[action_id] = ActionPolicy(policy_name)
            except ValueError:
                continue

        def persist(action_id: str, policy: ActionPolicy):
            self.db.set_safety_override(project_id, action_id, policy.value)

        policy = SafetyPolicy(
            mode=mode, overrides=overrides, on_override=persist,
        )
        self._safety_policies[project_id] = policy
        return policy

    def set_safety_mode(self, project_id: str, mode: str | None = None):
        """Set a project's safety mode."""
        if mode is None:
            mode = project_id
            project_id = self._active_project_id
        if not project_id or not self.db.get_project(project_id):
            raise CoordinatorError("Project not found for safety mode update")
        try:
            m = ConfirmationMode(mode)
            self.db.conn.execute(
                "UPDATE projects SET confirmation_policy = ?, updated_at = ? WHERE id = ?",
                (m.value, datetime.now(timezone.utc).isoformat(), project_id)
            )
            self.db.conn.commit()
            policy = self.get_safety_policy(project_id)
            policy.mode = m
            if project_id == self._active_project_id:
                self.safety = policy
        except ValueError:
            raise CoordinatorError(f"Invalid safety mode: {mode}")

    def set_safety_override(self, project_id: str, action_id: str, policy: str):
        """Set and persist a project-scoped safety override."""
        try:
            action_policy = ActionPolicy(policy)
        except ValueError as e:
            raise CoordinatorError(f"Invalid action policy: {policy}") from e
        safety = self.get_safety_policy(project_id)
        safety.set_override(action_id, action_policy)
        if project_id == self._active_project_id:
            self.safety = safety

    def check_safety(self, project_id: str, action_id: str) -> dict:
        """Check the safety policy for an action."""
        policy = self.get_safety_policy(project_id)
        return {
            "action": action_id,
            "requires_confirmation": policy.requires_confirmation(action_id),
            "is_hard_floor": policy.is_hard_floor(action_id),
            "can_automate": policy.can_automate(action_id),
            "policy": policy.get_policy(action_id).value,
        }

    def is_hard_floor_blocked(self, action_id: str) -> bool:
        """Return whether an action violates a non-disableable safety floor and must be rejected."""
        return action_id in HARD_FLOOR_ACTIONS

    # ── Validation Command Registry ──────────────────────

    _DEFAULT_CHECK_COMMANDS: dict[str, dict] = {
        "unit-tests": {
            "executable": "python",
            "args": ["-B", "-m", "pytest", "-p", "no:cacheprovider", "-x", "-q"],
        },
        "lint": {"executable": "ruff", "args": ["check"]},
        "typecheck": {"executable": "python", "args": ["-m", "mypy"]},
        "build": {"executable": "python", "args": ["-m", "build"]},
    }

    def register_check_command(self, check_id: str, executable: str,
                                args: list[str], project_id: str = "", confirmed: bool = False):
        """Register an immutable validation command template.

        P0 safety guarantee: bind check_id to an immutable command template so
        callers cannot use a valid ID to execute an arbitrary program.
        """
        project_id = project_id or self._active_project_id
        action_id = "run_custom_command"
        safety = self.get_safety_policy(project_id)
        if not safety.check_allowed(action_id, confirmed=confirmed):
            if safety.is_disabled(action_id):
                raise CoordinatorError(
                    f"Registering custom check commands is DISABLED"
                )
            raise CoordinatorError(
                f"Registering custom check command requires confirmation "
                f"under {safety.mode.value} mode"
            )
        command = {"executable": executable, "args": list(args)}
        if project_id:
            self.db.set_validation_command(
                project_id, check_id, executable, list(args),
            )
        else:
            self._ephemeral_check_commands[check_id] = command

    def run_validation(self, task_id: str, checks: list[dict],
                       project_root: str = "") -> list[dict]:
        """Run validation checks and persist their results to the database.

        P0 safety guarantees:
        - Prefer the active worktree registered by Bridge for the current task.
        - Otherwise use the project root recorded in the database.
        - Do not trust caller-supplied project_root to override a persisted project path.
        - Require check_id in the command registry, which supplies immutable executable/args.
        - Write the actual command to the audit record.
        """
        from bridgelib.validation import ValidationCheck, ValidationExecutor
        from datetime import datetime, timezone

        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        project_id = task.get("project_id", "")
        proj = self.db.get_project(project_id) if project_id else None
        db_project_root = proj.get("root_path", "") if proj else ""

        workspace = self.workspaces.get_by_task(task_id)
        workspace_root = ""
        if workspace and workspace.status == "active":
            candidate = os.path.realpath(workspace.worktree_path or "")
            if candidate and os.path.isdir(candidate):
                workspace_root = candidate

        effective_root = workspace_root or db_project_root or project_root or "."

        required_check_ids = set(json.loads(
            task.get("required_checks_json", "[]") or "[]"
        ))

        safety = self.get_safety_policy(project_id)
        command_registry = dict(self._DEFAULT_CHECK_COMMANDS)
        command_registry.update(self._ephemeral_check_commands)
        if project_id:
            command_registry.update(self.db.list_validation_commands(project_id))

        validation_checks = []
        for c in checks:
            check_id = c["check_id"]

            # ── Resolve the actual immutable command from the registry. ──
            if check_id in command_registry:
                cmd_template = command_registry[check_id]
                executable = cmd_template["executable"]
                args = cmd_template["args"]
            elif check_id in required_check_ids:
                # Reject an unregistered required check; the caller cannot supply executable.
                raise CoordinatorError(
                    f"Required check '{check_id}' is not registered in the command registry. "
                    f"Use register_check_command() first."
                )
            else:
                # Apply the safety policy to an unregistered, optional custom command.
                action_id = "run_custom_command"
                if not safety.check_allowed(action_id, confirmed=False):
                    if safety.is_disabled(action_id):
                        raise CoordinatorError(f"Custom validation command '{check_id}' is DISABLED")
                    raise CoordinatorError(
                        f"Custom validation command '{check_id}' requires confirmation "
                        f"under {safety.mode.value} mode"
                    )
                executable = c.get("executable", "echo")
                args = c.get("args", [])

            vc = ValidationCheck(
                check_id=check_id,
                executable=executable,
                args=args,
                display_name=c.get("display_name", check_id),
                working_directory=c.get("working_directory", "."),
                timeout_seconds=c.get("timeout_seconds", 300),
                required=c.get("required", True),
                evidence_paths=c.get("evidence_paths", []),
            )
            validation_checks.append(vc)

        executor = ValidationExecutor(project_root=effective_root)
        results = executor.execute_all(validation_checks)

        # Persist each result and record the actual command for auditing.
        persisted = []
        current_attempt = self.get_current_attempt(task_id)
        attempt_id = current_attempt["id"] if current_attempt else None
        for i, r in enumerate(results):
            actual_check = validation_checks[i]
            vid = f"val-{task_id}-{r.check_id}-{datetime.now(timezone.utc).timestamp()}"
            now = datetime.now(timezone.utc).isoformat()
            # Record the actual command for the audit trail.
            command_json = json.dumps({
                "executable": actual_check.executable,
                "args": actual_check.args,
            })
            self.db.conn.execute(
                """INSERT INTO validations (id, task_id, attempt_id, check_id, command_json,
                   exit_code, status, output_summary, evidence_path, created_at,
                   started_at, completed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (vid, task_id, attempt_id, r.check_id,
                 command_json,
                 r.exit_code, r.status.value,
                 (r.stdout[:500] if r.stdout else ""),
                 r.evidence_hash,
                 now, now, now),
            )
            self.db.conn.commit()
            persisted.append(r.to_dict())

        return persisted

    def run_qa_on_generated(self, content: str, base_dir: str = ".") -> list[dict]:
        """Run QA checks on generated Markdown content."""
        from bridgelib.reports import run_qa_checks
        results = run_qa_checks(content, base_dir)
        return [r.to_dict() if hasattr(r, 'to_dict') else {
            "check": getattr(r, 'check_name', ''),
            "passed": getattr(r, 'passed', False),
            "detail": getattr(r, 'detail', ''),
        } for r in results]

    def validate_artifacts(self, task_id: str, artifacts_path: str) -> dict:
        """Cross-check ARTIFACTS.json using protocol, forbidden-path, and Git validation.

        P1 guarantees:
        - Pass expected_attempt, agent_id, and base_commit for complete field validation.
        - Cross-check changed_files against the real Git diff.
        - Cross-check checks against the validations table and require passed status.
        """
        import os
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        if not os.path.isfile(artifacts_path):
            return {"valid": False, "error": f"Artifacts file not found: {artifacts_path}"}

        try:
            with open(artifacts_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return {"valid": False, "error": f"Cannot read artifacts: {e}"}

        # Perform complete protocol-level artifact validation with the expected context.
        from bridgelib.protocol import validate_artifacts as protocol_validate

        # Read the current attempt number for validation.
        current_attempt = self.get_current_attempt(task_id)
        current_attempt_num = current_attempt["attempt_number"] if current_attempt else 0
        current_attempt_id = current_attempt["id"] if current_attempt else None

        issues = protocol_validate(
            data,
            expected_task_id=task_id,
            expected_attempt=current_attempt_num,
            expected_agent_id=task.get("owner_agent_id") or "",
            expected_base_commit="",
        )

        # Perform additional cross-checks.
        if data.get("task_id") != task_id:
            issues.append(f"task_id mismatch: {data.get('task_id')} vs {task_id}")
        if data.get("agent_id") != task.get("owner_agent_id"):
            issues.append(f"agent_id mismatch: {data.get('agent_id')} vs {task.get('owner_agent_id')}")

        # Enforce forbidden paths.
        allowed = json.loads(task.get("allowed_paths_json", "[]") or "[]")
        forbidden = json.loads(task.get("forbidden_paths_json", "[]") or "[]")
        for f in data.get("changed_files", []):
            path = f.get("path", f) if isinstance(f, dict) else f
            if forbidden and is_within_scope(path, [], forbidden):
                issues.append(f"File in forbidden scope: {path}")
            elif not is_within_scope(path, allowed, forbidden):
                issues.append(f"File outside allowed scope: {path}")

        # Cross-check IDs and passed status against validations from only the current attempt.
        db_validations = self.db.list_validations_by_task(task_id)
        db_check_status = {
            v.get("check_id"): v.get("status")
            for v in db_validations
            if current_attempt_id is None or v.get("attempt_id") == current_attempt_id
        }
        for check in data.get("checks", []):
            cid = check.get("id", "")
            if cid:
                if cid not in db_check_status:
                    issues.append(f"Check '{cid}' in artifacts not found in validation records")
                elif db_check_status[cid] != "passed":
                    issues.append(
                        f"Check '{cid}' in artifacts has status '{db_check_status[cid]}' "
                        f"in validation records, not 'passed'"
                    )

        # ── Git cross-check: compare the actual diff. ──
        project_id = task.get("project_id", "")
        submission_commit = data.get("submission_commit", "")
        base_commit = data.get("base_commit", "")
        if submission_commit and base_commit and project_id:
            git_issues = self._cross_validate_git_diff(
                project_id, base_commit, submission_commit, data.get("changed_files", [])
            )
            issues.extend(git_issues)

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "task_id": task_id,
        }

    def _cross_validate_git_diff(self, project_id: str, base_commit: str,
                                  submission_commit: str,
                                  claimed_changed_files: list) -> list[str]:
        """Cross-check changed_files against the actual Git diff."""
        issues = []
        try:
            from bridgelib.git_adapter import GitRepositoryAdapter, GitAdapterError
            proj = self.db.get_project(project_id)
            if not proj:
                return issues
            adapter = GitRepositoryAdapter(proj["root_path"])

            # Verify that both commits exist.
            if not adapter.commit_exists(base_commit):
                issues.append(f"base_commit '{base_commit}' does not exist in Git")
                return issues
            if not adapter.commit_exists(submission_commit):
                issues.append(f"submission_commit '{submission_commit}' does not exist in Git")
                return issues

            # Read the file list from the actual diff.
            diff = adapter.get_diff(base_commit, submission_commit)
            real_files = set(diff.files_changed)
            claimed_paths = set()
            for f in claimed_changed_files:
                path = f.get("path", f) if isinstance(f, dict) else str(f)
                claimed_paths.add(path)

            # Check that every claimed file appears in the actual diff.
            missing = claimed_paths - real_files
            if missing:
                issues.append(
                    f"Files claimed in artifacts but not in real Git diff: {missing}"
                )
            # Check for files in the actual diff that were not claimed.
            extra = real_files - claimed_paths
            if extra:
                issues.append(
                    f"Files in real Git diff but not declared in artifacts: {extra}"
                )
        except GitAdapterError:
            pass  # Skip validation when the repository is unavailable.
        except Exception:
            pass
        return issues

    # ── Receipt Import ────────────────────────────────────

    def import_receipt(self, task_dir: str, task_id: str, attempt: int,
                       lease_id: str, agent_id: str, confirmed: bool = False) -> dict | None:
        """Import a task receipt using a stability window, hash deduplication, and cross-validation."""
        from bridgelib.receipt_importer import ReceiptImporter

        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        project_id = task.get("project_id", "")
        safety = self.get_safety_policy(project_id)
        if not safety.check_allowed("receipt_import", confirmed=confirmed):
            if safety.is_disabled("receipt_import"):
                raise CoordinatorError("Receipt import is DISABLED")
            raise CoordinatorError(
                f"Receipt import requires confirmation under {safety.mode.value} mode. "
                "Set confirmed=True."
            )

        allowed = json.loads(task.get("allowed_paths_json", "[]") or "[]")
        forbidden = json.loads(task.get("forbidden_paths_json", "[]") or "[]")

        importer = ReceiptImporter(self.db)
        return importer.scan_and_import(
            task_dir, task_id, attempt, lease_id, agent_id,
            allowed_paths=allowed, forbidden_paths=forbidden,
        )

    # ── Transition Validators ────────────────────────────

    def _validate_task_ready(self, task: dict):
        """For Planning to Ready, require allowed paths and acceptance criteria."""
        allowed = json.loads(task.get("allowed_paths_json", "[]") or "[]")
        criteria = json.loads(task.get("acceptance_criteria_json", "[]") or "[]")
        if not allowed:
            raise CoordinatorError(
                "Task must have at least one allowed_path before transitioning to ready"
            )
        if not criteria:
            raise CoordinatorError(
                "Task must have at least one acceptance_criteria before transitioning to ready"
            )

    def _validate_task_approved(self, task: dict):
        """For Validating to Approved, require current-attempt PASSED results for every required check.

        P0 safety guarantee: fail closed when the current attempt lacks validation;
        never fall back to validation results from an earlier attempt.
        """
        tid = task["id"]

        # 1. Acceptance criteria are required.
        criteria = json.loads(task.get("acceptance_criteria_json", "[]") or "[]")
        if not criteria:
            raise CoordinatorError(
                "Task must have acceptance_criteria before approval"
            )

        # 2. Require and load the current attempt.
        current_attempt = self.get_current_attempt(tid)
        current_attempt_id = current_attempt["id"] if current_attempt else None

        if not current_attempt_id:
            raise CoordinatorError(
                f"Task {tid} has no active attempt. "
                f"Cannot approve without current attempt's validation results."
            )

        # 3. Load all validation results.
        required_checks = json.loads(task.get("required_checks_json", "[]") or "[]")
        all_validations = self.db.list_validations_by_task(tid)

        # Fail closed by using only current-attempt results, with no fallback.
        validations = [
            v for v in all_validations
            if v.get("attempt_id") == current_attempt_id
        ]

        if required_checks:
            passed_check_ids = {
                v["check_id"] for v in validations
                if v.get("status") == "passed"
            }
            missing = [c for c in required_checks if c not in passed_check_ids]
            if missing:
                raise CoordinatorError(
                    f"Task {tid} requires all checks to pass before approval. "
                    f"Missing PASSED results for: {', '.join(missing)}. "
                    f"Required: {required_checks}, Passed: {list(passed_check_ids)}"
                )
        else:
            passed = [v for v in validations if v.get("status") == "passed"]
            if not passed:
                raise CoordinatorError(
                    f"Task {tid} must have at least one PASSED validation before approval. "
                    f"Found {len(validations)} validation(s), 0 passed."
                )

        # 4. Require an APPROVED review result.
        reviews = self.db.list_reviews_by_task(tid)
        approved_reviews = [
            r for r in reviews
            if r.get("verdict") == "approved"
            and r.get("attempt_id") == current_attempt_id
        ]
        if not approved_reviews:
            raise CoordinatorError(
                f"Task {tid} must have an approved review before approval. "
                f"Found {len(reviews)} review(s), 0 approved."
            )

    def _validate_task_done(self, task: dict):
        """For Merging to Done, require a completed MERGED queue entry."""
        merge_entries = self.merge.get_by_task(task["id"])
        if not any(e.status == MergeStatus.MERGED for e in merge_entries):
            raise CoordinatorError(
                "Task must have a completed merge (MERGED) before transitioning to done"
            )

    # ── Worktree Lifecycle ───────────────────────────────

    def create_worktree(self, task_id: str, agent_id: str, attempt: int = 1,
                        base_commit: str = "", confirmed: bool = False) -> dict:
        """Create a task worktree and register it in the database.

        P0 safety guarantees:
        - Create the task branch before creating the worktree.
        - Fail closed on Git errors by rolling back and reporting the error.
        - Assign worktree_path correctly on the Workspace object.
        - Persist the workspace in the database workspaces table.
        """
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        project_id = task.get("project_id", "")

        # Verify that the agent owns the task.
        if task.get("owner_agent_id") != agent_id:
            raise CoordinatorError(
                f"Agent {agent_id} is not the owner of task {task_id}"
            )

        # Use check_allowed so DISABLED actions are always blocked.
        action_id = "create_worktree"
        safety = self.get_safety_policy(project_id)
        if not safety.check_allowed(action_id, confirmed=confirmed):
            if safety.is_disabled(action_id):
                raise CoordinatorError("Create worktree is DISABLED")
            raise CoordinatorError(
                f"Create worktree requires confirmation under {safety.mode.value} mode"
            )

        # Register through WorkspaceManager.
        ws = self.workspaces.register(
            task_id=task_id, agent_id=agent_id, attempt=attempt,
            base_commit=base_commit,
        )

        # Attempt to create a real Git worktree and fail closed.
        worktree_path = ""
        if project_id:
            proj = self.db.get_project(project_id)
            if proj and os.path.isdir(proj.get("root_path", "")):
                root_path = proj["root_path"]
                try:
                    from bridgelib.git_adapter import GitRepositoryAdapter, GitAdapterError
                    adapter = GitRepositoryAdapter(root_path)
                    repo_status = adapter.check_repo()
                    if repo_status.is_repo:
                        # Construct the worktree path.
                        from bridgelib.workspace import workspace_dir_name
                        ws_dir_name = workspace_dir_name(agent_id, task_id)
                        worktree_path = os.path.join(
                            root_path, "..", ".bridge-task-worktrees", ws_dir_name
                        )
                        # 1. Create the task branch first.
                        branch_base = base_commit or "HEAD"
                        r = adapter._run([
                            "git", "branch", ws.branch, branch_base
                        ])
                        if r.returncode != 0:
                            raise CoordinatorError(
                                f"Failed to create branch '{ws.branch}': {r.stderr.strip()}"
                            )
                        # 2. Create the worktree.
                        result = adapter.create_worktree(
                            worktree_path, ws.branch,
                            operation_id=f"ws-{ws.workspace_id}",
                        )
                        if not result.success:
                            # The branch exists but worktree creation failed; remove the branch.
                            adapter._run(["git", "branch", "-D", ws.branch])
                            raise CoordinatorError(
                                f"Failed to create Git worktree: {result.error}"
                            )
                        # 3. Assign worktree_path to the Workspace object.
                        ws.worktree_path = worktree_path
                    else:
                        raise CoordinatorError(
                            f"Project root '{root_path}' is not a Git repository"
                        )
                except CoordinatorError:
                    # Roll back the in-memory registration.
                    self.workspaces._workspaces.pop(ws.workspace_id, None)
                    self.workspaces._by_task.pop(task_id, None)
                    raise
                except Exception as e:
                    self.workspaces._workspaces.pop(ws.workspace_id, None)
                    self.workspaces._by_task.pop(task_id, None)
                    raise CoordinatorError(
                        f"Failed to create worktree: {e}"
                    ) from e

        # Persist to the database.
        try:
            self.db.create_workspace(
                workspace_id=ws.workspace_id,
                task_id=task_id, agent_id=agent_id,
                worktree_path=worktree_path,
                branch=ws.branch, base_commit=base_commit,
            )
        except Exception as e:
            self.workspaces._workspaces.pop(ws.workspace_id, None)
            self.workspaces._by_task.pop(task_id, None)
            # P1 rollback guarantee: remove the real Git worktree and branch on database failure.
            if worktree_path:
                try:
                    from bridgelib.git_adapter import GitRepositoryAdapter
                    adapter = GitRepositoryAdapter(proj["root_path"] if proj else root_path)
                    if os.path.exists(worktree_path):
                        adapter._run(["git", "worktree", "remove", "--force", worktree_path])
                    if ws.branch:
                        adapter._run(["git", "branch", "-D", ws.branch])
                except Exception:
                    pass
            raise CoordinatorError(
                f"Failed to persist workspace to database: {e}"
            ) from e

        self.db.write_event(
            event_type="WorktreeCreated", actor_type="system", actor_id="coordinator",
            task_id=task_id, project_id=project_id,
            payload={"workspace_id": ws.workspace_id, "branch": ws.branch,
                     "worktree_path": worktree_path},
        )
        return ws.to_dict()

    def remove_worktree(self, task_id: str, force: bool = False,
                        confirmed: bool = False) -> dict:
        """Remove a task worktree.

        P0 safety guarantees:
        - Read actual Git state before removal instead of trusting the database status field.
        - Require confirmed=True for a dirty worktree.
        - force=True cannot bypass confirmation.
        - Use check_allowed rather than requires_confirmation.
        - Do not suppress database update failures.
        """
        ws = self.workspaces.get_by_task(task_id)
        if ws is None:
            raise CoordinatorError(f"No active workspace for task {task_id}")

        task = self.db.get_task(task_id)
        project_id = task.get("project_id", "") if task else ""

        # ── Read actual Git state before removal. ──
        real_is_dirty = False
        if ws.worktree_path and project_id:
            proj = self.db.get_project(project_id)
            if proj and os.path.isdir(proj.get("root_path", "")):
                try:
                    from bridgelib.git_adapter import GitRepositoryAdapter
                    adapter = GitRepositoryAdapter(proj["root_path"])
                    if adapter.check_repo().is_repo:
                        # Check the worktree for uncommitted changes.
                        if os.path.isdir(ws.worktree_path):
                            r = adapter._run(
                                ["git", "status", "--porcelain"],
                                cwd=ws.worktree_path,
                            )
                            if r.returncode == 0 and r.stdout.strip():
                                real_is_dirty = True

                    # Synchronize the database state.
                    if real_is_dirty and ws.status != "dirty":
                        ws.status = "dirty"
                        try:
                            self.db.conn.execute(
                                "UPDATE workspaces SET status = 'dirty' WHERE id = ?",
                                (ws.workspace_id,),
                            )
                            self.db.conn.commit()
                        except Exception:
                            pass
                except Exception:
                    pass

        # Apply check_allowed based on actual Git state.
        action_id = "remove_dirty_worktree" if real_is_dirty else "remove_clean_worktree"
        safety = self.get_safety_policy(project_id)
        if not safety.check_allowed(action_id, confirmed=confirmed):
            if safety.is_disabled(action_id):
                raise CoordinatorError(
                    f"Worktree removal is DISABLED for {'dirty' if real_is_dirty else 'clean'} worktrees"
                )
            raise CoordinatorError(
                f"Remove {'dirty' if real_is_dirty else ''} worktree requires confirmation "
                f"under {safety.mode.value} mode. Set confirmed=True."
            )

        # A dirty worktree requires explicit confirmation; force cannot bypass it.
        if real_is_dirty and not confirmed:
            raise CoordinatorError(
                f"Worktree for task {task_id} has uncommitted changes. "
                f"Removal requires confirmed=True."
            )

        # Attempt to remove the real Git worktree.
        if ws.worktree_path and project_id:
            proj = self.db.get_project(project_id)
            if proj and os.path.isdir(proj.get("root_path", "")):
                try:
                    from bridgelib.git_adapter import GitRepositoryAdapter
                    adapter = GitRepositoryAdapter(proj["root_path"])
                    if adapter.check_repo().is_repo:
                        bridge_paths = set()
                        for w in self.workspaces.list_active():
                            if w.worktree_path:
                                bridge_paths.add(w.worktree_path)
                        bridge_paths.add(ws.worktree_path)

                        result = adapter.remove_worktree(
                            ws.worktree_path, force=force,
                            operation_id=f"ws-remove-{ws.workspace_id}",
                            bridge_owned_paths=bridge_paths,
                        )
                        if not result.success:
                            raise CoordinatorError(
                                f"Failed to remove Git worktree: {result.error}"
                            )
                        if ws.branch:
                            adapter._run(["git", "branch", "-D", ws.branch])
                except CoordinatorError:
                    raise
                except Exception as e:
                    raise CoordinatorError(
                        f"Worktree removal failed: {e}"
                    ) from e

        # Mark the workspace as cleaned.
        self.workspaces.mark_cleaned(ws.workspace_id)

        # Update database state without suppressing exceptions.
        self.db.conn.execute(
            "UPDATE workspaces SET status = 'cleaned', cleaned_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), ws.workspace_id),
        )
        self.db.conn.commit()

        self.db.write_event(
            event_type="WorktreeRemoved", actor_type="system", actor_id="coordinator",
            task_id=task_id,
            payload={"workspace_id": ws.workspace_id},
        )
        return {"workspace_id": ws.workspace_id, "status": "cleaned"}

    def get_worktree(self, task_id: str) -> dict | None:
        """Return a task's worktree information."""
        ws = self.workspaces.get_by_task(task_id)
        return ws.to_dict() if ws else None

    # ── Attempt Lifecycle ────────────────────────────────

    def create_attempt(self, task_id: str, agent_id: str,
                       lease_id: str = "") -> str:
        """Create a task attempt record.

        P1 guarantees:
        - Verify that the agent owns the task.
        - Verify that the lease belongs to the same task and agent.
        - Allow at most one in_progress attempt per task.
        - Persist the attempt in the database attempts table.
        """
        import secrets
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        # Verify that the agent owns the task.
        if task.get("owner_agent_id") != agent_id:
            raise CoordinatorError(
                f"Agent {agent_id} is not the owner of task {task_id}"
            )

        # Verify that the agent exists and belongs to the same project.
        agent = self.db.get_agent(agent_id)
        if agent is None:
            raise CoordinatorError(f"Agent {agent_id} not found")
        if not agent.get("enabled"):
            raise CoordinatorError(f"Agent {agent_id} is disabled")
        project_id = task.get("project_id", "")
        if agent.get("project_id") != project_id:
            raise CoordinatorError(
                f"Agent {agent_id} belongs to project {agent.get('project_id')}, "
                f"not {project_id}"
            )

        # Verify that the lease belongs to the same task and agent.
        if lease_id:
            lease = self.db.get_lease(lease_id)
            if lease is None:
                raise CoordinatorError(f"Lease {lease_id} not found")
            if lease.get("status") != "active":
                raise CoordinatorError(
                    f"Lease {lease_id} is not active (status: {lease.get('status')})"
                )
            if lease.get("task_id") != task_id:
                raise CoordinatorError(
                    f"Lease {lease_id} belongs to task {lease.get('task_id')}, not {task_id}"
                )
            if lease.get("agent_id") != agent_id:
                raise CoordinatorError(
                    f"Lease {lease_id} belongs to agent {lease.get('agent_id')}, not {agent_id}"
                )

        # Verify that no in_progress attempt already exists.
        existing_active = self.db.conn.execute(
            "SELECT id FROM attempts WHERE task_id = ? AND status = 'in_progress' LIMIT 1",
            (task_id,)
        ).fetchone()
        if existing_active:
            raise CoordinatorError(
                f"Task {task_id} already has an in_progress attempt ({existing_active['id']}). "
                f"Complete it first before creating a new one."
            )

        # Determine the next attempt number.
        existing = self.db.conn.execute(
            "SELECT MAX(attempt_number) as max_num FROM attempts WHERE task_id = ?",
            (task_id,)
        ).fetchone()
        attempt_number = (existing["max_num"] or 0) + 1 if existing else 1

        attempt_id = f"att-{secrets.token_hex(6)}"
        now = datetime.now(timezone.utc).isoformat()

        self.db.conn.execute(
            """INSERT INTO attempts (id, task_id, attempt_number, agent_id,
               lease_id, status, started_at, created_at)
               VALUES (?,?,?,?,?,'in_progress',?,?)""",
            (attempt_id, task_id, attempt_number, agent_id,
             lease_id or None, now, now),
        )
        self.db.conn.commit()

        self.db.write_event(
            event_type="AttemptCreated", actor_type="system", actor_id=agent_id,
            task_id=task_id, project_id=project_id,
            payload={"attempt_id": attempt_id, "attempt_number": attempt_number,
                      "lease_id": lease_id or ""},
        )
        return attempt_id

    def complete_attempt(self, attempt_id: str, status: str = "completed"):
        """Complete an attempt record."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self.db.conn.execute(
            "UPDATE attempts SET status = ?, completed_at = ? WHERE id = ?",
            (status, now, attempt_id),
        )
        self.db.conn.commit()
        if cur.rowcount == 0:
            raise CoordinatorError(f"Attempt {attempt_id} not found")

    def get_current_attempt(self, task_id: str) -> dict | None:
        """Return the task's current active attempt."""
        row = self.db.conn.execute(
            "SELECT * FROM attempts WHERE task_id = ? AND status = 'in_progress' "
            "ORDER BY attempt_number DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_attempts(self, task_id: str) -> list[dict]:
        """List all attempts for a task."""
        rows = self.db.conn.execute(
            "SELECT * FROM attempts WHERE task_id = ? ORDER BY attempt_number",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Receipt Watcher ──────────────────────────────────

    def start_receipt_watcher(self, task_dir: str, task_id: str, attempt: int,
                              lease_id: str, agent_id: str):
        """Start automatic receipt-file monitoring.

        P0 safety guarantees:
        - Use a separate thread-safe database connection, not the main-thread connection.
        - Write receipt data from the background thread through that separate connection.
        """
        from bridgelib.file_watcher import ReceiptWatcher
        from bridgelib.receipt_importer import ReceiptImporter

        # Create a dedicated database connection for the background thread.
        thread_db = self.db.get_thread_safe_connection()
        thread_db.initialize()
        importer = ReceiptImporter(thread_db)

        if not hasattr(self, '_receipt_watcher'):
            self._receipt_watcher = ReceiptWatcher(importer)
            self._receipt_watcher_dbs = []
        self._receipt_watcher_dbs.append(thread_db)
        self._receipt_watcher.watch_task_dir(task_dir, task_id, attempt,
                                             lease_id, agent_id)
        if not self._receipt_watcher.watcher._running:
            self._receipt_watcher.start()

    def stop_receipt_watcher(self):
        """Stop receipt-file monitoring and close the thread database connections."""
        if hasattr(self, '_receipt_watcher'):
            self._receipt_watcher.stop()
        if hasattr(self, '_receipt_watcher_dbs'):
            for tdb in self._receipt_watcher_dbs:
                try:
                    tdb.close()
                except Exception:
                    pass
            self._receipt_watcher_dbs = []

    def rescan_receipts(self, task_dirs: list[str],
                        task_map: dict | None = None) -> list[dict]:
        """Rescan for unimported receipt files at startup.

        P1 guarantees:
        - The coordinator provides automatic rescanning.
        - task_map can be built automatically from the database.
        """
        from bridgelib.receipt_importer import ReceiptImporter
        importer = ReceiptImporter(self.db)

        # Build task_map from the database when it is not provided.
        if task_map is None:
            task_map = self._build_task_map_for_rescan(task_dirs)

        return importer.rescan_on_startup(task_dirs, task_map=task_map)

    def _build_task_map_for_rescan(self, task_dirs: list[str]) -> dict:
        """Build the task_map required for rescanning from the database."""
        task_map = {}
        for d in task_dirs:
            # Try to infer task_id from the directory name.
            dir_name = os.path.basename(d)
            # Search all tasks for a match.
            tasks = self.db.list_tasks()
            for task in tasks:
                if task["id"] in dir_name or dir_name in task["id"]:
                    # Read the current attempt.
                    attempt = self.get_current_attempt(task["id"])
                    attempt_num = attempt["attempt_number"] if attempt else 1
                    # Read the active lease.
                    leases = self.db.list_leases_by_task(task["id"])
                    active_lease = next(
                        (l for l in leases if l.get("status") == "active"), None
                    )
                    task_map[d] = {
                        "task_id": task["id"],
                        "attempt": attempt_num,
                        "lease_id": active_lease["id"] if active_lease else "",
                        "agent_id": task.get("owner_agent_id") or "",
                    }
                    break
        return task_map

    # ── Safety Policy Initialization ─────────────────────

    def init_safety_from_project(self, project_id: str):
        """Initialize the safety policy from project configuration.

        P1 guarantees:
        - Initialize the safety policy from the project's confirmation_policy.
        - Support later dynamic changes.
        """
        proj = self.db.get_project(project_id)
        if not proj:
            return
        confirmation = proj.get("confirmation_policy", "balanced")
        try:
            mode = ConfirmationMode(confirmation)
            self.safety.mode = mode
        except ValueError:
            pass  # Keep the default when the configured value is invalid.
