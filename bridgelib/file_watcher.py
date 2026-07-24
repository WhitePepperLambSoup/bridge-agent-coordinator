"""Bridge file watcher — polling-based change detection with a stability window.

Design reference: docs/bridge-design/10-adapter-interfaces.md §7 + 05 §Import Algorithm
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
    STABLE = "stable"  # File is stable and ready to read


@dataclass
class WatchedFile:
    """A file being watched."""
    path: str
    last_size: int = -1
    last_mtime: float = -1.0
    last_hash: str = ""
    stable_since: float = 0.0
    notified: bool = False
    event: FileEvent | None = None

    def is_stable(self, stability_ms: float, now: float) -> bool:
        return self.stable_since > 0 and (now - self.stable_since) * 1000 >= stability_ms


class FileWatcher:
    """Poll for file changes and invoke callbacks after files stabilize."""

    def __init__(self, stability_ms: float = 750, poll_interval_ms: float = 100):
        self.stability_ms = stability_ms
        self.poll_interval_ms = poll_interval_ms
        self._files: dict[str, WatchedFile] = {}
        self._callbacks: list = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def watch(self, filepath: str):
        """Start watching a file."""
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
        """Stop watching a file."""
        abs_path = os.path.realpath(filepath)
        with self._lock:
            self._files.pop(abs_path, None)

    def on_stable(self, callback):
        """Register callback(watched_file) to run after a file stabilizes."""
        self._callbacks.append(callback)

    def start(self):
        """Start the background watcher thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("FileWatcher started")

    def stop(self):
        """Stop watching."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("FileWatcher stopped")

    def scan_now(self) -> list[WatchedFile]:
        """Scan all files immediately and return those that are stable."""
        stable = []
        now = time.monotonic()
        with self._lock:
            for wf in list(self._files.values()):
                self._check_file(wf, now)
                if wf.is_stable(self.stability_ms, now) and not wf.notified:
                    wf.event = FileEvent.STABLE
                    wf.notified = True
                    stable.append(wf)
        return stable

    def _poll_loop(self):
        """Run the background polling loop."""
        while self._running:
            try:
                now = time.monotonic()
                stable_files = []
                with self._lock:
                    for wf in list(self._files.values()):
                        changed = self._check_file(wf, now)
                        if changed:
                            wf.event = FileEvent.MODIFIED if wf.last_size >= 0 else FileEvent.CREATED
                        # Trigger callbacks only after stabilization and before notification
                        if wf.is_stable(self.stability_ms, now) and not wf.notified:
                            wf.event = FileEvent.STABLE
                            wf.notified = True
                            stable_files.append(wf)

                # Run callbacks outside the lock
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
        """Check whether a file changed and return True if it did."""
        if not os.path.isfile(wf.path):
            if wf.last_size >= 0:
                wf.event = FileEvent.DELETED
                wf.last_size = -1
                wf.stable_since = 0
                wf.notified = False
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
            wf.stable_since = 0  # Reset the stability timer
            wf.notified = False  # Reset the notification flag
            return True

        # Start the stability timer when size and modification time are unchanged
        if wf.stable_since == 0:
            wf.stable_since = now

        return False


class ReceiptWatcher:
    """Watch .bridge-task/*/RECEIPT.md files for receipt changes."""

    def __init__(self, importer, stability_ms: float = 750):
        self.importer = importer
        self.watcher = FileWatcher(stability_ms=stability_ms)
        self.watcher.on_stable(self._on_receipt_stable)

    def watch_task_dir(self, task_dir: str, task_id: str, attempt: int,
                        lease_id: str, agent_id: str):
        """Watch the receipt file in a task directory."""
        receipt_path = os.path.join(task_dir, "RECEIPT.md")
        self.watcher.watch(receipt_path)
        # Store context for import
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
        """Attempt import after the receipt stabilizes."""
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
