"""P2 integration tests for routing, cost, retry, context, receipt import, and QA."""

import pytest
import tempfile
import os
from datetime import datetime, timezone

from bridgelib.database import init_database
from bridgelib.leases import LeaseManager
from bridgelib.review import ReviewManager
from bridgelib.merge import MergeQueue
from bridgelib.workspace import WorkspaceManager
from bridgelib.operations import OperationLog
from bridgelib.coordinator import BridgeCoordinator, CoordinatorError
from bridgelib.state_machine import TaskState


@pytest.fixture
def db():
    d = tempfile.mkdtemp()
    db_path = os.path.join(d, "test.db")
    database = init_database(db_path)
    yield database
    database.close()
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def coordinator(db):
    return BridgeCoordinator(
        database=db,
        lease_manager=LeaseManager(),
        review_manager=ReviewManager(),
        merge_queue=MergeQueue(),
        workspace_manager=WorkspaceManager(),
        operation_log=OperationLog(),
    )


@pytest.fixture
def env(coordinator):
    pid = coordinator.init_project(name="Test", root_path="/tmp/t")
    planner = coordinator.add_agent(pid, display_name="Codex",
                                     capability_tier="high", cost_tier="high",
                                     roles=["planner"], can_plan=True, can_review=True)
    implementer = coordinator.add_agent(pid, display_name="Reasonix",
                                         capability_tier="standard", cost_tier="low",
                                         roles=["implementer"])
    reviewer = coordinator.add_agent(pid, display_name="Claude",
                                      capability_tier="high", cost_tier="high",
                                      roles=["reviewer"], can_review=True)
    gid = coordinator.create_goal(pid, title="Test")
    tid = coordinator.create_task(
        goal_id=gid, title="Test task",
        allowed_paths=["src/**"], acceptance_criteria=["AC-1"],
        risk="medium", complexity="medium",
    )
    return pid, planner, implementer, reviewer, gid, tid


class TestRouting:
    def test_recommend_implementer(self, coordinator, env):
        pid, planner, implementer, reviewer, gid, tid = env
        result = coordinator.recommend_agent_for_task(tid, role="implementer")
        assert result["recommended_agent_id"] is not None
        assert result["reason"]

    def test_recommend_planner(self, coordinator, env):
        pid, planner, implementer, reviewer, gid, tid = env
        result = coordinator.recommend_agent_for_task(tid, role="planner")
        assert result["recommended_agent_id"] == planner

    def test_recommend_reviewer(self, coordinator, env):
        pid, planner, implementer, reviewer, gid, tid = env
        result = coordinator.recommend_agent_for_task(tid, role="reviewer")
        # The reviewer role should select agents with can_review permission.
        assert result["recommended_agent_id"] is not None
        assert result["reason"]


class TestCostTracking:
    def test_record_and_check_cost(self, coordinator, env):
        pid, planner, implementer, reviewer, gid, tid = env
        coordinator.record_task_cost(tid, implementer, input_tokens=500, output_tokens=200, source="test")
        result = coordinator.check_budget(tid, token_budget=3000)
        assert result["threshold"] == "ok"
        assert result["total_tokens"] == 700


class TestRetry:
    def test_record_failure_and_retry(self, coordinator, env):
        pid, planner, implementer, reviewer, gid, tid = env
        result = coordinator.record_task_failure(tid, "assertion failed in test_auth", exit_code=1)
        assert result["category"] == "test_failure"
        assert result["should_retry"] is True
        assert result["failure_count"] == 1

    def test_reset_retry(self, coordinator, env):
        pid, planner, implementer, reviewer, gid, tid = env
        coordinator.record_task_failure(tid, "timeout", exit_code=124)
        coordinator.reset_task_retry(tid)
        assert coordinator.retry.failure_count(tid) == 0


class TestContext:
    def test_generate_context(self, coordinator, env):
        pid, planner, implementer, reviewer, gid, tid = env
        summary = coordinator.generate_task_context(tid, "task")
        assert summary
        # The context summary should include the task ID.
        assert tid in summary


class TestQA:
    def test_qa_on_content(self, coordinator, env):
        pid, planner, implementer, reviewer, gid, tid = env
        content = "# Test\n\n[link](README.md)\n\nsome text"
        results = coordinator.run_qa_on_generated(content)
        assert isinstance(results, list)

    def test_qa_no_mixed_language(self, coordinator, env):
        pid, planner, implementer, reviewer, gid, tid = env
        content = "# English title\n\n中文内容 mixed\n"
        results = coordinator.run_qa_on_generated(content)
        assert isinstance(results, list)


class TestArtifactsValidation:
    def test_valid_artifacts(self, coordinator, env):
        pid, planner, implementer, reviewer, gid, tid = env
        coordinator.transition_task(tid, TaskState.PLANNING,
                                     actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.READY,
                                     actor="user", confirmed=True)
        coordinator.assign_task(tid, implementer, reviewer, actor="user")
        # Create the artifacts file.
        import json
        d = tempfile.mkdtemp()
        artifacts_path = os.path.join(d, "ARTIFACTS.json")
        with open(artifacts_path, "w") as f:
            json.dump({
                "protocol_version": 1,
                "task_id": tid,
                "attempt": 1,
                "agent_id": implementer,
                "base_commit": "abc123",
                "submission_commit": "def456",
                "changed_files": [{"path": "src/main.py", "change": "modified"}],
                "checks": [],
                "generated_at": "2026-01-01T00:00:00Z",
            }, f)
        result = coordinator.validate_artifacts(tid, artifacts_path)
        assert result["valid"] is True
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_scope_violation_detected(self, coordinator, env):
        pid, planner, implementer, reviewer, gid, tid = env
        coordinator.transition_task(tid, TaskState.PLANNING,
                                     actor="user", confirmed=True)
        coordinator.transition_task(tid, TaskState.READY,
                                     actor="user", confirmed=True)
        coordinator.assign_task(tid, implementer, reviewer, actor="user")
        import json
        d = tempfile.mkdtemp()
        artifacts_path = os.path.join(d, "ARTIFACTS.json")
        with open(artifacts_path, "w") as f:
            json.dump({
                "protocol_version": 1,
                "task_id": tid,
                "attempt": 1,
                "agent_id": implementer,
                "base_commit": "abc123",
                "submission_commit": "def456",
                "changed_files": [{"path": "secret/passwords.txt", "change": "modified"}],
                "checks": [],
                "generated_at": "",
            }, f)
        result = coordinator.validate_artifacts(tid, artifacts_path)
        assert result["valid"] is False
        assert len(result["issues"]) >= 1
        import shutil
        shutil.rmtree(d, ignore_errors=True)
