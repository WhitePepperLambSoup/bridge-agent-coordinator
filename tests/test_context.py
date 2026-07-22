"""Phase 5.1 测试 — 分层上下文与摘要缓存"""

import pytest
import hashlib
from bridgelib.context import (
    ContextLayer,
    ContextManager,
    ContextCache,
    ProjectContext,
    ModuleContext,
    TaskContext,
    IncrementalContext,
    generate_context_summary,
    is_context_stale,
)


class TestContextLayers:
    """四层上下文"""

    def test_project_context(self):
        ctx = ProjectContext(
            project_id="proj-1",
            tech_stack=["Python 3.11", "SQLite"],
            architecture="Layered: coordinator → adapters → external",
            global_constraints=["No force push", "Atomic writes only"],
            source_commit="abc123",
        )
        assert ctx.layer == ContextLayer.PROJECT

    def test_module_context(self):
        ctx = ModuleContext(
            module_name="auth",
            key_files=["src/auth/service.py", "src/auth/models.py"],
            interfaces=["POST /login", "GET /verify"],
            known_pitfalls=["Session timeout not handled"],
            source_commit="abc123",
        )
        assert ctx.layer == ContextLayer.MODULE
        assert len(ctx.key_files) == 2

    def test_task_context(self):
        ctx = TaskContext(
            task_id="TASK-014",
            objective="Add OAuth login",
            allowed_paths=["src/auth/**"],
            acceptance_criteria=["AC-1: OAuth flow works"],
            required_checks=["unit-tests"],
            source_commit="abc123",
        )
        assert ctx.layer == ContextLayer.TASK
        assert ctx.task_id == "TASK-014"

    def test_incremental_context(self):
        ctx = IncrementalContext(
            task_id="TASK-014",
            attempt=2,
            diff_summary="+150 -20 in auth module",
            failure_logs=["Test auth failed: timeout"],
            review_comments=["Add retry logic"],
            source_commit="def456",
        )
        assert ctx.layer == ContextLayer.INCREMENTAL
        assert ctx.attempt == 2


class TestContextSummary:
    """上下文摘要生成"""

    def test_summary_includes_key_info(self):
        ctx = TaskContext(
            task_id="TASK-042",
            objective="Fix login bug",
            allowed_paths=["src/auth/**"],
            acceptance_criteria=["AC-1"],
            source_commit="abc",
        )
        summary = generate_context_summary(ctx)
        assert "TASK-042" in summary
        assert "Fix login bug" in summary

    def test_summary_excludes_full_body(self):
        """摘要不应包含完整源码或聊天历史"""
        ctx = ProjectContext(
            project_id="p1",
            tech_stack=["Python"],
            architecture="simple",
            global_constraints=[],
            source_commit="abc",
        )
        summary = generate_context_summary(ctx)
        assert len(summary) < 2000  # 摘要应该紧凑


class TestContextStaleness:
    """上下文过期判断"""

    def test_same_commit_not_stale(self):
        ctx = TaskContext(
            task_id="TASK-001", objective="Test",
            allowed_paths=["src/**"], acceptance_criteria=["AC-1"],
            source_commit="abc123",
        )
        assert not is_context_stale(ctx, current_commit="abc123")

    def test_different_commit_is_stale(self):
        ctx = TaskContext(
            task_id="TASK-001", objective="Test",
            allowed_paths=["src/**"], acceptance_criteria=["AC-1"],
            source_commit="abc123",
        )
        assert is_context_stale(ctx, current_commit="def456")

    def test_no_source_commit_always_stale(self):
        ctx = TaskContext(
            task_id="TASK-001", objective="Test",
            allowed_paths=["src/**"], acceptance_criteria=["AC-1"],
            source_commit="",
        )
        assert is_context_stale(ctx, current_commit="abc123")


class TestContextManager:
    """上下文管理器"""

    @pytest.fixture
    def manager(self):
        return ContextManager()

    def test_set_and_get(self, manager):
        ctx = TaskContext(
            task_id="TASK-001", objective="Test",
            allowed_paths=["src/**"], acceptance_criteria=["AC-1"],
            source_commit="abc",
        )
        manager.set_context("TASK-001", ctx)
        retrieved = manager.get_context("TASK-001")
        assert retrieved is not None
        assert retrieved.task_id == "TASK-001"

    def test_list_stale_contexts(self, manager):
        ctx1 = TaskContext(task_id="TASK-001", objective="T1",
                          allowed_paths=["src/**"], acceptance_criteria=["AC-1"],
                          source_commit="old1")
        ctx2 = TaskContext(task_id="TASK-002", objective="T2",
                          allowed_paths=["src/**"], acceptance_criteria=["AC-1"],
                          source_commit="old2")
        manager.set_context("TASK-001", ctx1)
        manager.set_context("TASK-002", ctx2)

        stale = manager.list_stale(current_commit="new_commit")
        assert len(stale) == 2

    def test_invalidate(self, manager):
        ctx = TaskContext(task_id="TASK-001", objective="Test",
                         allowed_paths=["src/**"], acceptance_criteria=["AC-1"],
                         source_commit="abc")
        manager.set_context("TASK-001", ctx)
        manager.invalidate("TASK-001")
        assert manager.get_context("TASK-001") is None


class TestContextCache:
    """上下文缓存（基于内容哈希）"""

    def test_cache_hit(self):
        cache = ContextCache()
        cache.put("key1", "This is cached content", source_commit="abc123")
        hit = cache.get("key1", current_commit="abc123")
        assert hit == "This is cached content"

    def test_cache_miss_stale_commit(self):
        cache = ContextCache()
        cache.put("key1", "Old content", source_commit="old")
        hit = cache.get("key1", current_commit="new")
        assert hit is None

    def test_cache_miss_unknown_key(self):
        cache = ContextCache()
        assert cache.get("unknown", current_commit="abc") is None

    def test_cache_clear(self):
        cache = ContextCache()
        cache.put("k1", "v1", source_commit="abc")
        cache.clear()
        assert cache.get("k1", current_commit="abc") is None
