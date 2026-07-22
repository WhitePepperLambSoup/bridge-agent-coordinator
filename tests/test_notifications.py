"""Phase 4.3 测试 — 通知系统"""

import pytest
from bridgelib.notifications import (
    Notification,
    NotificationLevel,
    NotificationManager,
    InAppNotifier,
    LogNotifier,
)


class TestNotification:
    def test_create_notification(self):
        n = Notification(
            level=NotificationLevel.INFO,
            title="Task assigned",
            message="TASK-001 has been assigned to Reasonix.",
            task_id="TASK-001",
        )
        assert n.level == NotificationLevel.INFO
        assert n.task_id == "TASK-001"

    def test_notification_to_dict(self):
        n = Notification(
            level=NotificationLevel.WARNING,
            title="Budget 80%",
            message="Token budget at 80% for TASK-042.",
            task_id="TASK-042",
            action="review_budget",
        )
        d = n.to_dict()
        assert d["level"] == "warning"
        assert d["action"] == "review_budget"


class TestNotificationLevels:
    def test_five_levels(self):
        assert NotificationLevel.INFO.value == "info"
        assert NotificationLevel.ACTION.value == "action"
        assert NotificationLevel.WARNING.value == "warning"
        assert NotificationLevel.HIGH.value == "high"
        assert NotificationLevel.CRITICAL.value == "critical"


class TestNotificationManager:
    @pytest.fixture
    def manager(self):
        return NotificationManager()

    def test_send_and_list(self, manager):
        manager.send(NotificationLevel.INFO, "Test", "Hello", task_id="TASK-001")
        manager.send(NotificationLevel.WARNING, "Warning", "Budget low", task_id="TASK-002")
        notifications = manager.list_all()
        assert len(notifications) == 2

    def test_list_by_level(self, manager):
        manager.send(NotificationLevel.INFO, "Info", "x")
        manager.send(NotificationLevel.CRITICAL, "Critical", "y")
        critical = manager.list_by_level(NotificationLevel.CRITICAL)
        assert len(critical) == 1

    def test_list_by_task(self, manager):
        manager.send(NotificationLevel.INFO, "T1", "x", task_id="TASK-001")
        manager.send(NotificationLevel.INFO, "T2", "y", task_id="TASK-001")
        manager.send(NotificationLevel.INFO, "T3", "z", task_id="TASK-002")
        task1 = manager.list_by_task("TASK-001")
        assert len(task1) == 2

    def test_unread_count(self, manager):
        manager.send(NotificationLevel.INFO, "A", "x")
        manager.send(NotificationLevel.INFO, "B", "y")
        assert manager.unread_count() == 2
        manager.mark_all_read()
        assert manager.unread_count() == 0

    def test_max_notifications(self, manager):
        """超过最大数量时自动清理旧通知"""
        manager.max_notifications = 5
        for i in range(10):
            manager.send(NotificationLevel.INFO, f"T{i}", f"msg {i}")
        assert len(manager.list_all()) <= 5
