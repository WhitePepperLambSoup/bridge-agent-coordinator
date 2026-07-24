"""Bridge layered context management — project, module, task, and incremental layers with summary caching.

Design reference: docs/bridge-design/04-agent-routing-and-cost.md §Layered Context
"""

import hashlib
import threading
from enum import Enum
from dataclasses import dataclass, field


class ContextLayer(Enum):
    PROJECT = "project"
    MODULE = "module"
    TASK = "task"
    INCREMENTAL = "incremental"


# ── Context Types ─────────────────────────────────────────

@dataclass
class ProjectContext:
    """Project layer: technology stack, architecture, and global constraints."""
    project_id: str
    tech_stack: list[str] = field(default_factory=list)
    architecture: str = ""
    global_constraints: list[str] = field(default_factory=list)
    source_commit: str = ""
    layer: ContextLayer = ContextLayer.PROJECT


@dataclass
class ModuleContext:
    """Module layer: interfaces, key files, and known pitfalls."""
    module_name: str
    key_files: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    known_pitfalls: list[str] = field(default_factory=list)
    source_commit: str = ""
    layer: ContextLayer = ContextLayer.MODULE


@dataclass
class TaskContext:
    """Task layer: objective, scope, acceptance criteria, and checks."""
    task_id: str
    objective: str = ""
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    required_checks: list[str] = field(default_factory=list)
    source_commit: str = ""
    layer: ContextLayer = ContextLayer.TASK


@dataclass
class IncrementalContext:
    """Incremental layer: diffs, failure logs, and review comments."""
    task_id: str
    attempt: int = 1
    diff_summary: str = ""
    failure_logs: list[str] = field(default_factory=list)
    review_comments: list[str] = field(default_factory=list)
    source_commit: str = ""
    layer: ContextLayer = ContextLayer.INCREMENTAL


# ── Summary Generation ────────────────────────────────────

def generate_context_summary(ctx) -> str:
    """Generate a compact context summary without full source code or chat history."""
    if isinstance(ctx, ProjectContext):
        return (
            f"[Project:{ctx.project_id}] "
            f"Stack: {', '.join(ctx.tech_stack[:5])}. "
            f"Constraints: {', '.join(ctx.global_constraints[:5])}. "
            f"Commit: {ctx.source_commit[:8]}"
        )
    elif isinstance(ctx, ModuleContext):
        return (
            f"[Module:{ctx.module_name}] "
            f"Files: {', '.join(ctx.key_files[:5])}. "
            f"Pitfalls: {', '.join(ctx.known_pitfalls[:3])}. "
            f"Commit: {ctx.source_commit[:8]}"
        )
    elif isinstance(ctx, TaskContext):
        return (
            f"[Task:{ctx.task_id}] {ctx.objective[:80]}. "
            f"Paths: {', '.join(ctx.allowed_paths[:3])}. "
            f"ACs: {len(ctx.acceptance_criteria)}. "
            f"Commit: {ctx.source_commit[:8]}"
        )
    elif isinstance(ctx, IncrementalContext):
        return (
            f"[Incremental:{ctx.task_id}/a{ctx.attempt}] "
            f"Diff: {ctx.diff_summary[:100]}. "
            f"Failures: {len(ctx.failure_logs)}. "
            f"Reviews: {len(ctx.review_comments)}. "
            f"Commit: {ctx.source_commit[:8]}"
        )
    return str(ctx)[:200]


def is_context_stale(ctx, current_commit: str) -> bool:
    """Determine whether the context is stale."""
    if not ctx.source_commit:
        return True
    return ctx.source_commit != current_commit


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ── Context Manager ───────────────────────────────────────

class ContextManager:
    """Store and retrieve layered context."""

    def __init__(self):
        self._contexts: dict[str, object] = {}
        self._lock = threading.Lock()

    def set_context(self, key: str, ctx):
        with self._lock:
            self._contexts[key] = ctx

    def get_context(self, key: str):
        return self._contexts.get(key)

    def list_stale(self, current_commit: str) -> list:
        stale = []
        for key, ctx in self._contexts.items():
            if hasattr(ctx, 'source_commit') and is_context_stale(ctx, current_commit):
                stale.append(ctx)
        return stale

    def invalidate(self, key: str):
        with self._lock:
            self._contexts.pop(key, None)


# ── Context Cache ─────────────────────────────────────────

class ContextCache:
    """Context cache invalidated by source_commit."""

    def __init__(self, max_size: int = 100):
        self._cache: dict[str, tuple[str, str]] = {}  # key → (content, source_commit)
        self._max_size = max_size
        self._lock = threading.Lock()

    def put(self, key: str, content: str, source_commit: str = ""):
        with self._lock:
            if len(self._cache) >= self._max_size:
                # Simple FIFO eviction
                first_key = next(iter(self._cache))
                del self._cache[first_key]
            self._cache[key] = (content, source_commit)

    def get(self, key: str, current_commit: str = "") -> str | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            content, source_commit = entry
            if source_commit and current_commit and source_commit != current_commit:
                del self._cache[key]
                return None
            return content

    def clear(self):
        with self._lock:
            self._cache.clear()
