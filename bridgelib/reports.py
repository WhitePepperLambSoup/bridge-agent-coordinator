"""Bridge 成本报告、诊断包与 Markdown QA 检查器。

设计参考：docs/bridge-design/04 §成本节省报告 + 09 §诊断包 + 11 §Markdown QA
"""

import json
import os
import re
from dataclasses import dataclass, field


# ── Cost Report ───────────────────────────────────────────

@dataclass
class CostReport:
    total_tokens: int = 0
    total_cost: float = 0.0
    agent_breakdown: list[dict] = field(default_factory=list)
    task_breakdown: list[dict] = field(default_factory=list)
    is_estimated: bool = True
    generated_at: str = ""


@dataclass
class SavingsEstimate:
    actual_cost: float = 0.0
    hypothetical_strong_cost: float = 0.0
    saved_cost: float = 0.0
    savings_pct: float = 0.0
    strong_model: str = ""
    actual_models: list[str] = field(default_factory=list)
    disclaimer: str = ""


def generate_cost_report(records: list[dict]) -> CostReport:
    total_tokens = sum(r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in records)
    total_cost = sum(r.get("estimated_cost", 0.0) for r in records)

    by_agent: dict[str, dict] = {}
    for r in records:
        aid = r.get("agent_id", "unknown")
        if aid not in by_agent:
            by_agent[aid] = {"agent_id": aid, "tokens": 0, "cost": 0.0, "tasks": 0}
        by_agent[aid]["tokens"] += r.get("input_tokens", 0) + r.get("output_tokens", 0)
        by_agent[aid]["cost"] += r.get("estimated_cost", 0.0)
        by_agent[aid]["tasks"] += 1

    return CostReport(
        total_tokens=total_tokens,
        total_cost=total_cost,
        agent_breakdown=list(by_agent.values()),
    )


def generate_savings_estimate(
    actual_cost: float,
    hypothetical_strong_cost: float,
    strong_model: str = "gpt-5.6-sol",
    actual_models: list[str] | None = None,
) -> SavingsEstimate:
    saved = hypothetical_strong_cost - actual_cost
    pct = (saved / hypothetical_strong_cost * 100) if hypothetical_strong_cost > 0 else 0.0

    return SavingsEstimate(
        actual_cost=actual_cost,
        hypothetical_strong_cost=hypothetical_strong_cost,
        saved_cost=saved,
        savings_pct=pct,
        strong_model=strong_model,
        actual_models=actual_models or [],
        disclaimer=(
            f"Estimated savings assume all tasks were done by {strong_model} "
            f"at standard pricing. Actual token counts may vary. "
            f"This is an estimate, not a precise measurement."
        ),
    )


# ── Diagnostic Package ────────────────────────────────────

@dataclass
class DiagnosticPackage:
    project_id: str
    version: str = "0.0.0"
    event_summary: dict = field(default_factory=dict)
    config_summary: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    limited_logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "version": self.version,
            "event_summary": self.event_summary,
            "config_summary": self.config_summary,
            "errors": self.errors,
            "limited_logs": self.limited_logs,
        }

    def preview(self) -> str:
        """生成诊断包预览（不包含秘密或完整日志）。"""
        lines = [
            f"Diagnostic Package for {self.project_id}",
            f"Version: {self.version}",
            f"Events: {self.event_summary.get('total_events', 0)}",
            f"Errors: {len(self.errors)}",
            f"Config: {json.dumps(self.config_summary, indent=2)[:500]}",
        ]
        return "\n".join(lines)


# ── Markdown QA ───────────────────────────────────────────

@dataclass
class QACheckResult:
    check: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {"check": self.check, "passed": self.passed, "detail": self.detail}


def run_qa_checks(content: str, base_dir: str = ".") -> list[QACheckResult]:
    results = []

    # 语言检查：英文文档不混入中文
    has_cn = any('\u4e00' <= ch <= '\u9fff' for ch in content)
    results.append(QACheckResult(
        "language_mixing",
        not has_cn,
        "No Chinese characters found" if not has_cn else "Chinese characters detected in English document",
    ))

    # Agent 数量检查：不写死为两个
    agent_fixed = bool(re.search(r'Agent\s+[AB]\b', content)) and "Agent C" not in content
    results.append(QACheckResult(
        "agent_count_dynamic",
        not agent_fixed,
        "Agent references appear dynamic" if not agent_fixed else "Agent references may be hardcoded to two",
    ))

    # 敏感信息检查
    sensitive_patterns = [
        (r'sk-[a-zA-Z0-9]{20,}', "OpenAI API key"),
        (r'AIza[0-9A-Za-z\-_]{35}', "Google API key"),
        (r'ghp_[a-zA-Z0-9]{36}', "GitHub token"),
    ]
    found_sensitive = []
    for pattern, name in sensitive_patterns:
        if re.search(pattern, content):
            found_sensitive.append(name)
    results.append(QACheckResult(
        "sensitive_info",
        len(found_sensitive) == 0,
        "No secrets found" if not found_sensitive else f"Found: {', '.join(found_sensitive)}",
    ))

    # 相对链接检查
    links = re.findall(r'\]\(([^)]+\.md)\)', content)
    broken = []
    for link in links:
        target = os.path.join(base_dir, link)
        if not os.path.exists(target):
            broken.append(link)
    results.append(QACheckResult(
        "relative_links",
        len(broken) == 0,
        "All links valid" if not broken else f"Broken: {', '.join(broken)}",
    ))

    return results
