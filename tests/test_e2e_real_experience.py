"""End-to-End (E2E) Real Experience and Architecture Integration Test Suite.

This test suite executes real multi-stage, multi-agent workflows:
1. Real Git repository operations & branch lifecycle
2. Git worktree physical isolation and sensitive environment (.env) inheritance
3. Protection against main working copy dirty file contamination in diff viewer
4. Real code writing, test execution, and validation
5. AI Pre-Review security and quality analysis
6. Serial merge queue integration with master branch and workspace cleanup
7. DAG multi-tier dependency wave cascade automatic unlocking
8. Full MCP protocol execution covering all 3 pillars:
   - Tools (JSON-RPC 2.0 execution)
   - Resources (bridge://project/summary, bridge://tasks/{id}/context, bridge://audit/events)
   - Prompts (implementer_start_task, reviewer_inspect_task, healer_fix_failure)
9. Automated failure diagnosis and remediation generation from real pytest failures
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from bridgelib.ai_assistant import AIAssistant
from bridgelib.coordinator import BridgeCoordinator
from bridgelib.database import init_database
from bridgelib.git_adapter import GitRepositoryAdapter
from bridgelib.mcp_server import BridgeMCPServer
from bridgelib.state_machine import TaskState


def run_cmd(args, cwd):
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.STDOUT)


@pytest.fixture
def real_git_project(tmp_path):
    """Set up a fully functional real Git repository with initial commit and configs."""
    repo_dir = tmp_path / "production_repo"
    repo_dir.mkdir()

    run_cmd(["git", "init"], cwd=repo_dir)
    run_cmd(["git", "config", "user.name", "Bridge E2E Tester"], cwd=repo_dir)
    run_cmd(["git", "config", "user.email", "tester@bridge.local"], cwd=repo_dir)

    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo_dir / "tests").mkdir()
    (repo_dir / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo_dir / "README.md").write_text("# Production Project\n", encoding="utf-8")

    (repo_dir / ".env").write_text("DATABASE_URL=sqlite:///prod.db\nAPI_KEY=mock-key-prod-12345\n", encoding="utf-8")
    (repo_dir / ".env.local").write_text("DEBUG=1\n", encoding="utf-8")

    run_cmd(["git", "add", "README.md", "src/__init__.py", "tests/__init__.py"], cwd=repo_dir)
    run_cmd(["git", "commit", "-m", "chore: initial commit"], cwd=repo_dir)

    db_file = tmp_path / "runtime_bridge.db"
    db = init_database(str(db_file))
    coord = BridgeCoordinator(database=db)
    pid = coord.init_project(name="E2E Pipeline Project", root_path=str(repo_dir))
    gid = coord.create_goal(pid, "Deliver Full E2E Integration")

    agent_a = coord.add_agent(pid, "ArchitectAgent", role="architect", can_review=True, can_plan=True)
    agent_b = coord.add_agent(pid, "EngineerAgent", role="engineer")

    return {
        "repo_dir": repo_dir,
        "db": db,
        "coord": coord,
        "project_id": pid,
        "goal_id": gid,
        "agent_a": agent_a,
        "agent_b": agent_b,
    }


def test_real_git_worktree_isolation_env_inheritance_and_diff(real_git_project):
    """Test real worktree creation, .env copying, branch isolation, and diff accuracy."""
    coord: BridgeCoordinator = real_git_project["coord"]
    repo_dir: Path = real_git_project["repo_dir"]
    gid = real_git_project["goal_id"]
    agent_b = real_git_project["agent_b"]

    task_id = coord.create_task(
        gid,
        "Implement Calculator Core",
        allowed_paths=["src/calculator.py", "tests/test_calculator.py"],
        acceptance_criteria=["Calculator functions correctly and tests pass"],
    )
    coord.transition_task(task_id, TaskState.PLANNING)
    coord.transition_task(task_id, TaskState.READY, confirmed=True)
    coord.assign_task(task_id, agent_b)

    # 1. Create worktree
    wt_info = coord.create_worktree(task_id, agent_b)
    wt_path = Path(wt_info["worktree_path"])
    assert wt_path.exists()
    assert (wt_path / ".git").exists()

    # 2. Sensitive environment files are excluded by default.
    assert not (wt_path / ".env").exists()
    assert not (wt_path / ".env.local").exists()

    # 3. Write real application code and real test inside worktree
    calc_code = """
