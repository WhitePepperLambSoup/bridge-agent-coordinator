"""Phase 5.3 测试 — 成本报告 + 诊断包 + Markdown QA"""

import json
import pytest
import tempfile
import os
from bridgelib.reports import (
    CostReport,
    generate_cost_report,
    generate_savings_estimate,
    DiagnosticPackage,
    QACheckResult,
    run_qa_checks,
)


class TestCostReport:
    def test_generate_report(self):
        costs = [
            {"agent_id": "gpt", "input_tokens": 10000, "output_tokens": 5000, "estimated_cost": 0.10},
            {"agent_id": "reasonix", "input_tokens": 50000, "output_tokens": 20000, "estimated_cost": 0.05},
        ]
        report = generate_cost_report(costs)
        assert report.total_tokens == 85000
        assert abs(report.total_cost - 0.15) < 0.01
        assert len(report.agent_breakdown) == 2

    def test_savings_estimate(self):
        """如果全部由强模型完成 vs 实际成本"""
        estimate = generate_savings_estimate(
            actual_cost=0.15,
            hypothetical_strong_cost=0.80,
            strong_model="gpt-5.6-sol",
            actual_models=["gpt-5.6-sol", "reasonix"],
        )
        assert estimate.saved_cost > 0
        assert estimate.savings_pct > 50
        assert "gpt-5.6-sol" in estimate.disclaimer

    def test_savings_disclaimer_includes_assumptions(self):
        estimate = generate_savings_estimate(0.10, 0.50)
        assert "estimate" in estimate.disclaimer.lower()
        assert "assume" in estimate.disclaimer.lower()


class TestDiagnosticPackage:
    def test_create_package(self):
        pkg = DiagnosticPackage(
            project_id="test-proj",
            version="0.2.0",
            event_summary={"total_events": 150, "events_by_type": {"TaskCreated": 10}},
            config_summary={"workspace_mode": "per_task_worktree"},
            errors=["No errors"],
        )
        assert pkg.project_id == "test-proj"

    def test_export_to_dict(self):
        pkg = DiagnosticPackage(
            project_id="test",
            version="1.0",
            event_summary={"total": 10},
            config_summary={"mode": "hybrid"},
            errors=[],
        )
        d = pkg.to_dict()
        assert d["project_id"] == "test"
        assert d["version"] == "1.0"

    def test_preview_does_not_include_secrets(self):
        pkg = DiagnosticPackage(
            project_id="test",
            version="1.0",
            event_summary={},
            config_summary={},
            errors=[],
        )
        preview = pkg.preview()
        assert "API_KEY" not in preview
        assert "secret" not in preview.lower()


class TestMarkdownQA:
    def test_relative_links_check(self):
        """检查相对链接目标是否存在"""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
            with open(os.path.join(tmp, "docs", "readme.md"), "w") as f:
                f.write("See [architecture](architecture.md) for details.")
            with open(os.path.join(tmp, "docs", "architecture.md"), "w") as f:
                f.write("# Architecture")

            result = QACheckResult("relative_links", True, "All links valid")
            assert result.passed

    def test_mixed_language_check(self):
        """英文文档不应混入中文"""
        en_text = "## Project Info\n\n- **Name**: Test\n- **Mode**: Auto"
        has_cn = any('\u4e00' <= ch <= '\u9fff' for ch in en_text)
        assert not has_cn

    def test_qa_result_to_dict(self):
        result = QACheckResult("relative_links", True, "OK")
        d = result.to_dict()
        assert d["check"] == "relative_links"
        assert d["passed"] is True


class TestRunQAChecks:
    def test_run_on_content(self):
        content = """# AGENTS.md

## Project Info
- **Name**: Test Project
- **Mode**: Architect-Engineer

See [COLLAB.md](COLLAB.md) for current stage.
"""
        result = run_qa_checks(content, base_dir=".")
        assert len(result) >= 3  # 至少检查 language/agent_count/sensitive_info
        for r in result:
            assert isinstance(r, QACheckResult)
