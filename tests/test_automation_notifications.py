from pathlib import Path

from ai_drive.automation.notifications import NotificationQueue


def test_notification_queue_is_bounded_and_persists_pending_text(tmp_path: Path):
    queue = NotificationQueue(tmp_path / "state.sqlite", max_items=2)
    queue.enqueue("one")
    queue.enqueue("two")
    queue.enqueue("three")

    assert [item.text for item in queue.pending()] == ["two", "three"]


def test_notification_queue_discards_items_older_than_one_day(tmp_path: Path):
    now = [100.0]
    queue = NotificationQueue(tmp_path / "state.sqlite", max_age_seconds=10, now=lambda: now[0])
    queue.enqueue("old")
    now[0] = 111.0

    assert queue.pending() == ()
