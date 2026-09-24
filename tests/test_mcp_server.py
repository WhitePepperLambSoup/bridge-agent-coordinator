"""Unit tests for Bridge MCP Server (Model Context Protocol)."""

import json
import os
import subprocess
import pytest
from bridgelib.database import init_database
from bridgelib.coordinator import BridgeCoordinator, CoordinatorError
from bridgelib.mcp_server import BridgeMCPServer, create_mcp_server
from bridgelib.state_machine import TaskState


@pytest.fixture
def mcp_env(tmp_path):
    repo_dir = str(tmp_path / "repo")
    os.makedirs(repo_dir, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)

    dummy_file = os.path.join(repo_dir, "hello.txt")
    with open(dummy_file, "w") as f:
        f.write("hello")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    db_path = str(tmp_path / "bridge.db")
    db = init_database(db_path)
    coord = BridgeCoordinator(database=db)
    pid = coord.init_project("MCP Project", repo_dir)
    gid = coord.create_goal(pid, "Goal 1")
    tid = coord.create_task(
        gid, "Task 1",
        allowed_paths=["**"],
        acceptance_criteria=["Criteria 1"],
        required_checks=["mock-check"],
    )

    server = BridgeMCPServer(coordinator=coord, default_project_id=pid)
    return {
        "server": server,
        "coord": coord,
        "db": db,
        "pid": pid,
        "gid": gid,
        "tid": tid,
        "repo_dir": repo_dir,
    }


def test_mcp_initialize(mcp_env):
    server = mcp_env["server"]
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    }
    resp = server.handle_request(req)
    assert resp["id"] == 1
    assert "protocolVersion" in resp["result"]
    assert resp["result"]["serverInfo"]["name"] == "bridge-agent-coordinator"


def test_mcp_ping_and_notification(mcp_env):
    server = mcp_env["server"]
    req = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    resp = server.handle_request(req)
    assert resp["id"] == 2
    assert resp["result"] == {}

    # Notification has no id and returns None
    notify = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    resp = server.handle_request(notify)
    assert resp is None


def test_mcp_tools_list(mcp_env):
    server = mcp_env["server"]
    req = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    resp = server.handle_request(req)
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "bridge_list_tasks" in tool_names
    assert "bridge_get_task" in tool_names
    assert "bridge_claim_task" in tool_names
    assert "bridge_report_progress" in tool_names
    assert "bridge_submit_task" in tool_names
    assert "bridge_run_validation" in tool_names
    assert "bridge_get_ai_pre_review" in tool_names

    assert "bridge_diagnose_failure" in tool_names
    assert "bridge_add_dependency" in tool_names
    assert "bridge_get_dag_plan" in tool_names
    assert "bridge_get_task_diff" in tool_names




def test_mcp_list_and_get_tasks(mcp_env):
    server = mcp_env["server"]
    tid = mcp_env["tid"]

    # 1. List tasks
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "bridge_list_tasks",
            "arguments": {},
        },
    }
    resp = server.handle_request(req)
    assert not resp["result"]["isError"]
    data = json.loads(resp["result"]["content"][0]["text"])
    assert data["count"] >= 1
    assert any(t["id"] == tid for t in data["tasks"])

    # 2. Get task details
    req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "bridge_get_task",
            "arguments": {"task_id": tid},
        },
    }
    resp = server.handle_request(req)
    assert not resp["result"]["isError"]
    task_data = json.loads(resp["result"]["content"][0]["text"])
    assert task_data["task"]["id"] == tid
    assert task_data["task"]["allowed_paths"] == ["**"]


