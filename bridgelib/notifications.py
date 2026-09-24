"""Bridge notification system with in-app, desktop, and log adapters.

Design reference: docs/bridge-design/10-adapter-interfaces.md, section 5
"""

import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum


class NotificationLevel(Enum):
    INFO = "info"
    ACTION = "action"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Notification:
    level: NotificationLevel
    title: str
    message: str
    task_id: str = ""
    action: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "level": self.level.value,
            "title": self.title,
            "message": self.message,
            "task_id": self.task_id,
            "action": self.action,
            "created_at": self.created_at,
        }


class NotificationManager:
    """Notification manager supporting multiple notifiers."""

    def __init__(self, max_notifications: int = 200):
        self.max_notifications = max_notifications
        self._notifications: list[Notification] = []
        self._notifiers: list = []
        self._lock = threading.Lock()
        self._read_count: int = 0

    def register(self, notifier):
        self._notifiers.append(notifier)

    def send(self, level: NotificationLevel, title: str, message: str,
             task_id: str = "", action: str = "") -> Notification:
        n = Notification(level=level, title=title, message=message,
                        task_id=task_id, action=action)
        with self._lock:
            self._notifications.append(n)
            while len(self._notifications) > self.max_notifications:
                self._notifications.pop(0)

        for notifier in self._notifiers:
            try:
                notifier.notify(n)
            except Exception:
                pass

        return n

    def list_all(self) -> list[Notification]:
        with self._lock:
            return list(self._notifications)

    def list_by_level(self, level: NotificationLevel) -> list[Notification]:
        return [n for n in self._notifications if n.level == level]

    def list_by_task(self, task_id: str) -> list[Notification]:
        return [n for n in self._notifications if n.task_id == task_id]

    def unread_count(self) -> int:
        return len(self._notifications) - self._read_count

    def mark_all_read(self):
        self._read_count = len(self._notifications)


class InAppNotifier:
    """Queue in-app notifications for GUI consumption."""
    def __init__(self):
        self._queue: list[Notification] = []
        self._lock = threading.Lock()

    def notify(self, notification: Notification):
        with self._lock:
            self._queue.append(notification)
            if len(self._queue) > 100:
                self._queue.pop(0)

    def drain(self) -> list[Notification]:
        """Return and clear all pending notifications."""
        with self._lock:
            msgs = list(self._queue)
            self._queue.clear()
            return msgs


class LogNotifier:
    """Write notifications to a log file."""
    def __init__(self, log_path: str = ""):
        self.log_path = log_path

    def notify(self, notification: Notification):
        import logging
        logger = logging.getLogger("bridge.notifications")
        level_map = {
            NotificationLevel.INFO: logging.INFO,
            NotificationLevel.ACTION: logging.INFO,
            NotificationLevel.WARNING: logging.WARNING,
            NotificationLevel.HIGH: logging.WARNING,
            NotificationLevel.CRITICAL: logging.ERROR,
        }
        log_level = level_map.get(notification.level, logging.INFO)
        logger.log(log_level,
                   f"[{notification.level.value}] {notification.title}: "
                   f"{notification.message} (task={notification.task_id})")
