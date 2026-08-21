"""Bounded persistent text-only notification retry queue."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Notification:
    id: int
    text: str


class NotificationQueue:
    def __init__(self, path: Path, *, max_items: int = 100, max_age_seconds: float = 86_400, now=time.time):
        self._connection = sqlite3.connect(path, timeout=5)
        self._max_items = max_items
        self._max_age_seconds = max_age_seconds
        self._now = now
        with self._connection:
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("CREATE TABLE IF NOT EXISTS notification_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created_at REAL NOT NULL)")
            columns = {str(row[1]) for row in self._connection.execute("PRAGMA table_info(notification_queue)")}
            if "created_at" not in columns:
                self._connection.execute("ALTER TABLE notification_queue ADD COLUMN created_at REAL")
                self._connection.execute("UPDATE notification_queue SET created_at = ? WHERE created_at IS NULL", (self._now(),))

    def enqueue(self, text: str) -> None:
        if not text or len(text) > 4_096:
            raise ValueError("notification text must be between 1 and 4096 characters")
        with self._connection:
            self._discard_expired()
            self._connection.execute("INSERT INTO notification_queue(text, created_at) VALUES (?, ?)", (text, self._now()))
            self._connection.execute("DELETE FROM notification_queue WHERE id IN (SELECT id FROM notification_queue ORDER BY id DESC LIMIT -1 OFFSET ?)", (self._max_items,))

    def pending(self) -> tuple[Notification, ...]:
        with self._connection:
            self._discard_expired()
            return tuple(Notification(int(row[0]), str(row[1])) for row in self._connection.execute("SELECT id, text FROM notification_queue ORDER BY id"))

    def acknowledge(self, notification_id: int) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM notification_queue WHERE id = ?", (notification_id,))

    def close(self) -> None:
        self._connection.close()

    def _discard_expired(self) -> None:
        self._connection.execute("DELETE FROM notification_queue WHERE created_at < ?", (self._now() - self._max_age_seconds,))
