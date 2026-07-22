"""Bridge 协调器核心 — 整合所有子系统，提供统一 API。

设计参考：docs/bridge-design/02-system-architecture.md §Coordinator
"""

import json
from dataclasses import dataclass, field
from bridgelib.database import Database
from bridgelib.state_machine import (
    TaskState, ProgressionPolicy, validate_transition, is_valid_transition,
    StateMachineError,
)
from bridgelib.leases import LeaseManager, Lease
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


class CoordinatorError(Exception):
    """协调器错误"""
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
    """协调器 — 整合数据库、状态机、租约、审查、合并队列、工作区、操作日志。"""

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

        # 从数据库恢复运行时状态（支持重启恢复）
        self._hydrate_from_db()

    def _hydrate_from_db(self):
        """从数据库恢复租约、审查、合并队列到内存管理器。"""
        # ── 恢复租约 ──
        active_leases = self.db.list_active_leases()
        for row in active_leases:
            lease = Lease(
                lease_id=row["id"],
                task_id=row["task_id"],
                agent_id=row["agent_id"],
                attempt_id=row.get("attempt_id") or "",
                resource_type=row.get("resource_type", "task"),
                resource_path=row.get("resource_path", ""),
                status=row.get("status", "active"),
            )
            self.leases._leases[lease.lease_id] = lease

        # ── 恢复审查 ──
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

        # ── 恢复已完成审查结果 ──
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
                    summary=row.get("issues_json", ""),
                    fix_task_id=row.get("fix_task_id"),
                    completed_at=row.get("completed_at", ""),
                )
                self.reviews._results[rid] = result
        except Exception:
            pass

        # ── 恢复合并队列 ──
        merge_rows = self.db.list_queued_merges()
        for row in merge_rows:
            entry = MergeEntry(
                entry_id=row["id"],
                task_id=row["task_id"],
                target_branch=row.get("target_branch", "main"),
                candidate_commit=row.get("candidate_commit", ""),
                queue_position=row.get("queue_position", 0),
                status=row.get("status", "queued"),
                result=row.get("result", ""),
            )
            self.merge._entries[entry.entry_id] = entry
            self.merge._next_position = max(
                self.merge._next_position, entry.queue_position + 1
            )

    # ── Project ───────────────────────────────────────────

    def init_project(self, name: str, root_path: str, language: str = "zh-CN",
                     progression: str = "hybrid", confirmation: str = "balanced") -> str:
        pid = self.db.create_project(
            name=name, root_path=root_path, language=language,
            progression_policy=progression, confirmation_policy=confirmation,
        )
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

        # 强制任务继承 goal 的项目
        project_id = goal["project_id"]
        if kwargs.get("project_id") and kwargs["project_id"] != project_id:
            raise CoordinatorError(
                f"Task project_id ({kwargs['project_id']}) must match "
                f"goal project_id ({project_id})"
            )

        tid = self.db.create_task(
            goal_id=goal_id, title=title,
            project_id=project_id,  # 强制使用 goal 的 project_id
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
        """执行任务状态转换。若未指定策略，从项目配置读取；默认 HYBRID。"""
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        # 优先级：调用参数 > 任务覆盖 > 项目配置 > HYBRID
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

        # 检查确认策略
        if result.requires_confirmation and not confirmed:
            raise CoordinatorError(
                f"Transition {from_state.value} -> {to_state.value} requires user confirmation "
                f"under {policy.value} policy. Set confirmed=True."
            )

        # 业务规则验证（仅针对特定转换）
        if to_state == TaskState.READY:
            self._validate_task_ready(task)
        elif to_state == TaskState.APPROVED:
            self._validate_task_approved(task)
        elif to_state == TaskState.DONE:
            self._validate_task_done(task)

        # 原子：状态更新 + 事件写入
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
        """分配任务给 Agent。校验 Agent 身份/项目/启用/权限/确认策略。"""
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        if task["state"] != TaskState.READY.value:
            raise CoordinatorError(
                f"Task must be in 'ready' state to assign, current: {task['state']}"
            )

        project_id = task.get("project_id", "")

        # ── 确认策略检查（仅 MANUAL 策略强制要求）─────────
        if not confirmed:
            task_policy_str = task.get("progression_policy")
            proj = self.db.get_project(project_id) if project_id else None
            proj_policy_str = proj.get("progression_policy", "") if proj else ""
            policy_str = task_policy_str or proj_policy_str or "hybrid"
            try:
                policy = ProgressionPolicy(policy_str)
            except ValueError:
                policy = ProgressionPolicy.HYBRID
            if policy == ProgressionPolicy.MANUAL:
                raise CoordinatorError(
                    "Task assignment requires confirmation under manual policy. "
                    "Set confirmed=True."
                )

        # ── owner Agent 校验 ───────────────────────────────
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
            pass  # 实现者不需要特殊权限标记

        # ── reviewer Agent 校验 ─────────────────────────────
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

        # ── owner 与 reviewer 不应相同 ─────────────────────
        if reviewer_agent_id and owner_agent_id == reviewer_agent_id:
            raise CoordinatorError("Owner and reviewer must be different agents")

        # 原子操作：状态转换 + owner/reviewer 更新 + 事件
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

        # 检查 agent 是否存在、启用、属于同一项目
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

        # 检查任务是否处于 ASSIGNED 或 IN_PROGRESS 状态
        if task["state"] not in (TaskState.ASSIGNED.value, TaskState.IN_PROGRESS.value):
            raise CoordinatorError(
                f"Task must be in 'assigned' or 'in_progress' state to acquire a lease, "
                f"current: {task['state']}"
            )

        # 检查 agent 是否是任务的 owner
        if task.get("owner_agent_id") != agent_id:
            raise CoordinatorError(
                f"Agent {agent_id} is not the owner of task {task_id}"
            )

        # 如果 resource_path 非空，检查是否在任务的 allowed_paths 内
        if resource_path:
            allowed = json.loads(task.get("allowed_paths_json", "[]") or "[]")
            forbidden = json.loads(task.get("forbidden_paths_json", "[]") or "[]")
            if not is_within_scope(resource_path, allowed, forbidden):
                raise CoordinatorError(
                    f"Resource path '{resource_path}' is not within the allowed scope "
                    f"of task {task_id}"
                )

        # 防止同一任务已有活跃租约时重复获取（检查过期时间）
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
                # 过期租约：不阻止新获取

        # 通过内存管理器获取租约（含路径冲突检查）
        lease = self.leases.acquire(
            task_id=task_id, agent_id=agent_id, resource_type=resource_type,
            resource_path=resource_path, ttl_seconds=ttl_seconds,
        )

        # 持久化到数据库（失败时回滚内存租约）
        try:
            self.db.create_lease(
                lease_id=lease.lease_id, task_id=task_id, agent_id=agent_id,
                resource_type=resource_type, resource_path=resource_path,
                ttl_seconds=ttl_seconds,
            )
        except Exception:
            # DB 失败 → 回滚内存中的租约
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

        # 检查 reviewer agent 是否存在、启用、属于同一项目
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

        # 检查 reviewer 是否有审查权限
        import json as _json
        perms = _json.loads(reviewer.get("permissions_json", "{}"))
        if not perms.get("can_review"):
            raise CoordinatorError(
                f"Reviewer agent {reviewer_agent_id} does not have review permission"
            )

        # 检查任务状态是否合理（至少是 IN_PROGRESS 及以上）
        valid_states = {TaskState.IN_PROGRESS.value, TaskState.SUBMITTED.value,
                        TaskState.VALIDATING.value, TaskState.APPROVED.value,
                        TaskState.MERGE_QUEUED.value, TaskState.MERGING.value}
        if task["state"] not in valid_states:
            raise CoordinatorError(
                f"Task must be in a reviewable state (in_progress/submitted/validating/"
                f"approved/merge_queued/merging), current: {task['state']}"
            )

        # 通过内存管理器提交（生成 request_id）
        req = self.reviews.submit(task_id, reviewer_agent_id, review_package)

        # 持久化到数据库
        self.db.create_review(
            review_id=req.request_id,
            task_id=task_id,
            reviewer_agent_id=reviewer_agent_id,
        )

        self.db.write_event(
            event_type="ReviewRequested", actor_type="user", actor_id="user",
            task_id=task_id,
            payload={"request_id": req.request_id, "reviewer": reviewer_agent_id},
        )
        return req.request_id

    def complete_review(self, request_id: str, verdict: ReviewVerdict,
                        summary: str = "", **kwargs) -> ReviewResult:
        result = self.reviews.complete(request_id, verdict, summary, **kwargs)
        req = self.reviews.get_request(request_id)
        # 持久化到数据库
        import json as _json
        self.db.update_review(
            request_id,
            verdict=verdict.value,
            issues_json=_json.dumps(kwargs.get("issues", [])),
            fix_task_id=kwargs.get("fix_task_id"),
        )
        self.db.write_event(
            event_type="ReviewCompleted", actor_type="system", actor_id="coordinator",
            task_id=req.task_id if req else "",
            payload={"verdict": verdict.value, "summary": summary},
        )
        return result

    # ── Merge Queue ───────────────────────────────────────

    def enqueue_merge(self, task_id: str, candidate_commit: str = "") -> MergeEntry:
        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        # 检查任务是否处于 APPROVED 状态
        if task["state"] != TaskState.APPROVED.value:
            raise CoordinatorError(
                f"Task must be in 'approved' state to enqueue merge, "
                f"current: {task['state']}"
            )

        # candidate_commit 不能为空
        if not candidate_commit:
            raise CoordinatorError("candidate_commit must not be empty")

        # 通过内存管理器入队（生成 entry_id）
        entry = self.merge.enqueue(task_id, candidate_commit)

        # 持久化到数据库
        self.db.create_merge_entry(
            entry_id=entry.entry_id,
            task_id=task_id,
            target_branch=self.merge.target_branch,
            candidate_commit=candidate_commit,
            queue_position=entry.queue_position,
        )

        self.db.write_event(
            event_type="MergeQueued", actor_type="system", actor_id="coordinator",
            task_id=task_id,
            payload={"entry_id": entry.entry_id, "commit": candidate_commit},
        )
        return entry

    def start_merge(self, entry_id: str) -> MergeEntry:
        op = OperationEntry.prepare(
            "merge", task_id="",
            idempotency_key=f"merge-{entry_id}",
            target=f"entry:{entry_id}",
        )
        self.ops.record(op)
        # 持久化操作日志到数据库
        self.db.create_operation(
            op_id=op.idempotency_key,
            op_type="merge",
            idempotency_key=op.idempotency_key,
            target=op.target,
            status="prepared",
        )
        try:
            entry = self.merge.start_merge(entry_id)
            # 更新数据库状态为 merging
            self.db.update_merge_entry(entry_id, MergeStatus.MERGING)
            return entry
        except Exception as e:
            op.mark_failed(repr(e))
            self.ops.update(op)
            raise

    def complete_merge(self, entry_id: str, result: str = "") -> MergeEntry:
        # 检查合并条目存在于内存中
        entry = self.merge.get(entry_id)
        if entry is None:
            raise CoordinatorError(f"Merge entry {entry_id} not found")

        # 检查合并条目是否处于 MERGING 状态
        if entry.status != MergeStatus.MERGING:
            raise CoordinatorError(
                f"Merge entry {entry_id} is {entry.status}, not 'merging'"
            )

        # 完成内存中的合并
        entry = self.merge.complete_merge(entry_id, result)

        # 更新数据库状态
        self.db.update_merge_entry(entry_id, MergeStatus.MERGED, result)

        # 查找对应的操作日志并标记完成
        op_key = f"merge-{entry_id}"
        for op in self.ops.list_incomplete():
            if op.idempotency_key == op_key:
                op.mark_completed(f"merged: {result}")
                self.ops.update(op)
                break
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
        """为任务推荐最佳 Agent，使用路由模块的硬过滤+评分。"""
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
        """记录任务成本并持久化到数据库。"""
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

    def check_budget(self, task_id: str, token_budget: int = 50000) -> dict:
        """检查预算状态。使用传入的 token_budget 创建临时 BudgetGuard。"""
        task_costs = self.costs.records_by_task(task_id)
        total = sum(r.input_tokens + r.output_tokens for r in task_costs)
        # 使用传入的预算参数创建正确的 BudgetGuard
        from bridgelib.cost import BudgetGuard as BG
        guard = BG(task_token_budget=token_budget)
        threshold = guard.check(total, 0.0)
        return {
            "threshold": threshold.value,
            "total_tokens": total,
            "budget": token_budget,
            "message": guard.get_status_message(total, 0.0),
        }

    # ── Retry & Escalation ────────────────────────────────

    def record_task_failure(self, task_id: str, error_message: str,
                            exit_code: int | None = None) -> dict:
        """记录任务失败并决策是否应重试/升级。"""
        self.retry.record_failure(task_id, error_message, exit_code)
        category = FailureClassifier.classify(error_message, exit_code)

        should_retry = self.retry.should_retry(task_id)
        esc = self.retry if hasattr(self.retry, 'should_escalate') else self.escalation
        should_escalate = esc.should_escalate(task_id) if hasattr(esc, 'should_escalate') else self.escalation.should_escalate(self.retry.failure_count(task_id))

        return {
            "failure_count": self.retry.failure_count(task_id),
            "category": category.value,
            "should_retry": should_retry,
            "should_escalate": should_escalate,
            "escalation_reason": self.escalation._last_reason if should_escalate else "",
        }

    def reset_task_retry(self, task_id: str):
        """任务成功后重置重试计数。"""
        self.retry.reset(task_id)

    # ── Context ───────────────────────────────────────────

    def generate_task_context(self, task_id: str, layer: str = "task") -> str:
        """为任务生成分层上下文摘要。"""
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
        """使任务的上下文缓存失效。"""
        self.context.invalidate(task_id)

    # ── QA & Validation ───────────────────────────────────

    def run_validation(self, task_id: str, checks: list[dict],
                       project_root: str = "") -> list[dict]:
        """运行验证检查并持久化结果到数据库。
        
        checks: [{"check_id":"unit","executable":"python","args":["-m","pytest"],...}, ...]
        返回每个检查的结果字典列表。
        """
        from bridgelib.validation import ValidationCheck, ValidationExecutor
        from datetime import datetime, timezone

        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        validation_checks = []
        for c in checks:
            vc = ValidationCheck(
                check_id=c["check_id"],
                executable=c.get("executable", "echo"),
                args=c.get("args", []),
                display_name=c.get("display_name", c["check_id"]),
                working_directory=c.get("working_directory", "."),
                timeout_seconds=c.get("timeout_seconds", 300),
                required=c.get("required", True),
                evidence_paths=c.get("evidence_paths", []),
            )
            validation_checks.append(vc)

        executor = ValidationExecutor(project_root=project_root or ".")
        results = executor.execute_all(validation_checks)

        # 持久化每个结果
        persisted = []
        for r in results:
            vid = f"val-{task_id}-{r.check_id}-{datetime.now(timezone.utc).timestamp()}"
            now = datetime.now(timezone.utc).isoformat()
            self.db.conn.execute(
                """INSERT INTO validations (id, task_id, check_id, command_json,
                   exit_code, status, output_summary, evidence_path, created_at,
                   started_at, completed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (vid, task_id, r.check_id,
                 json.dumps({"executable": "", "args": []}),
                 r.exit_code, r.status.value,
                 (r.stdout[:500] if r.stdout else ""),
                 r.evidence_hash,
                 now, now, now),
            )
            self.db.conn.commit()
            persisted.append(r.to_dict())

        return persisted

    def run_qa_on_generated(self, content: str, base_dir: str = ".") -> list[dict]:
        """对生成的 Markdown 内容运行 QA 检查。"""
        from bridgelib.reports import run_qa_checks
        results = run_qa_checks(content, base_dir)
        return [r.to_dict() if hasattr(r, 'to_dict') else {
            "check": getattr(r, 'check_name', ''),
            "passed": getattr(r, 'passed', False),
            "detail": getattr(r, 'detail', ''),
        } for r in results]

    def validate_artifacts(self, task_id: str, artifacts_path: str) -> dict:
        """交叉验证 ARTIFACTS.json — 使用协议级验证 + forbidden paths 检查。"""
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

        # 使用协议级 validate_artifacts 进行完整字段检查
        from bridgelib.protocol import validate_artifacts as protocol_validate
        issues = protocol_validate(data)

        # 补充额外交叉验证
        if data.get("task_id") != task_id:
            issues.append(f"task_id mismatch: {data.get('task_id')} vs {task_id}")
        if data.get("agent_id") != task.get("owner_agent_id"):
            issues.append(f"agent_id mismatch: {data.get('agent_id')} vs {task.get('owner_agent_id')}")

        # 使用 forbidden paths 检查（之前写死为 []）
        allowed = json.loads(task.get("allowed_paths_json", "[]") or "[]")
        forbidden = json.loads(task.get("forbidden_paths_json", "[]") or "[]")
        for f in data.get("changed_files", []):
            path = f.get("path", f) if isinstance(f, dict) else f
            if forbidden and is_within_scope(path, [], forbidden):
                issues.append(f"File in forbidden scope: {path}")
            elif not is_within_scope(path, allowed, forbidden):
                issues.append(f"File outside allowed scope: {path}")

        # 验证 checks 与 validations 表交叉
        db_validations = self.db.list_validations_by_task(task_id)
        db_check_ids = {v.get("check_id") for v in db_validations}
        for check in data.get("checks", []):
            cid = check.get("id", "")
            if cid and cid not in db_check_ids:
                issues.append(f"Check '{cid}' in artifacts not found in validation records")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "task_id": task_id,
        }

    # ── Receipt Import ────────────────────────────────────

    def import_receipt(self, task_dir: str, task_id: str, attempt: int,
                       lease_id: str, agent_id: str) -> dict | None:
        """导入任务目录中的回执（稳定窗口+哈希去重+交叉验证）。"""
        from bridgelib.receipt_importer import ReceiptImporter

        task = self.db.get_task(task_id)
        if task is None:
            raise CoordinatorError(f"Task {task_id} not found")

        allowed = json.loads(task.get("allowed_paths_json", "[]") or "[]")
        forbidden = json.loads(task.get("forbidden_paths_json", "[]") or "[]")

        importer = ReceiptImporter(self.db)
        return importer.scan_and_import(
            task_dir, task_id, attempt, lease_id, agent_id,
            allowed_paths=allowed, forbidden_paths=forbidden,
        )

    # ── Transition Validators ────────────────────────────

    def _validate_task_ready(self, task: dict):
        """Planning→Ready：检查任务有允许的路径和验收标准"""
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
        """Validating→Approved：逐个 required check 必须有 PASSED 验证结果。"""
        tid = task["id"]

        # 1. 验收标准必须存在
        criteria = json.loads(task.get("acceptance_criteria_json", "[]") or "[]")
        if not criteria:
            raise CoordinatorError(
                "Task must have acceptance_criteria before approval"
            )

        # 2. 每个 required check 必须有当前 attempt 的 PASSED 结果
        required_checks = json.loads(task.get("required_checks_json", "[]") or "[]")
        validations = self.db.list_validations_by_task(tid)

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
            # 如果没有定义 required checks，至少需要一次 PASSED 验证
            passed = [v for v in validations if v.get("status") == "passed"]
            if not passed:
                raise CoordinatorError(
                    f"Task {tid} must have at least one PASSED validation before approval. "
                    f"Found {len(validations)} validation(s), 0 passed."
                )

        # 3. 必须有审查结果且为 APPROVED
        reviews = self.db.list_reviews_by_task(tid)
        approved_reviews = [r for r in reviews if r.get("verdict") == "approved"]
        if not approved_reviews:
            raise CoordinatorError(
                f"Task {tid} must have an approved review before approval. "
                f"Found {len(reviews)} review(s), 0 approved."
            )

    def _validate_task_done(self, task: dict):
        """Merging→Done：检查合并队列中有已完成（MERGED）的条目"""
        merge_entries = self.merge.get_by_task(task["id"])
        if not any(e.status == MergeStatus.MERGED for e in merge_entries):
            raise CoordinatorError(
                "Task must have a completed merge (MERGED) before transitioning to done"
            )