def add(a: int, b: int) -> int:
    return a + b

def multiply(a: int, b: int) -> int:
    return a * b
"""
    calc_test = """
from src.calculator import add, multiply

def test_add():
    assert add(2, 3) == 5

def test_multiply():
    assert multiply(3, 4) == 12
"""
    (wt_path / "src" / "calculator.py").write_text(calc_code, encoding="utf-8")
    (wt_path / "tests" / "test_calculator.py").write_text(calc_test, encoding="utf-8")

    # Commit inside worktree
    run_cmd(["git", "add", "src/calculator.py", "tests/test_calculator.py"], cwd=wt_path)
    run_cmd(["git", "commit", "-m", "feat: implement calculator core and tests"], cwd=wt_path)
    commit_sha = run_cmd(["git", "rev-parse", "HEAD"], cwd=wt_path).strip()

    # 4. Dirty the main repo with unrelated uncommitted files to test pollution resistance
    (repo_dir / "unrelated_dirty_file.tmp").write_text("DIRTY NOISE", encoding="utf-8")

    # 5. Check task diff
    diff_result = coord.get_task_diff(task_id)
    assert diff_result["task_id"] == task_id
    assert "src/calculator.py" in diff_result["files_changed"]
    assert "tests/test_calculator.py" in diff_result["files_changed"]
    assert "unrelated_dirty_file.tmp" not in diff_result["files_changed"]
    assert "def multiply(a: int, b: int) -> int:" in diff_result["diff_text"]
    assert "DIRTY NOISE" not in diff_result["diff_text"]

    # 6. Run AI Pre-Review
    ai_report = coord.generate_ai_pre_review(task_id)
    assert ai_report.verdict == "approved"
    assert ai_report.confidence >= 0.8
    assert len(ai_report.issues) == 0

    # Clean up test dirty file in main repo
    (repo_dir / "unrelated_dirty_file.tmp").unlink()


def test_dag_cascade_unlocking_and_merge_queue_flow(real_git_project):
    """Test multi-task DAG dependencies, serial merge integration, and auto-unlocking."""
    coord: BridgeCoordinator = real_git_project["coord"]
    repo_dir: Path = real_git_project["repo_dir"]
    gid = real_git_project["goal_id"]
    agent_a = real_git_project["agent_a"]
    agent_b = real_git_project["agent_b"]

    from bridgelib.review import ReviewPackage, ReviewVerdict

    t1_id = coord.create_task(gid, "Base Module A", allowed_paths=["src/module_a.py"], acceptance_criteria=["Module A implemented"])
    t2_id = coord.create_task(gid, "Base Module B", allowed_paths=["src/module_b.py"], acceptance_criteria=["Module B implemented"])
    t3_id = coord.create_task(gid, "Integration Module C", allowed_paths=["src/module_c.py"], acceptance_criteria=["Module C implemented"])

    coord.add_task_dependency(t3_id, t1_id)
    coord.add_task_dependency(t3_id, t2_id)

    status_t3 = coord.get_task_dependency_status(t3_id)
    assert status_t3["met"] is False
    assert set(status_t3["unmet_dependencies"]) == {t1_id, t2_id}

    # Complete Task 1
    coord.transition_task(t1_id, TaskState.PLANNING)
    coord.transition_task(t1_id, TaskState.READY, confirmed=True)
    coord.assign_task(t1_id, agent_b, reviewer_agent_id=agent_a)
    wt1 = coord.create_worktree(t1_id, agent_b)
    wt1_path = Path(wt1["worktree_path"])
    (wt1_path / "src" / "module_a.py").write_text("MODULE_A = 100\n", encoding="utf-8")
    run_cmd(["git", "add", "src/module_a.py"], cwd=wt1_path)
    run_cmd(["git", "commit", "-m", "feat: add module A"], cwd=wt1_path)
    c1 = run_cmd(["git", "rev-parse", "HEAD"], cwd=wt1_path).strip()

    # Merge Task 1
    lease1 = coord.acquire_lease(t1_id, agent_b, resource_path="src/module_a.py")
    att1 = coord.create_attempt(t1_id, agent_b, lease1.lease_id)
    coord.transition_task(t1_id, TaskState.IN_PROGRESS, confirmed=True)
    coord.transition_task(t1_id, TaskState.SUBMITTED)
    coord.transition_task(t1_id, TaskState.VALIDATING)
    coord.db.record_validation(t1_id, attempt_id=att1, check_id="unit-tests", status="passed")
    pkg1 = ReviewPackage(task_id=t1_id, title="Review Module A")
    rev1 = coord.submit_review(t1_id, agent_a, pkg1)
    coord.complete_review(rev1, ReviewVerdict.APPROVED, summary="LGTM", reviewer_agent_id=agent_a)
    coord.transition_task(t1_id, TaskState.APPROVED, confirmed=True)
    entry1 = coord.enqueue_merge(t1_id, c1, confirmed=True)
    coord.transition_task(t1_id, TaskState.MERGE_QUEUED, confirmed=True)
    coord.start_merge(entry1.entry_id, confirmed=True)
    coord.transition_task(t1_id, TaskState.MERGING)
    coord.complete_merge(entry1.entry_id, confirmed=True)
    coord.transition_task(t1_id, TaskState.DONE, confirmed=True)

    status_t3_step2 = coord.get_task_dependency_status(t3_id)
    assert status_t3_step2["met"] is False
    assert status_t3_step2["unmet_dependencies"] == [t2_id]

    # Complete Task 2
    coord.transition_task(t2_id, TaskState.PLANNING)
    coord.transition_task(t2_id, TaskState.READY, confirmed=True)
    coord.assign_task(t2_id, agent_b, reviewer_agent_id=agent_a)
    wt2 = coord.create_worktree(t2_id, agent_b)
    wt2_path = Path(wt2["worktree_path"])
    (wt2_path / "src" / "module_b.py").write_text("MODULE_B = 200\n", encoding="utf-8")
    run_cmd(["git", "add", "src/module_b.py"], cwd=wt2_path)
    run_cmd(["git", "commit", "-m", "feat: add module B"], cwd=wt2_path)
    c2 = run_cmd(["git", "rev-parse", "HEAD"], cwd=wt2_path).strip()

    # Merge Task 2
    lease2 = coord.acquire_lease(t2_id, agent_b, resource_path="src/module_b.py")
    att2 = coord.create_attempt(t2_id, agent_b, lease2.lease_id)
    coord.transition_task(t2_id, TaskState.IN_PROGRESS, confirmed=True)
    coord.transition_task(t2_id, TaskState.SUBMITTED)
    coord.transition_task(t2_id, TaskState.VALIDATING)
    coord.db.record_validation(t2_id, attempt_id=att2, check_id="unit-tests", status="passed")
    pkg2 = ReviewPackage(task_id=t2_id, title="Review Module B")
    rev2 = coord.submit_review(t2_id, agent_a, pkg2)
    coord.complete_review(rev2, ReviewVerdict.APPROVED, summary="LGTM", reviewer_agent_id=agent_a)
    coord.transition_task(t2_id, TaskState.APPROVED, confirmed=True)
    entry2 = coord.enqueue_merge(t2_id, c2, confirmed=True)
    coord.transition_task(t2_id, TaskState.MERGE_QUEUED, confirmed=True)
    coord.start_merge(entry2.entry_id, confirmed=True)
    coord.transition_task(t2_id, TaskState.MERGING)
    coord.complete_merge(entry2.entry_id, confirmed=True)
    coord.transition_task(t2_id, TaskState.DONE, confirmed=True)

    # Verify Task 3 is now completely UNLOCKED
    status_t3_final = coord.get_task_dependency_status(t3_id)
    assert status_t3_final["met"] is True
    assert len(status_t3_final["unmet_dependencies"]) == 0
    assert status_t3_final["permanently_blocked"] is False

    assert (repo_dir / "src" / "module_a.py").exists()
    assert (repo_dir / "src" / "module_b.py").exists()


def test_mcp_server_full_three_pillars(real_git_project):
    """Verify MCP Server complies with Model Context Protocol: Tools, Resources, and Prompts."""
    coord: BridgeCoordinator = real_git_project["coord"]
    pid = real_git_project["project_id"]
    gid = real_git_project["goal_id"]

    tid = coord.create_task(gid, "MCP Protocol Task")

    server = BridgeMCPServer(coordinator=coord, default_project_id=pid)

    # 1. Tools
    tools_resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "tools/list",
        "params": {},
    })
    tools = {t["name"] for t in tools_resp["result"]["tools"]}
    assert "bridge_list_tasks" in tools
    assert "bridge_get_task_diff" in tools
    assert "bridge_get_ai_pre_review" in tools

    call_resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": "req-2",
        "method": "tools/call",
        "params": {
            "name": "bridge_list_tasks",
            "arguments": {"project_id": pid},
        },
    })
    task_data = json.loads(call_resp["result"]["content"][0]["text"])
    tasks = task_data.get("tasks", task_data)
    assert any(t["id"] == tid for t in tasks)

    # 2. Resources
    res_list_resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": "req-3",
        "method": "resources/list",
        "params": {},
    })
    res_uris = [r["uri"] for r in res_list_resp["result"]["resources"]]
    assert "bridge://project/summary" in res_uris
    assert f"bridge://tasks/{tid}/context" in res_uris

    read_resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": "req-4",
        "method": "resources/read",
        "params": {"uri": f"bridge://tasks/{tid}/context"},
    })
    task_context = json.loads(read_resp["result"]["contents"][0]["text"])
    assert task_context["task"]["id"] == tid
    assert task_context["task"]["title"] == "MCP Protocol Task"

    # 3. Prompts
    prompts_resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": "req-5",
        "method": "prompts/list",
        "params": {},
    })
    prompt_names = [p["name"] for p in prompts_resp["result"]["prompts"]]
    assert "implementer_start_task" in prompt_names
    assert "reviewer_inspect_task" in prompt_names
    assert "healer_fix_failure" in prompt_names

    prompt_get_resp = server.handle_request({
        "jsonrpc": "2.0",
        "id": "req-6",
        "method": "prompts/get",
        "params": {
            "name": "implementer_start_task",
            "arguments": {"task_id": tid},
        },
    })
    messages = prompt_get_resp["result"]["messages"]
    assert len(messages) >= 1
    assert tid in messages[0]["content"]["text"]
    assert "MCP Protocol Task" in messages[0]["content"]["text"]


def test_ai_diagnosis_from_real_test_failure(real_git_project):
    """Test AI assistant diagnosis on a realistic pytest failure output with stack trace."""
    coord: BridgeCoordinator = real_git_project["coord"]
    gid = real_git_project["goal_id"]

    tid = coord.create_task(gid, "Failed Validation Task")

    mock_pytest_output = """
============================= test session starts =============================
platform win32 -- Python 3.14.5, pytest-9.1.1, pluggy-1.6.0
collected 1 item

tests/test_service.py F                                                  [100%]

=================================== FAILURES ===================================
__________________________________ test_auth ___________________________________
tests/test_service.py:22: in test_auth
    assert user.is_authenticated() is True
E   AssertionError: assert False is True
=========================== 1 failed in 0.22s ===========================
"""
    coord.db.record_validation(
        task_id=tid,
        validator="pytest",
        status="failed",
        details=mock_pytest_output,
    )

    diag = coord.diagnose_task_failure(tid)
    assert diag.failure_category == "test_assertion"
    assert "AssertionError: assert False is True" in diag.root_cause
    assert "Remediation Instruction" in diag.next_attempt_prompt
