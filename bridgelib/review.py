"""Bridge review system for requests, verdicts, fix tasks, and escalation paths.

Design references: docs/bridge-design/04-agent-routing-and-cost.md, review packages,
and 08, review center
"""

import secrets
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum


class ReviewVerdict(Enum):
    APPROVED = "approved"
    REVISION_REQUIRED = "revision_required"
    BLOCKED = "blocked"
    ESCALATE = "escalate"


class ReviewError(Exception):
    pass


# ── Review Package ────────────────────────────────────────

@dataclass
class ReviewPackage:
    """Minimal context package provided to the reviewing agent."""
    task_id: str
    title: str
    objective: str = ""
    risk: str = "medium"
    complexity: str = "medium"
    approved_decisions: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    diff_summary: str = ""
    check_evidence: dict | None = None
    implementer_receipt: str = ""
    known_risks: list[str] = field(default_factory=list)
    pending_questions: list[str] = field(default_factory=list)


# ── Review Request ────────────────────────────────────────

@dataclass
class ReviewRequest:
    request_id: str
    task_id: str
    reviewer_agent_id: str
    review_package: ReviewPackage
    status: str = "pending"    # pending | completed
    created_at: str = ""
    completed_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "reviewer_agent_id": self.reviewer_agent_id,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


# ── Review Result ─────────────────────────────────────────

@dataclass
class ReviewResult:
    request_id: str
    verdict: ReviewVerdict
    summary: str = ""
    issues: list[dict] = field(default_factory=list)
    fix_task_id: str | None = None
    escalation_reason: str | None = None
    completed_at: str = ""

    def __post_init__(self):
        if not self.completed_at:
            self.completed_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "verdict": self.verdict.value,
            "summary": self.summary,
            "issues": self.issues,
            "fix_task_id": self.fix_task_id,
            "escalation_reason": self.escalation_reason,
            "completed_at": self.completed_at,
        }


# ── Review Manager ────────────────────────────────────────

class ReviewManager:
    """Manage review requests and results."""

    def __init__(self):
        self._requests: dict[str, ReviewRequest] = {}
        self._results: dict[str, ReviewResult] = {}
        self._by_task: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def submit(
        self, task_id: str, reviewer_agent_id: str, review_package: ReviewPackage
    ) -> ReviewRequest:
        with self._lock:
            # Verify that review_package and the request have the same task_id
            if review_package.task_id != task_id:
                raise ReviewError(
                    f"Review package task_id ({review_package.task_id}) "
                    f"does not match request task_id ({task_id})"
                )

            # Check for an existing pending review for the same task
            pending = self._find_pending_by_task(task_id)
            if pending:
                raise ReviewError(
                    f"Task {task_id} already has a pending review ({pending.request_id})"
                )

            req_id = f"rev-{secrets.token_hex(6)}"
            req = ReviewRequest(
                request_id=req_id,
                task_id=task_id,
                reviewer_agent_id=reviewer_agent_id,
                review_package=review_package,
            )
            self._requests[req_id] = req
            self._by_task.setdefault(task_id, []).append(req_id)
            return req

    def complete(
        self, request_id: str, verdict: ReviewVerdict, summary: str = "",
        issues: list[dict] | None = None, fix_task_id: str | None = None,
        escalation_reason: str | None = None,
    ) -> ReviewResult:
        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                raise ReviewError(f"Review request {request_id} not found")

            # Prevent duplicate completion
            if request_id in self._results:
                raise ReviewError(
                    f"Review {request_id} has already been completed "
                    f"with verdict {self._results[request_id].verdict.value}"
                )

            result = ReviewResult(
                request_id=request_id,
                verdict=verdict,
                summary=summary,
                issues=issues or [],
                fix_task_id=fix_task_id,
                escalation_reason=escalation_reason,
            )
            self._results[request_id] = result
            req.status = "completed"
            req.completed_at = datetime.now(timezone.utc).isoformat()
            return result

    def get_request(self, request_id: str) -> ReviewRequest | None:
        return self._requests.get(request_id)

    def get_result(self, request_id: str) -> ReviewResult | None:
        return self._results.get(request_id)

    def list_pending(self) -> list[ReviewRequest]:
        return [r for r in self._requests.values() if r.status == "pending"]

    def get_by_task(self, task_id: str) -> list[ReviewRequest]:
        ids = self._by_task.get(task_id, [])
        return [self._requests[rid] for rid in ids if rid in self._requests]

    def _find_pending_by_task(self, task_id: str) -> ReviewRequest | None:
        for rid in self._by_task.get(task_id, []):
            req = self._requests.get(rid)
            if req and req.status == "pending":
                return req
        return None
