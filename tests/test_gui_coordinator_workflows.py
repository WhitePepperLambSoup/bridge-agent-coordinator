import json
import subprocess
import tkinter as tk
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bridgelib import gui
from bridgelib.gui import BridgeApp, ScrollablePage, get_next_task_action
from bridgelib.coordinator import BridgeCoordinator
from bridgelib.database import init_database
from bridgelib.review import ReviewVerdict
from bridgelib.state_machine import TaskState


class _Value:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@pytest.fixture(scope="module")
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def test_scrollable_page_scrolls_in_small_viewport(tk_root):
    root = tk.Toplevel(tk_root)
    try:
        root.geometry("500x320")
        page = ScrollablePage(root, padding=12, min_content_height=900)
        page.pack(fill=tk.BOTH, expand=True)
        marker = tk.Label(page.content, text="bottom")
        marker.place(x=10, y=850)
        root.update()

        before = page.canvas.yview()
        page.scroll_units(8)
        root.update()
        after = page.canvas.yview()

        assert page.scrollbar.winfo_ismapped()
        assert before[0] == 0.0
        assert after[0] > before[0]
    finally:
        root.destroy()


def test_scrollable_page_defers_to_nested_scroll_widgets(tk_root):
    root = tk.Toplevel(tk_root)
    try:
        page = ScrollablePage(root)
        page.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(page.content)
        label = tk.Label(page.content, text="plain")

        assert page.has_nested_scroll_target(text)
        assert not page.has_nested_scroll_target(label)
    finally:
        root.destroy()


def test_bridge_app_supports_small_window_with_scrollable_tabs(tk_root):
    root = tk.Toplevel(tk_root)
    try:
        app = BridgeApp(root)
        root.geometry("820x560")
        root.update()

        assert root.minsize() == (820, 560)
        assert len(app.scroll_pages) == 4
        assert all(page.scrollbar.winfo_ismapped() for page in app.scroll_pages if page.winfo_ismapped())
        assert app.scroll_pages[0].can_scroll()
    finally:
        root.destroy()


def test_mousewheel_scrolls_the_page_under_the_pointer(tk_root):
    root = tk.Toplevel(tk_root)
    try:
        app = BridgeApp(root)
        root.geometry("820x560+100+100")
        root.update()
        page = app.scroll_pages[0]
        page.canvas.yview_moveto(0)
        page.content.event_generate("<Motion>", warp=True, x=100, y=100)
        root.update()
        before = page.canvas.yview()[0]

        page.content.event_generate("<MouseWheel>", delta=-120)
        root.update()

        assert page.canvas.yview()[0] > before
    finally:
        root.destroy()


@pytest.mark.parametrize(
    ("state", "key", "label"),
    [
        ("draft", "prepare", "准备任务"),
        ("planning", "prepare", "完成准备"),
        ("ready", "assign", "分配并开始"),
        ("assigned", "start", "创建工作区"),
        ("in_progress", "submit", "检测 Agent 提交"),
        ("submitted", "validate", "运行验证"),
        ("validating", "review", "准备审查"),
        ("approved", "merge", "安全合并"),
        ("merge_queued", "merge", "继续合并"),
        ("merging", "merge", "完成合并"),
        ("done", "done", "任务已完成"),
        ("revision_required", "retry", "开始修改"),
        ("blocked", "retry", "解除阻塞"),
        ("conflict", "retry", "处理冲突"),
        ("stale", "retry", "重新开始"),
        ("escalated", "retry", "重新分配"),
        ("cancelled", "done", "任务已取消"),
    ],
)
def test_each_task_state_has_one_clear_next_action(state, key, label):
    action = get_next_task_action(state)
    assert action.key == key
    assert action.label == label


def test_pending_review_changes_validating_action_label():
    action = get_next_task_action("validating", has_pending_review=True)
    assert action.key == "review"
    assert action.label == "记录审查结果"


