"""Bridge cost tracking and budget guard — token and cost estimates with threshold checks.

Design reference: docs/bridge-design/04-agent-routing-and-cost.md §Budget Guard
"""

import threading
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone


class BudgetThreshold(Enum):
    OK = "ok"
    WARNING = "warning"
    BLOCKED = "blocked"
    EXCEEDED = "exceeded"


class CostError(Exception):
    pass


@dataclass
class CostRecord:
    task_id: str
    agent_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    is_estimated: bool = True
    source: str = "manual"
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


class CostTracker:
    """Track token usage and estimated costs."""

    def __init__(self):
        self._records: list[CostRecord] = []
        self._lock = threading.Lock()

    def record(self, task_id: str, agent_id: str, input_tokens: int = 0,
               output_tokens: int = 0, estimated_cost: float = 0.0,
               is_estimated: bool = True, source: str = "manual") -> CostRecord:
        record = CostRecord(
            task_id=task_id, agent_id=agent_id,
            input_tokens=input_tokens, output_tokens=output_tokens,
            estimated_cost=estimated_cost, is_estimated=is_estimated,
            source=source,
        )
        with self._lock:
            self._records.append(record)
        return record

    def total_tokens(self) -> int:
        return sum(r.input_tokens + r.output_tokens for r in self._records)

    def total_cost(self) -> float:
        return sum(r.estimated_cost for r in self._records)

    def tokens_by_task(self, task_id: str) -> int:
        return sum(
            r.input_tokens + r.output_tokens
            for r in self._records if r.task_id == task_id
        )

    def cost_by_task(self, task_id: str) -> float:
        return sum(r.estimated_cost for r in self._records if r.task_id == task_id)

    def cost_by_agent(self, agent_id: str) -> float:
        return sum(r.estimated_cost for r in self._records if r.agent_id == agent_id)

    def records_by_task(self, task_id: str) -> list[CostRecord]:
        return [r for r in self._records if r.task_id == task_id]


class BudgetGuard:
    """Budget guard with multiple threshold levels."""

    def __init__(self, task_token_budget: int = 50000,
                 task_cost_budget: float = 0.50,
                 warning_pct: int = 50, block_pct: int = 80):
        self.task_token_budget = task_token_budget
        self.task_cost_budget = task_cost_budget
        self.warning_pct = warning_pct
        self.block_pct = block_pct

    def check(self, current_tokens: int, current_cost: float) -> BudgetThreshold:
        token_pct = (current_tokens / self.task_token_budget * 100) if self.task_token_budget else 0
        cost_pct = (current_cost / self.task_cost_budget * 100) if self.task_cost_budget else 0
        max_pct = max(token_pct, cost_pct)

        if max_pct >= 100:
            return BudgetThreshold.EXCEEDED
        if max_pct >= self.block_pct:
            return BudgetThreshold.BLOCKED
        if max_pct >= self.warning_pct:
            return BudgetThreshold.WARNING
        return BudgetThreshold.OK

    def get_status_message(self, current_tokens: int, current_cost: float) -> str:
        threshold = self.check(current_tokens, current_cost)
        token_pct = (current_tokens / self.task_token_budget * 100) if self.task_token_budget else 0
        cost_pct = (current_cost / self.task_cost_budget * 100) if self.task_cost_budget else 0

        if threshold == BudgetThreshold.OK:
            return f"Budget OK: {current_tokens}/{self.task_token_budget} tokens, ${current_cost:.4f}/${self.task_cost_budget:.2f}"
        elif threshold == BudgetThreshold.WARNING:
            return f"⚠ Budget warning: {token_pct:.0f}% tokens, {cost_pct:.0f}% cost"
        elif threshold == BudgetThreshold.BLOCKED:
            return f"🛑 Budget blocked: {token_pct:.0f}% tokens, {cost_pct:.0f}% cost. Auto-advance paused."
        else:
            return f"❌ Budget exceeded: {token_pct:.0f}% tokens, {cost_pct:.0f}% cost. Manual decision required."
