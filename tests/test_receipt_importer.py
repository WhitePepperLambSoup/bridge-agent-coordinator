"""P0 regression tests for the complete ReceiptImporter workflow."""

import pytest
import tempfile
import os
import subprocess
from datetime import datetime, timezone

from bridgelib.database import init_database
from bridgelib.receipt_importer import ReceiptImporter, ReceiptImportError


@pytest.fixture
def db():
    d = tempfile.mkdtemp()
    # Initialize Git; fail-closed handling requires a verifiable completed-receipt commit.
    subprocess.run(["git", "init"], cwd=d, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, capture_output=True)
    with open(os.path.join(d, "README.md"), "w") as f:
        f.write("# Test\n")
    subprocess.run(["git", "add", "."], cwd=d, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=d, capture_output=True)
    # Get the real commit hash.
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, capture_output=True, text=True)
    real_commit = r.stdout.strip()[:7]

    db_path = os.path.join(d, "test.db")
    database = init_database(db_path)
    pid = database.create_project(name="test", root_path=d, language="zh-CN")
    aid = database.create_agent(project_id=pid, display_name="TestAgent",
                                 capability_tier="standard", roles=["implementer"])
    tid = database.create_task(project_id=pid, title="Test Task", goal_id=None,
                                owner_agent_id=aid)
    database.create_lease(
        lease_id="lease-abc", task_id=tid, agent_id=aid,
        resource_type="task", resource_path="src/**", ttl_seconds=900,
    )
    yield database, real_commit
    database.close()
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def importer(db):
    return ReceiptImporter(db[0])


@pytest.fixture
def ids(db):
    """Return project, agent, and task IDs for tests."""
    database = db[0]
    proj = database.conn.execute("SELECT id FROM projects LIMIT 1").fetchone()
    agent = database.conn.execute("SELECT id FROM agent_profiles LIMIT 1").fetchone()
    task = database.conn.execute("SELECT id FROM tasks LIMIT 1").fetchone()
    return {
        "project_id": proj["id"] if proj else "p1",
        "agent_id": agent["id"] if agent else "agent-001",
        "task_id": task["id"] if task else "TASK-001",
    }


