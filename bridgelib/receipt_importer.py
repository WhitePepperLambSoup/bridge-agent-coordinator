"""Bridge 回执导入器 — 稳定窗口、内容哈希去重、重启补扫。

设计参考：docs/bridge-design/05-agent-file-protocol.md §导入算法
"""

import os
import hashlib
import time
import threading
import logging
from datetime import datetime, timezone

from bridgelib.protocol import parse_receipt, validate_receipt, Manifest, ProtocolError

logger = logging.getLogger(__name__)

# 稳定窗口：文件在 750ms 内大小和修改时间不变才读取
STABILITY_WINDOW_MS = 750
MAX_RECEIPT_SIZE_BYTES = 1_048_576  # 1 MB


class ReceiptImportError(Exception):
    pass


class ReceiptImporter:
    """回执导入器 — 监控文件变化，稳定后导入，内容哈希去重。"""

    def __init__(self, db, stability_ms: int = STABILITY_WINDOW_MS):
        self.db = db
        self.stability_ms = stability_ms
        self._imported_hashes: set[str] = set()
        self._lock = threading.Lock()

    def scan_and_import(self, task_dir: str, manifest: Manifest,
                        task_id: str, attempt: int, lease_id: str,
                        agent_id: str) -> dict | None:
        """扫描任务目录中的回执文件，稳定后导入。返回导入结果或 None。"""
        receipt_path = os.path.join(task_dir, "RECEIPT.md")
        if not os.path.isfile(receipt_path):
            return None

        # 稳定窗口等待
        if not self._wait_stable(receipt_path):
            raise ReceiptImportError(
                f"Receipt file {receipt_path} did not stabilize within timeout"
            )

        # 读取并限制大小
        file_size = os.path.getsize(receipt_path)
        if file_size > MAX_RECEIPT_SIZE_BYTES:
            raise ReceiptImportError(
                f"Receipt file too large: {file_size} bytes (max {MAX_RECEIPT_SIZE_BYTES})"
            )

        with open(receipt_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 内容哈希去重
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._lock:
            if content_hash in self._imported_hashes:
                logger.info(f"Duplicate receipt ignored: {content_hash[:16]}")
                return None

            # 检查数据库中的重复
            existing = self.db.conn.execute(
                "SELECT id FROM receipts WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            if existing:
                self._imported_hashes.add(content_hash)
                logger.info(f"Receipt already in database: {content_hash[:16]}")
                return None

        # 解析回执
        try:
            receipt = parse_receipt(content)
        except ProtocolError as e:
            raise ReceiptImportError(f"Failed to parse receipt: {e}")

        # 交叉验证
        errors = validate_receipt(
            receipt, task_id=task_id, attempt=attempt,
            lease_id=lease_id, agent_id=agent_id,
        )
        if errors:
            raise ReceiptImportError(
                f"Receipt validation failed: {'; '.join(errors)}"
            )

        # 持久化到数据库
        receipt_id = f"rcpt-{content_hash[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        self.db.conn.execute(
            """INSERT INTO receipts (id, task_id, attempt_id, agent_id,
               receipt_path, content_hash, status, import_result, created_at)
               VALUES (?,?,?,?,?,?,'imported',?,?)""",
            (receipt_id, task_id, str(attempt), agent_id,
             receipt_path, content_hash,
             f"status={receipt.status.value}", now),
        )
        self.db.conn.commit()

        with self._lock:
            self._imported_hashes.add(content_hash)

        logger.info(f"Receipt imported: {receipt_id} status={receipt.status.value}")
        return {
            "receipt_id": receipt_id,
            "content_hash": content_hash,
            "status": receipt.status.value,
            "submission_commit": receipt.submission_commit,
        }

    def rescan_on_startup(self, task_dirs: list[str]) -> list[dict]:
        """启动时补扫未导入回执。"""
        results = []
        for d in task_dirs:
            receipt_path = os.path.join(d, "RECEIPT.md")
            if not os.path.isfile(receipt_path):
                continue
            try:
                with open(receipt_path, "r", encoding="utf-8") as f:
                    content = f.read()
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                existing = self.db.conn.execute(
                    "SELECT id FROM receipts WHERE content_hash = ?", (content_hash,)
                ).fetchone()
                if not existing:
                    results.append({
                        "directory": d,
                        "content_hash": content_hash[:16],
                        "status": "unimported",
                    })
            except Exception as e:
                results.append({
                    "directory": d,
                    "error": str(e),
                })
        return results

    def _wait_stable(self, filepath: str, timeout_seconds: float = 5.0) -> bool:
        """等待文件稳定（大小和修改时间在窗口期内不变）。"""
        deadline = time.monotonic() + timeout_seconds
        last_size = -1
        last_mtime = -1.0
        stable_since = 0.0

        while time.monotonic() < deadline:
            try:
                stat = os.stat(filepath)
                current_size = stat.st_size
                current_mtime = stat.st_mtime
            except OSError:
                time.sleep(0.1)
                continue

            if current_size == last_size and current_mtime == last_mtime:
                if stable_since == 0.0:
                    stable_since = time.monotonic()
                elif (time.monotonic() - stable_since) * 1000 >= self.stability_ms:
                    return True
            else:
                stable_since = 0.0
                last_size = current_size
                last_mtime = current_mtime

            time.sleep(0.05)

        return False
