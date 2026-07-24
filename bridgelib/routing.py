"""Bridge agent routing and recommendations with hard filters and explainable scores.

Design reference: docs/bridge-design/04-agent-routing-and-cost.md
"""

import json
from dataclasses import dataclass, field


@dataclass
class RouteRequest:
    task_id: str
    required_role: str = "implementer"   # implementer | planner | reviewer
    risk: str = "medium"
    complexity: str = "medium"
    cost_tier: str = "low"
    language: str = "zh-CN"


@dataclass
class RouteResult:
    recommended_agent_id: str | None = None
    candidates: list[dict] = field(default_factory=list)
    reason: str = ""
    excluded: list[dict] = field(default_factory=list)


# ── Hard Filters ──────────────────────────────────────────

def filter_candidates(agents: list[dict], request: RouteRequest) -> list[dict]:
    """Apply hard filters for role, capability tier, and enabled status."""
    candidates = []
    for a in agents:
        if not a.get("enabled", 1):
            continue

        roles = _parse_json(a.get("roles_json", "[]"), [])
        perms = _parse_json(a.get("permissions_json", "{}"), {})

        # Role matching
        if request.required_role == "planner" and not perms.get("can_plan"):
            continue
        if request.required_role == "reviewer" and not perms.get("can_review"):
            continue
        if request.required_role == "implementer" and request.required_role not in roles:
            # Implementers must have the implementer role
            if "implementer" not in roles:
                continue

        # High-risk tasks require high capability
        if request.risk in ("high", "critical"):
            if a.get("capability_tier") not in ("high",):
                continue

        candidates.append(a)
    return candidates


def score_candidates(agents: list[dict], request: RouteRequest) -> list[dict]:
    """Score candidates by cost efficiency and capability fit."""
    scored = []
    for a in agents:
        score = 0.0

        # Cost efficiency has greater weight for low-risk tasks
        cost_tier = a.get("cost_tier", "medium")
        if request.risk in ("low", "medium"):
            if cost_tier == "low":
                score += 40
            elif cost_tier == "medium":
                score += 20

        # Capability fit has greater weight for high-risk tasks
        cap_tier = a.get("capability_tier", "standard")
        if request.risk in ("high", "critical"):
            if cap_tier == "high":
                score += 50
            elif cap_tier == "standard":
                score += 20

        # Base match
        score += 30  # Every candidate that passes filtering gets a base score

        scored.append({"agent": a, "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return [s["agent"] for s in scored]


def recommend_agent(agents: list[dict], request: RouteRequest) -> RouteResult:
    """Recommend the best agent."""
    candidates = filter_candidates(agents, request)
    if not candidates:
        return RouteResult(reason="No agent matches the required criteria")

    ranked = score_candidates(candidates, request)
    best = ranked[0]

    return RouteResult(
        recommended_agent_id=best["id"],
        candidates=ranked,
        reason=f"Recommended {best['display_name']} — "
               f"tier={best.get('capability_tier')}, cost={best.get('cost_tier')}",
    )


def _parse_json(raw: str, default):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default
