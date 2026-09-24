"""Regression tests for audit fixes:
- get_task_diff working-tree pollution prevention
- DAG dependency status diagnosis (blocked/cancelled)
- MCP JSON-RPC null params and invalid payload robustness
- AI Assistant multiline pytest/unittest traceback diagnosis
- AI Assistant test fixture credential severity downgrade
- i18n key coverage
"""

import json
import os
import tempfile
import pytest
from pathlib import Path

from bridgelib.database import init_database
from bridgelib.coordinator import BridgeCoordinator
from bridgelib.state_machine import TaskState
from bridgelib.mcp_server import BridgeMCPServer
from bridgelib.ai_assistant import AIAssistant
from bridgelib.i18n import T, set_lang


@pytest.fixture
def audit_env(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    os.system(f"git -C \"{repo_dir}\" init")
    os.system(f"git -C \"{repo_dir}\" config user.name \"Audit Tester\"")
    os.system(f"git -C \"{repo_dir}\" config user.email \"audit@example.com\"")
    
    # Initial commit
    (repo_dir / "README.md").write_text("# Initial Repo\n", encoding="utf-8")
    os.system(f"git -C \"{repo_dir}\" add README.md")
    os.system(f"git -C \"{repo_dir}\" commit -m \"Initial commit\"")

    db_file = tmp_path / "audit_bridge.db"
    db = init_database(str(db_file))
    coord = BridgeCoordinator(database=db)
    pid = coord.init_project(name="Audit Project", root_path=str(repo_dir))
    gid = coord.create_goal(pid, "Audit Goal")
    return {
        "repo_dir": str(repo_dir),
        "db": db,
        "coord": coord,
        "project_id": pid,
        "goal_id": gid,
    }


def test_get_task_diff_prevents_main_repo_pollution(audit_env):
    """Ensure get_task_diff compares committed range and ignores main repo dirty files when worktree is closed."""
    coord = audit_env["coord"]
    repo_dir = audit_env["repo_dir"]
    gid = audit_env["goal_id"]

    import subprocess
    base_sha = subprocess.check_output(["git", "-C", repo_dir, "rev-parse", "HEAD"], text=True).strip()

    # Commit a feature on a task branch
    feature_file = Path(repo_dir) / "feature.py"
    feature_file.write_text("def task_feature(): return 42\n", encoding="utf-8")
    os.system(f"git -C \"{repo_dir}\" add feature.py")
    os.system(f"git -C \"{repo_dir}\" commit -m \"Implement task feature\"")
    commit_sha = subprocess.check_output(["git", "-C", repo_dir, "rev-parse", "HEAD"], text=True).strip()

    # Create task, register workspace record with base_commit, and record merge_queue with candidate_commit
    aid = coord.add_agent(audit_env["project_id"], "TestAgent")
    tid = coord.create_task(gid, "Committed Task")
    coord.db.conn.execute(
        "INSERT INTO workspaces (id, task_id, agent_id, worktree_path, branch, base_commit, status, created_at) "
        "VALUES (?, ?, ?, '/cleaned/path', 'task-branch', ?, 'cleaned', datetime('now'))",
        (f"ws-{tid}", tid, aid, base_sha),
    )
    coord.db.conn.execute(
        "INSERT INTO merge_queue (id, task_id, target_branch, candidate_commit, queue_position, status, created_at) "
        "VALUES (?, ?, 'master', ?, 1, 'queued', datetime('now'))",
        (f"mq-{tid}", tid, commit_sha),
    )
    coord.db.conn.commit()

    # Now create an unrelated uncommitted dirty file in main repository
    dirty_file = Path(repo_dir) / "unrelated_dirty.txt"
    dirty_file.write_text("Dirty file in main working copy\n", encoding="utf-8")

    # Inspect diff for task - it should strictly report feature.py and NOT unrelated_dirty.txt
    diff_data = coord.get_task_diff(tid)
    assert diff_data["task_id"] == tid
    assert diff_data["base_commit"] == base_sha
    assert diff_data["submission_commit"] == commit_sha
    assert "feature.py" in diff_data["files_changed"]
    assert "unrelated_dirty.txt" not in diff_data["files_changed"]
    assert "def task_feature" in diff_data["diff_text"]
    assert "Dirty file in main working copy" not in diff_data["diff_text"]


def test_dag_dependency_detailed_status(audit_env):
    """Test detailed DAG dependency inspection with blocked and cancelled parents."""
    coord = audit_env["coord"]
    gid = audit_env["goal_id"]

    p1_id = coord.create_task(gid, "Parent Done")
    p2_id = coord.create_task(gid, "Parent Cancelled")
    p3_id = coord.create_task(gid, "Parent In Progress")
    c_id = coord.create_task(gid, "Child Task")

    coord.add_task_dependency(c_id, p1_id)
    coord.add_task_dependency(c_id, p2_id)
    coord.add_task_dependency(c_id, p3_id)

    # Set states
    coord.db.conn.execute("UPDATE tasks SET state = ? WHERE id = ?", (TaskState.DONE.value, p1_id))
    coord.db.conn.execute("UPDATE tasks SET state = ? WHERE id = ?", (TaskState.CANCELLED.value, p2_id))
    coord.db.conn.execute("UPDATE tasks SET state = ? WHERE id = ?", (TaskState.IN_PROGRESS.value, p3_id))
    coord.db.conn.commit()

    status = coord.get_task_dependency_status(c_id)
    assert status["met"] is False
    assert status["total_dependencies"] == 3
    assert p1_id not in status["unmet_dependencies"]
    assert p2_id in status["unmet_dependencies"]
    assert p3_id in status["unmet_dependencies"]
    assert status["has_cancelled_parent"] is True
    assert status["permanently_blocked"] is True

    # Legacy method check
    met, unmet = coord.check_task_dependencies_met(c_id)
    assert met is False
    assert set(unmet) == {p2_id, p3_id}


def test_mcp_server_null_params_and_malformed_requests(audit_env):
    """Test MCP server handles null params, non-dict payloads, and unknown tools safely."""
    coord = audit_env["coord"]
    server = BridgeMCPServer(coordinator=coord, default_project_id=audit_env["project_id"])

    # 1. Non-dict request
    resp1 = server.handle_request("malformed json string")
    assert resp1["error"]["code"] == -32600

    # 2. Null params for tools/list
    resp2 = server.handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": None,
    })
    assert resp2["result"]["tools"] is not None

    # 3. Null arguments in tools/call
    resp3 = server.handle_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "bridge_list_tasks",
            "arguments": None,
        },
    })
    assert resp3["result"]["isError"] is False

    # 4. Notification (no id)
    resp4 = server.handle_request({
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    })
    assert resp4 is None