def test_gui_start_attempt_creates_handoff_and_uses_two_hour_lease(monkeypatch):
    calls = []
    task = {
        "id": "TASK-001",
        "project_id": "project-1",
        "owner_agent_id": "agent-1",
        "reviewer_agent_id": "reviewer-1",
        "title": "Implement feature",
        "goal": "Ship it",
        "risk": "medium",
        "complexity": "medium",
        "allowed_paths_json": '["src/**"]',
        "forbidden_paths_json": '[".bridge/**"]',
        "acceptance_criteria_json": '["works"]',
        "required_checks_json": '["unit-tests"]',
    }

    class DB:
        def update_lease_status(self, *args):
            calls.append(("lease_status", args))

    class Coordinator:
        db = DB()
        leases = SimpleNamespace(revoke=lambda *args, **kwargs: None)

        def acquire_lease(self, task_id, owner_id, **kwargs):
            calls.append(("lease", kwargs))
            return SimpleNamespace(
                lease_id="lease-1", expires_at=SimpleNamespace(isoformat=lambda: "later")
            )

        def create_attempt(self, task_id, owner_id, lease_id):
            return "attempt-1"

        def get_current_attempt(self, task_id):
            return {"id": "attempt-1", "attempt_number": 2, "lease_id": "lease-1"}

        def check_git_repo(self, project_id):
            return {"head_commit": "abc123"}

        def create_worktree(self, task_id, owner_id, **kwargs):
            calls.append(("worktree", kwargs))
            return {
                "worktree_path": "C:/tmp/worktree",
                "base_commit": kwargs["base_commit"],
                "branch": "bridge/agent-1/TASK-001/a2",
            }

        def transition_task(self, task_id, state, **kwargs):
            calls.append(("transition", state, kwargs))

        def get_worktree(self, task_id):
            return {"worktree_path": "C:/tmp/worktree"}

        def get_agent(self, agent_id):
            return {"display_name": "Reasonix"}

    app = BridgeApp.__new__(BridgeApp)
    app.root = None
    app.coordinator = Coordinator()
    app._selected_task = lambda: task
    app._generate_handoff_package = lambda *args: "C:/tmp/worktree.bridge-task-a2"
    app._build_implementer_prompt = lambda task: "prompt"
    app._copy_text = lambda text, message: calls.append(("copy", text))
    app._log = lambda message: calls.append(("log", message))
    app._refresh_coordinator = lambda: None
    app._select_task = lambda task_id: None
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args: pytest.fail(str(args)))

    app._gui_start_attempt(confirmed=True)

    assert ("lease", {"ttl_seconds": 7200}) in calls
    assert ("worktree", {"attempt": 2, "base_commit": "abc123", "confirmed": True}) in calls
    assert any(call[0] == "transition" and call[1] == TaskState.IN_PROGRESS for call in calls)
    assert ("copy", "prompt") in calls