@pytest.fixture
def task_dir():
    d = tempfile.mkdtemp()
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def _write_receipt(task_dir, status="completed", task_id="TASK-001", attempt=1,
                   lease_id="lease-abc", agent_id="agent-001",
                   submission_commit="abc1234"):
    content = f"""---
protocol_version: 1
task_id: {task_id}
attempt: {attempt}
lease_id: {lease_id}
agent_id: {agent_id}
status: {status}
submission_commit: {submission_commit}
completed_at: 2026-01-01T00:00:00Z
---

# Result

Task completed successfully.
"""
    path = os.path.join(task_dir, "RECEIPT.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestReceiptImport:
    """Test the complete ReceiptImporter import workflow."""

    def test_import_completed_receipt(self, importer, task_dir, ids, db):
        """Import a valid COMPLETED receipt using a real Git commit."""
        real_commit = db[1]
        _write_receipt(task_dir, task_id=ids["task_id"], agent_id=ids["agent_id"],
                       submission_commit=real_commit)
        result = importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        assert result is not None
        assert result["status"] == "completed"
        assert result["content_hash"]

    def test_import_blocked_receipt(self, importer, task_dir, ids):
        """Import a BLOCKED receipt without a submission_commit."""
        _write_receipt(task_dir, status="blocked", submission_commit="",
                       task_id=ids["task_id"], agent_id=ids["agent_id"])
        result = importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        assert result is not None
        assert result["status"] == "blocked"

    def test_task_id_mismatch_rejected(self, importer, task_dir, ids):
        """Reject an import when task_id does not match."""
        _write_receipt(task_dir, status="progress", submission_commit="",
                       task_id=ids["task_id"], agent_id=ids["agent_id"])
        with pytest.raises(ReceiptImportError, match="Task ID mismatch"):
            importer.scan_and_import(task_dir, "TASK-002", 1, "lease-abc", ids["agent_id"])

    def test_attempt_mismatch_rejected(self, importer, task_dir, ids):
        """Reject an import when attempt does not match."""
        _write_receipt(task_dir, status="progress", submission_commit="",
                       attempt=1, task_id=ids["task_id"], agent_id=ids["agent_id"])
        with pytest.raises(ReceiptImportError, match="Attempt mismatch"):
            importer.scan_and_import(task_dir, ids["task_id"], 2, "lease-abc", ids["agent_id"])

    def test_lease_id_mismatch_rejected(self, importer, task_dir, ids):
        """Reject an import when lease_id does not match."""
        _write_receipt(task_dir, status="progress", submission_commit="",
                       lease_id="lease-abc", task_id=ids["task_id"], agent_id=ids["agent_id"])
        with pytest.raises(ReceiptImportError, match="Lease ID mismatch"):
            importer.scan_and_import(task_dir, ids["task_id"], 1, "lease-xyz", ids["agent_id"])

    def test_duplicate_receipt_ignored(self, importer, task_dir, ids, db):
        """Silently ignore a duplicate receipt."""
        real_commit = db[1]
        _write_receipt(task_dir, task_id=ids["task_id"], agent_id=ids["agent_id"],
                       submission_commit=real_commit)
        r1 = importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        assert r1 is not None
        r2 = importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        assert r2 is None

    def test_no_receipt_file_returns_none(self, importer, task_dir, ids):
        """Return None when no receipt file exists."""
        result = importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        assert result is None

    def test_invalid_yaml_rejected(self, importer, task_dir, ids):
        """Reject invalid YAML front matter."""
        path = os.path.join(task_dir, "RECEIPT.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# No front matter\n\nJust text.")
        with pytest.raises(ReceiptImportError):
            importer.scan_and_import(task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"])

    def test_receipt_persisted_to_db(self, importer, task_dir, db, ids):
        """Persist an imported receipt to the database."""
        real_commit = db[1]
        _write_receipt(task_dir, task_id=ids["task_id"], agent_id=ids["agent_id"],
                       submission_commit=real_commit)
        importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        rows = db[0].conn.execute("SELECT * FROM receipts").fetchall()
        assert len(rows) >= 1
        assert rows[0]["status"] == "imported"


class TestRescanOnStartup:
    """Test supplemental scanning at startup."""

    def test_unimported_receipt_detected(self, importer, task_dir):
        """Detect an unimported receipt."""
        _write_receipt(task_dir, status="progress", submission_commit="")
        results = importer.rescan_on_startup([task_dir])
        assert len(results) >= 1
        assert results[0]["status"] == "unimported"

    def test_already_imported_skipped(self, importer, task_dir, ids, db):
        """Skip an imported receipt during a supplemental scan."""
        real_commit = db[1]
        _write_receipt(task_dir, task_id=ids["task_id"], agent_id=ids["agent_id"],
                       submission_commit=real_commit)
        importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        results = importer.rescan_on_startup([task_dir])
        unimported = [r for r in results if r.get("status") == "unimported"]
        assert len(unimported) == 0

    def test_rescan_with_task_map_auto_imports(self, importer, task_dir, ids, db):
        """Import automatically when task_map is provided."""
        real_commit = db[1]
        _write_receipt(task_dir, task_id=ids["task_id"], agent_id=ids["agent_id"],
                       submission_commit=real_commit)
        results = importer.rescan_on_startup(
            [task_dir],
            task_map={
                task_dir: {
                    "task_id": ids["task_id"],
                    "attempt": 1,
                    "lease_id": "lease-abc",
                    "agent_id": ids["agent_id"],
                }
            },
        )
        imported = [r for r in results if r.get("status") == "imported"]
        assert len(imported) == 1
