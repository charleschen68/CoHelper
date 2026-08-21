"""Bounded persistent text-only notification retry queue."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Notification:
    id: int
    text: str


class NotificationQueue:
    def __init__(self, path: Path, *, max_items: int = 100):
        self._connection = sqlite3.connect(path)
        self._max_items = max_items
        with self._connection:
            self._connection.execute("CREATE TABLE IF NOT EXISTS notification_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL)")

    def enqueue(self, text: str) -> None:
        with self._connection:
            self._connection.execute("INSERT INTO notification_queue(text) VALUES (?)", (text,))
            self._connection.execute("DELETE FROM notification_queue WHERE id IN (SELECT id FROM notification_queue ORDER BY id DESC LIMIT -1 OFFSET ?)", (self._max_items,))

    def pending(self) -> tuple[Notification, ...]:
        return tuple(Notification(int(row[0]), str(row[1])) for row in self._connection.execute("SELECT id, text FROM notification_queue ORDER BY id"))

    def acknowledge(self, notification_id: int) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM notification_queue WHERE id = ?", (notification_id,))
