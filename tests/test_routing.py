"""Phase 4.2 测试 — Agent 路由与推荐"""

import pytest
from bridgelib.routing import (
    RouteRequest,
    RouteResult,
    filter_candidates,
    score_candidates,
    recommend_agent,
)


# 模拟 Agent 档案
def _make_agent(aid, tier, cost_tier, roles, can_plan=False, can_review=False, can_merge=False, max_parallel=2):
    return {
        "id": aid, "display_name": f"Agent {aid}",
        "capability_tier": tier, "cost_tier": cost_tier,
        "roles_json": str(roles).replace("'", '"'),
        "permissions_json": str({
            "can_plan": can_plan, "can_review": can_review, "can_merge": can_merge,
        }).replace("'", '"'),
        "enabled": 1,
    }


class TestFilterCandidates:
    """硬过滤"""

    def test_filter_by_role(self):
        agents = [
            _make_agent("a1", "high", "high", ["planner"], can_plan=True),
            _make_agent("a2", "standard", "low", ["implementer"], can_plan=False),
            _make_agent("a3", "high", "high", ["reviewer"], can_review=True),
        ]
        req = RouteRequest(
            task_id="TASK-001",
            required_role="implementer",
            risk="medium",
        )
        filtered = filter_candidates(agents, req)
        assert len(filtered) == 1
        assert filtered[0]["id"] == "a2"

    def test_filter_disabled_agents(self):
        agents = [
            _make_agent("a1", "high", "high", ["implementer"]),
        ]
        agents[0]["enabled"] = 0
        req = RouteRequest(task_id="TASK-001", required_role="implementer", risk="low")
        filtered = filter_candidates(agents, req)
        assert len(filtered) == 0

    def test_filter_high_risk_requires_high_capability(self):
        agents = [
            _make_agent("a1", "standard", "low", ["implementer"]),
            _make_agent("a2", "high", "high", ["implementer"]),
        ]
        req = RouteRequest(task_id="TASK-001", required_role="implementer", risk="critical")
        filtered = filter_candidates(agents, req)
        assert len(filtered) == 1
        assert filtered[0]["id"] == "a2"


class TestScoreCandidates:
    """评分"""

    def test_lower_cost_scores_higher_for_low_risk(self):
        agents = [
            _make_agent("a1", "standard", "low", ["implementer"]),
            _make_agent("a2", "standard", "high", ["implementer"]),
        ]
        req = RouteRequest(task_id="TASK-001", required_role="implementer", risk="low")
        scored = score_candidates(agents, req)
        # 低成本应该排在前面
        assert scored[0]["id"] == "a1"

    def test_higher_capability_scores_higher_for_high_risk(self):
        agents = [
            _make_agent("a1", "standard", "low", ["implementer"]),
            _make_agent("a2", "high", "high", ["implementer"]),
        ]
        req = RouteRequest(task_id="TASK-001", required_role="implementer", risk="high")
        scored = score_candidates(agents, req)
        assert scored[0]["id"] == "a2"


class TestRecommendAgent:
    """推荐 Agent"""

    def test_recommend_returns_best(self):
        agents = [
            _make_agent("a1", "standard", "low", ["implementer"]),
            _make_agent("a2", "high", "high", ["implementer"]),
        ]
        req = RouteRequest(task_id="TASK-001", required_role="implementer", risk="low")
        result = recommend_agent(agents, req)
        assert result.recommended_agent_id == "a1"
        assert len(result.candidates) == 2
        assert result.reason != ""

    def test_no_candidates_returns_none(self):
        agents = [
            _make_agent("a1", "standard", "low", ["planner"], can_plan=True),
        ]
        req = RouteRequest(task_id="TASK-001", required_role="implementer", risk="low")
        result = recommend_agent(agents, req)
        assert result.recommended_agent_id is None
        assert len(result.candidates) == 0
