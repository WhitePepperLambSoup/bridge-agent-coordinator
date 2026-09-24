"""Unit and integration tests for AI Assistant, Pre-Review, and Auto-Fix."""

import json
import os
import subprocess
import pytest
from bridgelib.ai_assistant import AIAssistant, PreReviewIssue, AIPreReviewReport, AutoFixDiagnosis
from bridgelib.database import init_database
from bridgelib.coordinator import BridgeCoordinator
from bridgelib.state_machine import TaskState


def test_heuristic_code_checks_clean():
    assistant = AIAssistant()
    diff = """--- a/calc.py
+++ b/calc.py
@@ -1,3 +1,4 @@
 def add(a, b):
+    # Implementation
     return a + b
"""
    task = {"id": "TASK-001", "title": "Add helper"}
    report = assistant.pre_review(task, diff, changed_files=["calc.py"])
    assert report.verdict == "approved"
    assert report.scope_compliant is True
    assert len(report.issues) == 0


def test_heuristic_code_checks_scope_violation():
    assistant = AIAssistant()
    task = {
        "id": "TASK-002",
        "title": "Restricted Task",
        "allowed_paths_json": json.dumps(["src/**"]),
        "forbidden_paths_json": json.dumps([".bridge/**", "secrets/**"]),
    }
    diff = "diff --git a/secrets/key.txt b/secrets/key.txt\n+new_secret"
    report = assistant.pre_review(task, diff, changed_files=["secrets/key.txt"])
    assert report.verdict == "changes_requested"
    assert report.scope_compliant is False
    assert any(i.severity == "critical" and "violates task scope" in i.description for i in report.issues)


def test_heuristic_code_checks_security_and_conflict_markers():
    assistant = AIAssistant()
    diff = """--- a/service.py
+++ b/service.py
@@ -10,3 +10,8 @@
+<<<<<<< HEAD
+print('test')
+api_key = "sk-1234567890abcdef12345678"
+# TODO: clean this up later
+>>>>>>> candidate
"""
    task = {"id": "TASK-003", "title": "Service update"}
    report = assistant.pre_review(task, diff, changed_files=["service.py"])
    severities = [i.severity for i in report.issues]
    assert "critical" in severities  # conflict marker & hardcoded secret
    assert any("conflict marker" in i.description for i in report.issues)
    assert any("hardcoded secret" in i.description for i in report.issues)
    assert any("TODO" in i.description for i in report.issues)


def test_auto_fix_diagnosis_pytest_traceback():
    assistant = AIAssistant()
    task = {"id": "TASK-004", "title": "Math task"}
    val_results = [
        {
            "check_id": "unit-tests",
            "status": "failed",
            "output": """
============================= test session starts =============================
FAILED tests/test_math.py::test_division - ZeroDivisionError: division by zero
=========================== short test summary info ===========================
FAILED tests/test_math.py::test_division - ZeroDivisionError: division by zero
""",
        }
    ]
    diag = assistant.diagnose_failure(task, val_results)
    assert "test_division" in diag.failing_tests
    assert "division by zero" in diag.root_cause
    assert "Remediation Instruction" in diag.next_attempt_prompt
    assert "test_division" in diag.next_attempt_prompt


def test_auto_fix_diagnosis_syntax_error():
    assistant = AIAssistant()
    task = {"id": "TASK-005", "title": "Parser"}
    val_results = [
        {
            "check_id": "syntax",
            "status": "failed",
            "output": "SyntaxError: expected ':' at line 14",
        }
    ]
    diag = assistant.diagnose_failure(task, val_results)
    assert diag.failure_category == "syntax_error"
    assert "SyntaxError" in diag.root_cause


def test_merge_conflict_diagnosis():
    assistant = AIAssistant()
    res = assistant.diagnose_merge_conflict("TASK-006", ["models.py", "views.py"])
    assert res["conflict_count"] == 2
    assert "models.py" in res["conflict_files"]
    assert "Rebase" in res["recommended_action"]


def test_llm_custom_caller_injection():
    # Test that when a custom LLM caller is supplied, it properly enriches pre-review and diagnosis
    def fake_llm(system_prompt: str, user_prompt: str) -> str:
        if "PreReviewer" in system_prompt or "Review the Git diff" in system_prompt:
            return json.dumps({
                "verdict": "approved",
                "summary": "LLM verified diff correctly implements criteria.",
                "confidence": 0.99,
                "issues": [{"severity": "suggestion", "file": "app.py", "line": 5, "description": "Consider type hint", "suggestion": "Add -> int"}],
                "suggested_fixes": ["Add return type annotation"],
            })
        elif "Debugging" in system_prompt:
            return json.dumps({
                "failure_category": "test_assertion",
                "root_cause": "Expected 42 but got 0 due to uninitialized variable",
                "suggested_patch": "- return 0\n+ return 42",
                "next_attempt_prompt": "Fix variable initialization in calculate()",
            })
        return "{}"

    assistant = AIAssistant(llm_caller=fake_llm)
    task = {"id": "TASK-007", "title": "Calculation Task"}
    report = assistant.pre_review(task, "diff text", changed_files=["app.py"])
    assert report.confidence == 0.99
    assert report.summary == "LLM verified diff correctly implements criteria."

    diag = assistant.diagnose_failure(task, [{"check_id": "test", "status": "failed", "output": "error"}])
    assert diag.root_cause == "Expected 42 but got 0 due to uninitialized variable"
    assert "return 42" in diag.suggested_patch


def test_coordinator_integration_ai_pre_review_and_autofix(tmp_path):
    repo_dir = str(tmp_path / "repo")
    os.makedirs(repo_dir, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)

    dummy_file = os.path.join(repo_dir, "app.py")
    with open(dummy_file, "w") as f:
        f.write("def run(): pass\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    db_path = str(tmp_path / "bridge.db")
    db = init_database(db_path)
    coord = BridgeCoordinator(database=db)
    pid = coord.init_project("AI Project", repo_dir)
    gid = coord.create_goal(pid, "Goal 1")
    tid = coord.create_task(
        gid, "AI Task",
        allowed_paths=["app.py"],
        acceptance_criteria=["run function works"],
        required_checks=["lint"],
    )

    # 1. Test generate_ai_pre_review via coordinator
    report = coord.generate_ai_pre_review(tid)
    assert report.verdict in ("approved", "needs_human_attention")
    events = coord.db.conn.execute("SELECT * FROM events WHERE event_type = 'AIPreReviewGenerated'").fetchall()
    assert len(events) >= 1

    # 2. Record validation failure and test diagnose_task_failure via coordinator
    coord.db.conn.execute(
        """INSERT INTO validations (id, task_id, check_id, status, exit_code, output_summary, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        ("val-1", tid, "lint", "failed", 1, "SyntaxError: invalid syntax in app.py", "2026-01-01T00:00:00Z"),
    )
    coord.db.conn.commit()

    diag = coord.diagnose_task_failure(tid)

    assert diag.failure_category == "syntax_error"
    assert "SyntaxError" in diag.root_cause
    diag_events = coord.db.conn.execute("SELECT * FROM events WHERE event_type = 'AutoFixDiagnosisGenerated'").fetchall()
    assert len(diag_events) >= 1

    # 3. Test diagnose_merge_conflict via coordinator
    conflict_res = coord.diagnose_merge_conflict(tid, ["app.py"])
    assert conflict_res["conflict_count"] == 1
