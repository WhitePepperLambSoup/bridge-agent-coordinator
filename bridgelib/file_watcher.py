"""Bridge 文件监听服务 — 轮询式文件变化检测与稳定窗口。

设计参考：docs/bridge-design/10-adapter-interfaces.md §7 + 05 §导入算法
"""

import os
import hashlib
import threading
import time
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class FileEvent(Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    STABLE = "stable"  # 文件已稳定可读取


@dataclass
class WatchedFile:
    """被监听的文件"""
    path: str
    last_size: int = -1
    last_mtime: float = -1.0
    last_hash: str = ""
    stable_since: float = 0.0
    event: FileEvent | None = None

    def is_stable(self, stability_ms: float, now: float) -> bool:
        return self.stable_since > 0 and (now - self.stable_since) * 1000 >= stability_ms


class FileWatcher:
    """文件监听器 — 轮询检测变化，稳定后触发回调。"""

    def __init__(self, stability_ms: float = 750, poll_interval_ms: float = 100):
        self.stability_ms = stability_ms
        self.poll_interval_ms = poll_interval_ms
        self._files: dict[str, WatchedFile] = {}
        self._callbacks: list = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def watch(self, filepath: str):
        """开始监听文件。"""
        abs_path = os.path.realpath(filepath)
        with self._lock:
            if abs_path not in self._files:
                wf = WatchedFile(path=abs_path)
                if os.path.isfile(abs_path):
                    stat = os.stat(abs_path)
                    wf.last_size = stat.st_size
                    wf.last_mtime = stat.st_mtime
                self._files[abs_path] = wf

    def unwatch(self, filepath: str):
        """停止监听文件。"""
        abs_path = os.path.realpath(filepath)
        with self._lock:
            self._files.pop(abs_path, None)

    def on_stable(self, callback):
        """注册稳定回调。callback(watched_file) 在文件稳定后调用。"""
        self._callbacks.append(callback)

    def start(self):
        """启动后台监听线程。"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("FileWatcher started")

    def stop(self):
        """停止监听。"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("FileWatcher stopped")

    def scan_now(self) -> list[WatchedFile]:
        """立即扫描所有文件，返回已稳定的文件列表。"""
        stable = []
        now = time.monotonic()
        with self._lock:
            for wf in list(self._files.values()):
                if self._check_file(wf, now) and wf.is_stable(self.stability_ms, now):
                    wf.event = FileEvent.STABLE
                    stable.append(wf)
        return stable

    def _poll_loop(self):
        """后台轮询循环。"""
        while self._running:
            try:
                now = time.monotonic()
                stable_files = []
                with self._lock:
                    for wf in list(self._files.values()):
                        changed = self._check_file(wf, now)
                        if changed:
                            wf.event = FileEvent.MODIFIED if wf.last_size >= 0 else FileEvent.CREATED
                        if wf.is_stable(self.stability_ms, now):
                            wf.event = FileEvent.STABLE
                            stable_files.append(wf)

                # 回调在锁外执行
                for wf in stable_files:
                    for cb in self._callbacks:
                        try:
                            cb(wf)
                        except Exception:
                            logger.exception(f"Callback error for {wf.path}")

                time.sleep(self.poll_interval_ms / 1000)
            except Exception:
                logger.exception("FileWatcher poll error")
                time.sleep(1.0)

    def _check_file(self, wf: WatchedFile, now: float) -> bool:
        """检查文件是否变化，返回 True 如果有变化。"""
        if not os.path.isfile(wf.path):
            if wf.last_size >= 0:
                wf.event = FileEvent.DELETED
                wf.last_size = -1
                wf.stable_since = 0
                return True
            return False

        try:
            stat = os.stat(wf.path)
            current_size = stat.st_size
            current_mtime = stat.st_mtime
        except OSError:
            return False

        if current_size != wf.last_size or current_mtime != wf.last_mtime:
            wf.last_size = current_size
            wf.last_mtime = current_mtime
            wf.stable_since = 0  # 重置稳定计时器
            return True

        # 大小和时间都不变 → 开始计时稳定
        if wf.stable_since == 0:
            wf.stable_since = now

        return False


class ReceiptWatcher:
    """回执文件专用监听器 — 检测 .bridge-task/*/RECEIPT.md 变化。"""

    def __init__(self, importer, stability_ms: float = 750):
        self.importer = importer
        self.watcher = FileWatcher(stability_ms=stability_ms)
        self.watcher.on_stable(self._on_receipt_stable)

    def watch_task_dir(self, task_dir: str, task_id: str, attempt: int,
                        lease_id: str, agent_id: str):
        """监听任务目录中的回执文件。"""
        receipt_path = os.path.join(task_dir, "RECEIPT.md")
        self.watcher.watch(receipt_path)
        # 存储上下文以便导入
        if not hasattr(self, '_task_contexts'):
            self._task_contexts = {}
        abs_path = os.path.realpath(receipt_path)
        self._task_contexts[abs_path] = {
            "task_dir": task_dir,
            "task_id": task_id,
            "attempt": attempt,
            "lease_id": lease_id,
            "agent_id": agent_id,
        }

    def start(self):
        self.watcher.start()

    def stop(self):
        self.watcher.stop()

    def _on_receipt_stable(self, wf: WatchedFile):
        """回执稳定后尝试导入。"""
        ctx = getattr(self, '_task_contexts', {}).get(wf.path, {})
        if not ctx:
            return
        try:
            result = self.importer.scan_and_import(
                ctx["task_dir"], ctx["task_id"], ctx["attempt"],
                ctx["lease_id"], ctx["agent_id"],
            )
            if result:
                logger.info(f"Receipt auto-imported: {result.get('receipt_id')}")
        except Exception as e:
            logger.error(f"Receipt auto-import failed: {e}")
