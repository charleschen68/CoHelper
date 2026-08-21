"""Durable rule state for at-most-once unattended actions."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class RunState(StrEnum):
    IDLE = "idle"
    EXECUTING = "executing"
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class RuleSnapshot:
    rule_id: str
    run_state: RunState
    triggered: bool
    consecutive_absent: int


class AutomationStateStore:
    """SQLite-backed state that is safe to inspect from control and scan threads."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rule_state (
                    rule_id TEXT PRIMARY KEY,
                    run_state TEXT NOT NULL,
                    triggered INTEGER NOT NULL,
                    consecutive_absent INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # A process crash cannot prove whether a click/type reached macOS.
            self._connection.execute(
                "UPDATE rule_state SET run_state = ? WHERE run_state = ?",
                (RunState.UNKNOWN, RunState.EXECUTING),
            )

    def snapshot(self, rule_id: str) -> RuleSnapshot:
        with self._lock:
            row = self._connection.execute(
                "SELECT run_state, triggered, consecutive_absent FROM rule_state WHERE rule_id = ?",
                (rule_id,),
            ).fetchone()
        if row is None:
            return RuleSnapshot(rule_id, RunState.IDLE, False, 0)
        return RuleSnapshot(rule_id, RunState(row[0]), bool(row[1]), int(row[2]))

    def observe_present(self, rule_id: str) -> None:
        self._upsert(rule_id, run_state=None, triggered=None, consecutive_absent=0)

    def observe_absent(self, rule_id: str) -> RuleSnapshot:
        with self._lock, self._connection:
            current = self.snapshot(rule_id)
            absent = current.consecutive_absent + 1
            if absent >= 2:
                state, triggered = RunState.IDLE, False
            else:
                state, triggered = current.run_state, current.triggered
            self._connection.execute(
                """
                INSERT INTO rule_state(rule_id, run_state, triggered, consecutive_absent)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    run_state = excluded.run_state,
                    triggered = excluded.triggered,
                    consecutive_absent = excluded.consecutive_absent
                """,
                (rule_id, state, int(triggered), absent),
            )
        return self.snapshot(rule_id)

    def begin(self, rule_id: str) -> None:
        self._upsert(rule_id, run_state=RunState.EXECUTING, triggered=True, consecutive_absent=0)

    def claim(self, rule_id: str) -> bool:
        """Atomically reserve one previously re-armed rule for irreversible output."""
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO rule_state(rule_id, run_state, triggered, consecutive_absent)
                VALUES (?, ?, 0, 0)
                ON CONFLICT(rule_id) DO NOTHING
                """,
                (rule_id, RunState.IDLE),
            )
            result = self._connection.execute(
                """
                UPDATE rule_state
                SET run_state = ?, triggered = 1, consecutive_absent = 0
                WHERE rule_id = ? AND triggered = 0
                """,
                (RunState.EXECUTING, rule_id),
            )
        return result.rowcount == 1

    def finish(self, rule_id: str, succeeded: bool) -> None:
        self._upsert(rule_id, run_state=RunState.SUCCEEDED if succeeded else RunState.FAILED, triggered=True, consecutive_absent=0)

    def can_trigger(self, rule_id: str) -> bool:
        return not self.snapshot(rule_id).triggered

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _upsert(
        self,
        rule_id: str,
        *,
        run_state: RunState | None,
        triggered: bool | None,
        consecutive_absent: int,
    ) -> None:
        with self._lock, self._connection:
            current = self.snapshot(rule_id)
            state = run_state if run_state is not None else current.run_state
            is_triggered = triggered if triggered is not None else current.triggered
            self._connection.execute(
                """
                INSERT INTO rule_state(rule_id, run_state, triggered, consecutive_absent)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    run_state = excluded.run_state,
                    triggered = excluded.triggered,
                    consecutive_absent = excluded.consecutive_absent
                """,
                (rule_id, state, int(is_triggered), consecutive_absent),
            )