def test_mcp_claim_progress_and_submit_lifecycle(mcp_env):
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = mcp_env["tid"]
    agent_id = "test-cursor-agent"

    # Register ephemeral validation mock so checks pass
    coord.register_check_command("mock-check", "python", ["-c", "print('mock passed')"], confirmed=True)

    # 1. Claim task
    claim_req = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "bridge_claim_task",
            "arguments": {
                "task_id": tid,
                "agent_id": agent_id,
            },
        },
    }
    claim_resp = server.handle_request(claim_req)
    assert not claim_resp["result"]["isError"], claim_resp["result"]["content"][0]["text"]
    claim_data = json.loads(claim_resp["result"]["content"][0]["text"])
    assert claim_data["status"] == "claimed"
    assert claim_data["task_id"] == tid
    worktree_path = claim_data["worktree_path"]
    assert os.path.isdir(worktree_path)

    # Verify task state is now in_progress
    task = coord.get_task(tid)
    assert task["state"] == TaskState.IN_PROGRESS.value

    # 2. Report progress
    prog_req = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "bridge_report_progress",
            "arguments": {
                "task_id": tid,
                "agent_id": agent_id,
                "status": "progress",
                "notes": "Finished writing unit test",
                "input_tokens": 1200,
                "output_tokens": 400,
            },
        },
    }
    prog_resp = server.handle_request(prog_req)
    assert not prog_resp["result"]["isError"]

    # Check token costs got recorded
    costs = coord.costs.records_by_task(tid)
    assert sum(c.input_tokens for c in costs) == 1200
    assert sum(c.output_tokens for c in costs) == 400

    # 3. Modify worktree and commit
    code_file = os.path.join(worktree_path, "feature.py")
    with open(code_file, "w") as f:
        f.write("def feature(): return True\n")
    subprocess.run(["git", "add", "."], cwd=worktree_path, check=True)
    subprocess.run(["git", "commit", "-m", "implement feature"], cwd=worktree_path, check=True)

    git_r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree_path, capture_output=True, text=True, check=True)
    head_sha = git_r.stdout.strip()

    # 4. Run validation
    val_req = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {
            "name": "bridge_run_validation",
            "arguments": {"task_id": tid, "agent_id": agent_id},
        },
    }
    val_resp = server.handle_request(val_req)
    assert not val_resp["result"]["isError"]
    val_data = json.loads(val_resp["result"]["content"][0]["text"])
    assert val_data["all_passed"] is True

    # 5. Submit task
    submit_req = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {
            "name": "bridge_submit_task",
            "arguments": {
                "task_id": tid,
                "agent_id": agent_id,
                "submission_commit": head_sha,
                "summary": "Implemented feature successfully",
                "test_summary": "All tests passed",
                "input_tokens": 2000,
                "output_tokens": 800,
            },
        },
    }
    submit_resp = server.handle_request(submit_req)
    assert not submit_resp["result"]["isError"], submit_resp["result"]["content"][0]["text"]
    submit_data = json.loads(submit_resp["result"]["content"][0]["text"])
    assert submit_data["status"] == "submitted"
    assert submit_data["submission_commit"] == head_sha

    # MCP submission supplies authoritative usage totals, so receipt fallback
    # estimates must not be added a second time.
    receipt_costs = coord.db.conn.execute(
        "SELECT input_tokens, output_tokens, is_estimated, source "
        "FROM cost_records WHERE task_id = ? AND source = 'receipt'",
        (tid,),
    ).fetchall()
    assert receipt_costs == []

    # submit_task reports final cumulative usage.  Progress already recorded
    # 1200/400, so only the unreported 800/400 delta may be added here.
    submit_costs = coord.db.conn.execute(
        "SELECT input_tokens, output_tokens, is_estimated, source "
        "FROM cost_records WHERE task_id = ? AND source = 'mcp_submit'",
        (tid,),
    ).fetchall()
    assert [dict(row) for row in submit_costs] == [
        {
            "input_tokens": 800,
            "output_tokens": 400,
            "is_estimated": 1,
            "source": "mcp_submit",
        }
    ]
    assert coord.check_budget(tid)["total_tokens"] == 2800

    # A lost MCP response must be safe to retry without duplicating receipt,
    # state, or cost records.
    retry_resp = server.handle_request(submit_req)
    assert not retry_resp["result"]["isError"], retry_resp["result"]["content"][0]["text"]
    retry_data = json.loads(retry_resp["result"]["content"][0]["text"])
    assert retry_data["status"] == "submitted"
    assert retry_data["idempotent"] is True
    assert coord.check_budget(tid)["total_tokens"] == 2800

    # Verify task state in coordinator is now SUBMITTED
    task = coord.get_task(tid)
    assert task["state"] == TaskState.SUBMITTED.value

    # 6. Test bridge_get_ai_pre_review
    review_req = {
        "jsonrpc": "2.0",
        "id": 13,
        "method": "tools/call",
        "params": {
            "name": "bridge_get_ai_pre_review",
            "arguments": {"task_id": tid},
        },
    }
    review_resp = server.handle_request(review_req)
    assert not review_resp["result"]["isError"]
    review_data = json.loads(review_resp["result"]["content"][0]["text"])
    assert review_data["report"]["verdict"] in ("approved", "needs_human_attention")

    # 7. Test bridge_diagnose_failure
    diag_req = {
        "jsonrpc": "2.0",
        "id": 14,
        "method": "tools/call",
        "params": {
            "name": "bridge_diagnose_failure",
            "arguments": {"task_id": tid},
        },
    }
    diag_resp = server.handle_request(diag_req)
    assert not diag_resp["result"]["isError"]
    diag_data = json.loads(diag_resp["result"]["content"][0]["text"])
    assert "diagnosis" in diag_data



