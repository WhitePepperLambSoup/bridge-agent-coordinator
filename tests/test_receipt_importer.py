"""P0 回归测试 — ReceiptImporter 完整链路"""

import pytest
import tempfile
import os
from datetime import datetime, timezone

from bridgelib.database import init_database
from bridgelib.receipt_importer import ReceiptImporter, ReceiptImportError


@pytest.fixture
def db():
    d = tempfile.mkdtemp()
    db_path = os.path.join(d, "test.db")
    database = init_database(db_path)
    # 创建测试所需的 project/agent/task 记录（满足 receipts 表外键）
    pid = database.create_project(name="test", root_path=d, language="zh-CN")
    aid = database.create_agent(project_id=pid, display_name="TestAgent",
                                 capability_tier="standard", roles=["implementer"])
    tid = database.create_task(project_id=pid, title="Test Task", goal_id=None)
    yield database
    database.close()
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def importer(db):
    return ReceiptImporter(db)


@pytest.fixture
def ids(db):
    """返回测试用的 project/agent/task IDs"""
    proj = db.conn.execute("SELECT id FROM projects LIMIT 1").fetchone()
    agent = db.conn.execute("SELECT id FROM agent_profiles LIMIT 1").fetchone()
    task = db.conn.execute("SELECT id FROM tasks LIMIT 1").fetchone()
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
    """ReceiptImporter 完整导入测试"""

    def test_import_completed_receipt(self, importer, task_dir, ids):
        """正常 COMPLETED 回执可以成功导入"""
        _write_receipt(task_dir, task_id=ids["task_id"], agent_id=ids["agent_id"])
        result = importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        assert result is not None
        assert result["status"] == "completed"
        assert result["content_hash"]

    def test_import_blocked_receipt(self, importer, task_dir, ids):
        """BLOCKED 回执可以成功导入"""
        _write_receipt(task_dir, status="blocked",
                       task_id=ids["task_id"], agent_id=ids["agent_id"])
        result = importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        assert result is not None
        assert result["status"] == "blocked"

    def test_task_id_mismatch_rejected(self, importer, task_dir, ids):
        """task_id 不匹配时拒绝导入"""
        _write_receipt(task_dir, task_id=ids["task_id"])
        with pytest.raises(ReceiptImportError, match="Task ID mismatch"):
            importer.scan_and_import(task_dir, "TASK-002", 1, "lease-abc", ids["agent_id"])

    def test_attempt_mismatch_rejected(self, importer, task_dir, ids):
        """attempt 不匹配时拒绝导入"""
        _write_receipt(task_dir, attempt=1, task_id=ids["task_id"])
        with pytest.raises(ReceiptImportError, match="Attempt mismatch"):
            importer.scan_and_import(task_dir, ids["task_id"], 2, "lease-abc", ids["agent_id"])

    def test_lease_id_mismatch_rejected(self, importer, task_dir, ids):
        """lease_id 不匹配时拒绝导入"""
        _write_receipt(task_dir, lease_id="lease-abc", task_id=ids["task_id"])
        with pytest.raises(ReceiptImportError, match="Lease ID mismatch"):
            importer.scan_and_import(task_dir, ids["task_id"], 1, "lease-xyz", ids["agent_id"])

    def test_duplicate_receipt_ignored(self, importer, task_dir, ids):
        """重复回执被静默忽略"""
        _write_receipt(task_dir, task_id=ids["task_id"], agent_id=ids["agent_id"])
        r1 = importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        assert r1 is not None
        r2 = importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        assert r2 is None

    def test_no_receipt_file_returns_none(self, importer, task_dir, ids):
        """无回执文件时返回 None"""
        result = importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        assert result is None

    def test_invalid_yaml_rejected(self, importer, task_dir, ids):
        """无效 YAML front matter 时拒绝"""
        path = os.path.join(task_dir, "RECEIPT.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# No front matter\n\nJust text.")
        with pytest.raises(ReceiptImportError):
            importer.scan_and_import(task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"])

    def test_receipt_persisted_to_db(self, importer, task_dir, db, ids):
        """导入的回执持久化到数据库"""
        _write_receipt(task_dir, task_id=ids["task_id"], agent_id=ids["agent_id"])
        importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        rows = db.conn.execute("SELECT * FROM receipts").fetchall()
        assert len(rows) >= 1
        assert rows[0]["status"] == "imported"


class TestRescanOnStartup:
    """启动补扫测试"""

    def test_unimported_receipt_detected(self, importer, task_dir):
        """未导入的回执被检测到"""
        _write_receipt(task_dir)
        results = importer.rescan_on_startup([task_dir])
        assert len(results) >= 1
        assert results[0]["status"] == "unimported"

    def test_already_imported_skipped(self, importer, task_dir, ids):
        """已导入的回执在补扫时被跳过"""
        _write_receipt(task_dir, task_id=ids["task_id"], agent_id=ids["agent_id"])
        importer.scan_and_import(
            task_dir, ids["task_id"], 1, "lease-abc", ids["agent_id"]
        )
        results = importer.rescan_on_startup([task_dir])
        unimported = [r for r in results if r.get("status") == "unimported"]
        assert len(unimported) == 0

    def test_rescan_with_task_map_auto_imports(self, importer, task_dir, ids):
        """提供 task_map 时自动导入"""
        _write_receipt(task_dir, task_id=ids["task_id"], agent_id=ids["agent_id"])
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