def test_ai_assistant_multiline_pytest_and_mock_secrets():
    """Test multiline pytest traceback extraction and mock secret detection."""
    ai = AIAssistant()

    # Standard pytest multiline traceback
    sample_pytest_log = """
============================= test session starts =============================
collected 2 items

tests/test_math.py .F                                                    [100%]

================================== FAILURES ===================================
__________________________________ test_add ___________________________________
tests/test_math.py:15: in test_add
    assert calculate(2, 3) == 6
E   AssertionError: assert 5 == 6
=========================== 1 failed in 0.15s ============================
"""
    diag = ai.diagnose_failure(
        task={"id": "T001", "title": "Fix Math"},
        validation_results=[{"status": "failed", "output": sample_pytest_log}],
    )
    assert diag.failure_category == "test_assertion"
    assert "AssertionError" in diag.root_cause
    assert "assert 5 == 6" in diag.root_cause

    # Test file mock secret vs production code secret
    test_diff = """
diff --git a/tests/test_auth.py b/tests/test_auth.py
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -10,1 +10,1 @@
+    fake_api_key = "1234567890abcdef123456"
"""
    report_test = ai.pre_review(
        task={"id": "T002", "title": "Add Auth Test"},
        diff_text=test_diff,
        changed_files=["tests/test_auth.py"],
    )
    # Mock secret in test should be warning, NOT critical
    crit_issues = [i for i in report_test.issues if i.severity == "critical"]
    assert len(crit_issues) == 0
    warn_issues = [i for i in report_test.issues if i.severity == "warning"]
    assert len(warn_issues) > 0
    assert "test dummy/mock" in warn_issues[0].description


def test_i18n_keys_completeness():
    """Verify newly added i18n keys exist for both Chinese and English."""
    keys = [
        "btn_view_diff",
        "btn_ai_review",
        "diff_window_title",
        "diff_base_commit",
        "diff_files_stat",
        "diff_no_changes",
        "diff_truncated",
        "ai_review_window_title",
        "ai_review_verdict",
        "ai_review_analyzing",
        "ctx_view_diff",
        "ctx_ai_review",
        "ctx_copy_id",
        "ctx_copy_prompt",
        "ctx_open_ws",
    ]
    for k in keys:
        zh_text = T(k, "zh", tid="T001", base="abc", files="a.py", stats="1 file", total=10, limit=5, verdict="OK", conf=95)
        en_text = T(k, "en", tid="T001", base="abc", files="a.py", stats="1 file", total=10, limit=5, verdict="OK", conf=95)
        assert zh_text != k, f"Key '{k}' missing Chinese translation"
        assert en_text != k, f"Key '{k}' missing English translation"
        assert zh_text != en_text, f"Key '{k}' should have distinct translations in zh and en"