def test_validation_creates_review_but_does_not_self_approve(tmp_path, monkeypatch):
    calls = []
    (tmp_path / "RECEIPT.md").write_text("receipt", encoding="utf-8")
    task = {
        "id": "TASK-001",
        "reviewer_agent_id": "reviewer-1",
        "required_checks_json": '["unit-tests"]',
        "acceptance_criteria_json": '["works"]',
        "state": TaskState.SUBMITTED.value,
        "title": "Feature",
        "goal": "Ship it",
        "risk": "medium",
        "complexity": "medium",
    }

    class DB:
        def list_pending_reviews(self, project_id=None):
            return []

    class Coordinator:
        db = DB()

        def transition_task(self, task_id, state, **kwargs):
            calls.append(("transition", state))

        def run_validation(self, task_id, checks):
            calls.append(("validation", checks))
            return [{"check_id": "unit-tests", "status": "passed"}]

        def validate_artifacts(self, task_id, path):
            return {"valid": True}

        def submit_review(self, task_id, reviewer_id, package):
            calls.append(("review_requested", reviewer_id, package))
            return "review-1"

        def get_agent(self, agent_id):
            return {"display_name": "Codex"}

        def get_task(self, task_id):
            return task

    app = BridgeApp.__new__(BridgeApp)
    app.coordinator = Coordinator()
    app.db = app.coordinator.db
    app.current_project_id = _Value("project-1")
    app._selected_task = lambda: task
    app._task_package_dir = lambda task_id: str(tmp_path)
    app._load_artifacts = lambda task_id: {
        "submission_commit": "abc123", "changed_files": [{"path": "src/app.py"}]
    }
    app._build_review_prompt = lambda task: "review prompt"
    app._copy_text = lambda text, message: calls.append(("copy", text))
    app._log = lambda message: calls.append(("log", message))
    app._refresh_coordinator = lambda: None
    app._select_task = lambda task_id: None
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args: errors.append(args))

    app._gui_validate_and_review()

    assert not errors
    assert ("validation", [{"check_id": "unit-tests"}]) in calls
    transitions = [call[1] for call in calls if call[0] == "transition"]
    assert transitions == [TaskState.VALIDATING]
    assert any(call[0] == "review_requested" for call in calls)
    assert not any(call[0] == "review_complete" for call in calls)


def test_record_review_uses_supported_api_without_confirmation_keyword(monkeypatch):
    calls = []
    task = {"id": "TASK-001", "reviewer_agent_id": "reviewer-1"}

    class DB:
        def list_pending_reviews(self, project_id=None):
            return [{"id": "review-1", "task_id": "TASK-001"}]

    class Coordinator:
        db = DB()

        def complete_review(self, *args, **kwargs):
            calls.append((args, kwargs))

    app = BridgeApp.__new__(BridgeApp)
    app.root = None
    app.coordinator = Coordinator()
    app.db = app.coordinator.db
    app.current_project_id = _Value("project-1")
    app._selected_task = lambda: task
    app._transition_with_confirmation = lambda *args, **kwargs: True
    app._log = lambda message: None
    app._refresh_coordinator = lambda: None
    app._select_task = lambda task_id: None
    monkeypatch.setattr(gui.messagebox, "askyesnocancel", lambda *args, **kwargs: True)
    from tkinter import simpledialog
    monkeypatch.setattr(simpledialog, "askstring", lambda *args, **kwargs: "ok")

    app._record_review_result()

    assert calls
    args, kwargs = calls[0]
    assert args[:2] == ("review-1", ReviewVerdict.APPROVED)
    assert kwargs["reviewer_agent_id"] == "reviewer-1"
    assert "confirmed" not in kwargs


def test_gui_applies_user_selected_project_policies(monkeypatch):
    calls = []

    class DB:
        def update_project(self, *args, **kwargs):
            calls.append(("project", args, kwargs))

    class Coordinator:
        def set_safety_mode(self, *args):
            calls.append(("safety", args))

    app = BridgeApp.__new__(BridgeApp)
    app.coordinator = Coordinator()
    app.db = DB()
    app.current_project_id = _Value("project-1")
    app.progression_policy = _Value("manual")
    app.safety_mode = _Value("strict")
    app._log = lambda message: None
    errors = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *args: None)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args: errors.append(args))

    app._apply_project_policies()

    assert not errors
    assert ("safety", ("project-1", "strict")) in calls
    assert any(
        call[0] == "project" and call[2]["progression_policy"] == "manual"
        for call in calls
    )