def test_mcp_unknown_tool_and_invalid_arguments(mcp_env):
    server = mcp_env["server"]

    # Unknown tool
    req = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "non_existent_tool",
            "arguments": {},
        },
    }
    resp = server.handle_request(req)
    assert resp["result"]["isError"] is True
    assert "Unknown tool" in resp["result"]["content"][0]["text"]

    # Missing task id
    req = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "bridge_get_task",
            "arguments": {},
        },
    }
    resp = server.handle_request(req)
    assert resp["result"]["isError"] is True

    # Unknown method
    req = {
        "jsonrpc": "2.0",
        "id": 12,
        "method": "unknown/method",
        "params": {},
    }
    resp = server.handle_request(req)
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_mcp_claim_rolls_back_all_state_when_worktree_creation_fails(mcp_env, monkeypatch):
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = mcp_env["tid"]

    def fail_worktree(*_args, **_kwargs):
        raise CoordinatorError("injected worktree failure")

    monkeypatch.setattr(coord, "create_worktree", fail_worktree)
    with pytest.raises(CoordinatorError, match="injected worktree failure"):
        server._handle_claim_task({"task_id": tid, "agent_id": "rollback-agent"})

    task = coord.get_task(tid)
    assert task["state"] == TaskState.DRAFT.value
    assert task.get("owner_agent_id") in (None, "")
    assert coord.db.list_leases_by_task(tid) == []
    assert coord.list_attempts(tid) == []
    assert coord.db.list_active_workspaces() == []


def test_mcp_progress_rejects_non_owner_agent(mcp_env):
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = mcp_env["tid"]
    coord.register_check_command("mock-check", "python", ["-c", "print('ok')"], confirmed=True)

    claimed = server._handle_claim_task({"task_id": tid, "agent_id": "owner-agent"})
    assert claimed["status"] == "claimed"
    with pytest.raises(CoordinatorError, match="owner|lease|authorized|authorised"):
        server._handle_report_progress(
            {"task_id": tid, "agent_id": "spoof-agent", "status": "blocked", "notes": "spoof"}
        )
    assert coord.get_task(tid)["state"] == TaskState.IN_PROGRESS.value


def test_mcp_progress_rejects_negative_token_counts(mcp_env):
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = mcp_env["tid"]

    server._handle_claim_task({"task_id": tid, "agent_id": "token-agent"})

    with pytest.raises(CoordinatorError, match="non-negative"):
        server._handle_report_progress(
            {
                "task_id": tid,
                "agent_id": "token-agent",
                "input_tokens": -1,
                "output_tokens": 0,
            }
        )

    assert coord.costs.records_by_task(tid) == []


def test_mcp_progress_rejects_unknown_status_without_recording_cost(mcp_env):
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = mcp_env["tid"]

    server._handle_claim_task({"task_id": tid, "agent_id": "status-agent"})

    with pytest.raises(CoordinatorError, match="status|progress|blocked"):
        server._handle_report_progress(
            {
                "task_id": tid,
                "agent_id": "status-agent",
                "status": "finished",
                "input_tokens": 10,
                "output_tokens": 2,
            }
        )

    assert coord.costs.records_by_task(tid) == []


def test_mcp_progress_is_rejected_after_submission(mcp_env):
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = mcp_env["tid"]

    server._handle_claim_task({"task_id": tid, "agent_id": "lifecycle-agent"})
    coord.transition_task(tid, TaskState.SUBMITTED, confirmed=True)

    with pytest.raises(CoordinatorError, match="in_progress"):
        server._handle_report_progress(
            {
                "task_id": tid,
                "agent_id": "lifecycle-agent",
                "status": "progress",
                "input_tokens": 1,
            }
        )


def test_mcp_submit_does_not_double_count_receipt_estimates(mcp_env):
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = mcp_env["tid"]
    agent_id = "cost-agent"

    coord.register_check_command(
        "mock-check", "python", ["-c", "print('mock passed')"], confirmed=True,
    )
    claimed = server._handle_claim_task({"task_id": tid, "agent_id": agent_id})
    worktree_path = claimed["worktree_path"]
    with open(os.path.join(worktree_path, "feature.py"), "w", encoding="utf-8") as handle:
        handle.write("VALUE = 1\n")
    subprocess.run(["git", "add", "feature.py"], cwd=worktree_path, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=worktree_path, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=worktree_path, text=True,
    ).strip()

    server._handle_submit_task(
        {
            "task_id": tid,
            "agent_id": agent_id,
            "submission_commit": commit,
            "input_tokens": 100,
            "output_tokens": 50,
        }
    )

    # MCP totals are authoritative for this path; receipt fallback estimates
    # must not inflate the task budget.
    assert coord.check_budget(tid)["total_tokens"] == 150
    receipt_costs = coord.db.conn.execute(
        "SELECT 1 FROM cost_records WHERE task_id = ? AND source = 'receipt'",
        (tid,),
    ).fetchall()
    assert receipt_costs == []


