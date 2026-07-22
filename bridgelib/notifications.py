"""Bridge 通知系统 — InApp/Desktop/Log 三种适配器。

设计参考：docs/bridge-design/10-adapter-interfaces.md §5
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
    """通知管理器 — 支持多个通知器。"""

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
    """应用内通知器"""
    def notify(self, notification: Notification):
        pass  # GUI 层负责显示


class LogNotifier:
    """日志通知器"""
    def notify(self, notification: Notification):
        pass  # 写入日志文件
