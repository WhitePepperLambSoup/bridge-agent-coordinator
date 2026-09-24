"""Phase 4.4 tests - cost tracking and budget guards."""

import pytest
from bridgelib.cost import (
    CostRecord,
    CostTracker,
    BudgetGuard,
    BudgetThreshold,
)


class TestCostRecord:
    def test_estimated_record(self):
        record = CostRecord(
            task_id="TASK-001",
            agent_id="agent-a",
            input_tokens=5000,
            output_tokens=2000,
            estimated_cost=0.035,
            is_estimated=True,
        )
        assert record.is_estimated
        assert record.estimated_cost == 0.035

    def test_actual_record(self):
        record = CostRecord(
            task_id="TASK-001",
            agent_id="agent-a",
            input_tokens=5000,
            output_tokens=2000,
            estimated_cost=0.035,
            is_estimated=False,
            source="openai_api",
        )
        assert not record.is_estimated
        assert record.source == "openai_api"


class TestCostTracker:
    @pytest.fixture
    def tracker(self):
        return CostTracker()

    def test_record_and_total(self, tracker):
        tracker.record(task_id="TASK-001", agent_id="a",
                      input_tokens=1000, output_tokens=500, estimated_cost=0.01)
        tracker.record(task_id="TASK-002", agent_id="b",
                      input_tokens=2000, output_tokens=1000, estimated_cost=0.02)
        assert tracker.total_tokens() == 4500
        assert abs(tracker.total_cost() - 0.03) < 0.001

    def test_tokens_by_task(self, tracker):
        tracker.record(task_id="TASK-001", agent_id="a", input_tokens=100, output_tokens=50, estimated_cost=0.001)
        tracker.record(task_id="TASK-001", agent_id="a", input_tokens=200, output_tokens=100, estimated_cost=0.002)
        tokens = tracker.tokens_by_task("TASK-001")
        assert tokens == 450

    def test_cost_by_agent(self, tracker):
        tracker.record(task_id="TASK-001", agent_id="gpt", input_tokens=1000, output_tokens=0, estimated_cost=0.01)
        tracker.record(task_id="TASK-002", agent_id="reasonix", input_tokens=500, output_tokens=0, estimated_cost=0.001)
        tracker.record(task_id="TASK-003", agent_id="gpt", input_tokens=500, output_tokens=0, estimated_cost=0.005)
        cost = tracker.cost_by_agent("gpt")
        assert abs(cost - 0.015) < 0.001


class TestBudgetGuard:
    def test_under_budget(self):
        guard = BudgetGuard(
            task_token_budget=10000,
            task_cost_budget=0.10,
        )
        result = guard.check(2000, 0.02)
        assert result == BudgetThreshold.OK

    def test_warning_at_50_percent(self):
        guard = BudgetGuard(
            task_token_budget=10000,
            task_cost_budget=0.10,
            warning_pct=50,
        )
        result = guard.check(6000, 0.06)
        assert result == BudgetThreshold.WARNING

    def test_blocked_at_80_percent(self):
        guard = BudgetGuard(
            task_token_budget=10000,
            task_cost_budget=0.10,
            block_pct=80,
        )
        result = guard.check(9000, 0.09)
        assert result == BudgetThreshold.BLOCKED

    def test_exceeded_at_100_percent(self):
        guard = BudgetGuard(task_token_budget=10000)
        result = guard.check(11000, 0.15)
        assert result == BudgetThreshold.EXCEEDED

    def test_message_includes_details(self):
        guard = BudgetGuard(task_token_budget=10000)
        msg = guard.get_status_message(6000, 0.06)
        assert "6000" in msg or "60" in msg