def test_simplified_gui_workflow_reaches_done_with_real_git(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args, cwd=repo):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "bridge@example.test")
    git("config", "user.name", "Bridge Test")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "value.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (repo / "tests" / "test_value.py").write_text(
        "from src.value import value\n\ndef test_value():\n    assert value() == 1\n",
        encoding="utf-8",
    )
    git("add", ".")
    git("commit", "-m", "initial")

    database = init_database(str(tmp_path / "bridge.db"))
    coordinator = BridgeCoordinator(database=database)
    project_id = coordinator.init_project("Demo", str(repo))
    owner_id = coordinator.add_agent(
        project_id, "Reasonix", roles=["implementer"], can_run_validation=True
    )
    reviewer_id = coordinator.add_agent(
        project_id, "Codex", roles=["planner", "reviewer"], can_review=True
    )
    goal_id = coordinator.create_goal(project_id, "Change value")
    task_id = coordinator.create_task(
        goal_id,
        "Change value",
        goal="Return two",
        allowed_paths=["src/**", "tests/**"],
        forbidden_paths=[".bridge/**", ".git/**"],
        acceptance_criteria=["value returns two"],
        required_checks=["unit-tests"],
        risk="medium",
    )
    coordinator.transition_task(task_id, TaskState.PLANNING)
    coordinator.transition_task(task_id, TaskState.READY, confirmed=True)
    coordinator.assign_task(task_id, owner_id, reviewer_id, confirmed=True)

    app = BridgeApp.__new__(BridgeApp)
    app.root = None
    app.coordinator = coordinator
    app.db = database
    app.current_project_id = _Value(project_id)
    app._selected_task = lambda: coordinator.get_task(task_id)
    app._copy_text = lambda *args: None
    app._log = lambda *args: None
    app._refresh_coordinator = lambda: None
    app._select_task = lambda *args: None
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    monkeypatch.setattr(gui.messagebox, "askyesnocancel", lambda *args, **kwargs: True)
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args: errors.append(args))
    from tkinter import simpledialog
    monkeypatch.setattr(simpledialog, "askstring", lambda *args, **kwargs: "approved")

    app._gui_start_attempt(confirmed=True)
    workspace = coordinator.get_worktree(task_id)
    worktree_path = workspace["worktree_path"]
    package_dir = app._task_package_dir(task_id)
    attempt = coordinator.get_current_attempt(task_id)

    worktree = type(repo)(worktree_path)
    (worktree / "src" / "value.py").write_text(
        "def value():\n    return 2\n", encoding="utf-8"
    )
    (worktree / "tests" / "test_value.py").write_text(
        "from src.value import value\n\ndef test_value():\n    assert value() == 2\n",
        encoding="utf-8",
    )
    git("add", "src/value.py", "tests/test_value.py", cwd=worktree)
    git("commit", "-m", "change value", cwd=worktree)
    submission_commit = git("rev-parse", "HEAD", cwd=worktree)

    receipt = f"""---
protocol_version: 1
task_id: {task_id}
attempt: {attempt['attempt_number']}
lease_id: {attempt['lease_id']}
agent_id: {owner_id}
status: completed
submission_commit: '{submission_commit}'
completed_at: '{datetime.now(timezone.utc).isoformat()}'
---

# Completed

Implemented and tested the requested change.
"""
    package = type(repo)(package_dir)
    (package / "RECEIPT.md").write_text(receipt, encoding="utf-8")
    artifacts = {
        "protocol_version": 1,
        "task_id": task_id,
        "attempt": attempt["attempt_number"],
        "agent_id": owner_id,
        "base_commit": workspace["base_commit"],
        "submission_commit": submission_commit,
        "changed_files": [{"path": "src/value.py"}, {"path": "tests/test_value.py"}],
        "checks": [{"id": "unit-tests"}],
        "evidence_files": [],
        "new_dependencies": [],
        "migrations": [],
        "known_failures": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (package / "ARTIFACTS.json").write_text(
        json.dumps(artifacts), encoding="utf-8"
    )

    app._import_submission()
    app._gui_validate_and_review()
    app._record_review_result()
    app._merge_selected_task()

    assert not errors
    assert coordinator.get_task(task_id)["state"] == TaskState.DONE.value
    assert "return 2" in (repo / "src" / "value.py").read_text(encoding="utf-8")
    assert coordinator.get_worktree(task_id)["status"] == "cleaned"
    database.close()