def test_mcp_submit_keeps_protocol_package_outside_git_worktree(mcp_env):
    """MCP protocol files must not create untracked changes in the agent worktree."""
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = mcp_env["tid"]
    agent_id = "package-location-agent"

    coord.register_check_command(
        "mock-check", "python", ["-c", "print('mock passed')"], confirmed=True,
    )
    claimed = server._handle_claim_task({"task_id": tid, "agent_id": agent_id})
    worktree_path = claimed["worktree_path"]
    with open(os.path.join(worktree_path, "feature.py"), "w", encoding="utf-8") as handle:
        handle.write("VALUE = 1\n")
    subprocess.run(["git", "add", "feature.py"], cwd=worktree_path, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=worktree_path, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=worktree_path, text=True,
    ).strip()
    server._handle_run_validation({"task_id": tid, "agent_id": agent_id})

    result = server._handle_submit_task({
        "task_id": tid,
        "agent_id": agent_id,
        "submission_commit": commit,
    })

    package_dir = os.path.realpath(result["package_dir"])
    worktree_real = os.path.realpath(worktree_path)
    assert os.path.commonpath([package_dir, worktree_real]) != worktree_real
    assert os.path.isfile(os.path.join(package_dir, "RECEIPT.md"))
    assert os.path.isfile(os.path.join(package_dir, "ARTIFACTS.json"))
    status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=worktree_path, text=True,
    )
    assert status.strip() == ""


def test_mcp_claim_does_not_choose_non_reviewer_fallback(mcp_env):
    """Auto-routing must never assign an implementer as the reviewer."""
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = mcp_env["tid"]
    pid = mcp_env["pid"]

    coord.add_agent(
        pid,
        "Existing Implementer",
        id="existing-implementer",
        roles=["implementer"],
        can_review=False,
    )

    claimed = server._handle_claim_task({"task_id": tid, "agent_id": "new-agent"})
    assert claimed["status"] == "claimed"
    task = coord.get_task(tid)
    reviewer_id = task["reviewer_agent_id"]
    assert reviewer_id != "existing-implementer"
    reviewer = coord.get_agent(reviewer_id)
    assert reviewer is not None
    assert json.loads(reviewer["permissions_json"]).get("can_review") is True


def test_mcp_submit_failure_does_not_leave_receipt_or_cost(mcp_env, monkeypatch):
    """A failed final transition must be retryable without stale audit rows."""
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = mcp_env["tid"]
    agent_id = "rollback-agent"

    coord.register_check_command(
        "mock-check", "python", ["-c", "print('mock passed')"], confirmed=True,
    )
    claimed = server._handle_claim_task({"task_id": tid, "agent_id": agent_id})
    worktree = claimed["worktree_path"]
    with open(os.path.join(worktree, "feature.py"), "w", encoding="utf-8") as handle:
        handle.write("VALUE = 1\n")
    subprocess.run(["git", "add", "feature.py"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=worktree, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=worktree, text=True,
    ).strip()
    server._handle_run_validation({"task_id": tid, "agent_id": agent_id})

    original_transition = coord.transition_task

    def fail_submit(task_id, to_state, *args, **kwargs):
        if to_state == TaskState.SUBMITTED:
            raise CoordinatorError("injected submit transition failure")
        return original_transition(task_id, to_state, *args, **kwargs)

    monkeypatch.setattr(coord, "transition_task", fail_submit)
    with pytest.raises(CoordinatorError, match="transition failure"):
        server._handle_submit_task({
            "task_id": tid,
            "agent_id": agent_id,
            "submission_commit": commit,
            "input_tokens": 10,
            "output_tokens": 5,
        })

    assert coord.get_task(tid)["state"] == TaskState.IN_PROGRESS.value
    assert coord.db.list_receipts(tid) == []
    assert coord.db.conn.execute(
        "SELECT 1 FROM cost_records WHERE task_id = ? AND source = 'mcp_submit'",
        (tid,),
    ).fetchall() == []
    assert coord.get_current_attempt(tid) is not None
    assert coord.get_active_lease(tid) is not None


def test_mcp_default_project_scopes_task_reads(mcp_env):
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    other_pid = coord.init_project("Other Project", mcp_env["repo_dir"])
    other_gid = coord.create_goal(other_pid, "Other Goal")
    other_tid = coord.create_task(
        other_gid,
        "Other Task",
        allowed_paths=["**"],
        acceptance_criteria=["Other criteria"],
    )

    listed = server._handle_list_tasks({})
    assert all(task["id"] != other_tid for task in listed["tasks"])

    with pytest.raises(CoordinatorError, match="project"):
        server._handle_get_task({"task_id": other_tid})

    with pytest.raises(CoordinatorError, match="project"):
        server._handle_resources_read(f"bridge://tasks/{other_tid}/context")


def test_mcp_without_explicit_project_still_scopes_to_first_project(mcp_env):
    coord = mcp_env["coord"]
    other_pid = coord.init_project("Other Project", mcp_env["repo_dir"])
    other_gid = coord.create_goal(other_pid, "Other Goal")
    other_tid = coord.create_task(
        other_gid,
        "Other Task",
        allowed_paths=["**"],
        acceptance_criteria=["Other criteria"],
    )

    server = BridgeMCPServer(coordinator=coord)
    listed = server._handle_list_tasks({})

    assert all(task["id"] != other_tid for task in listed["tasks"])
    with pytest.raises(CoordinatorError, match="configured project|project"):
        server._handle_get_task({"task_id": other_tid})
    with pytest.raises(CoordinatorError, match="configured project|project"):
        server._handle_list_tasks({"project_id": other_pid})


def test_mcp_audit_resource_does_not_leak_other_project_events(mcp_env):
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    other_pid = coord.init_project("Other Project", mcp_env["repo_dir"])
    other_gid = coord.create_goal(other_pid, "Other Goal")
    other_tid = coord.create_task(
        other_gid,
        "Other Task",
        allowed_paths=["**"],
        acceptance_criteria=["Other criteria"],
    )
    coord.record_task_failure(other_tid, "other project failure")

    result = server._handle_resources_read("bridge://audit/events")
    events = json.loads(result["contents"][0]["text"])

    assert all(event.get("task_id") != other_tid for event in events)


def test_mcp_factory_releases_lock_when_coordinator_init_fails(tmp_path, monkeypatch):
    import bridgelib.mcp_server as mcp_module

    db_path = tmp_path / "factory-failure" / "bridge.db"

    def fail_init(*_args, **_kwargs):
        raise RuntimeError("coordinator init failed")

    monkeypatch.setattr(mcp_module, "BridgeCoordinator", fail_init)
    with pytest.raises(RuntimeError, match="coordinator init failed"):
        mcp_module.create_mcp_server(str(db_path))

    assert not (db_path.parent / "bridge.lock").exists()


def test_mcp_claim_rejects_missing_allowed_paths(mcp_env):
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = coord.create_task(
        mcp_env["gid"],
        "Scope required",
        allowed_paths=[],
        acceptance_criteria=["Must be scoped"],
    )
    with pytest.raises(CoordinatorError, match="allowed_path|scope"):
        server._handle_claim_task({"task_id": tid, "agent_id": "scope-agent"})
    assert coord.get_task(tid)["state"] == TaskState.DRAFT.value


def test_mcp_submit_rejects_symbolic_ref_and_unrelated_provenance(mcp_env):
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = mcp_env["tid"]
    claimed = server._handle_claim_task({"task_id": tid, "agent_id": "provenance-agent"})
    assert claimed["status"] == "claimed"
    with pytest.raises(CoordinatorError, match="commit|provenance|SHA|branch"):
        server._handle_submit_task(
            {
                "task_id": tid,
                "agent_id": "provenance-agent",
                "submission_commit": "main",
            }
        )


def test_mcp_submit_requires_current_attempt_validation(mcp_env):
    server = mcp_env["server"]
    coord = mcp_env["coord"]
    tid = mcp_env["tid"]
    claimed = server._handle_claim_task({"task_id": tid, "agent_id": "validation-agent"})
    worktree = claimed["worktree_path"]
    path = os.path.join(worktree, "feature.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("VALUE = 1\n")
    subprocess.run(["git", "add", "feature.py"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=worktree, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=worktree, text=True).strip()
    with pytest.raises(CoordinatorError, match="validation|check|passed"):
        server._handle_submit_task(
            {
                "task_id": tid,
                "agent_id": "validation-agent",
                "submission_commit": commit,
            }
        )
