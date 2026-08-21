"""High-frequency, independent emergency-stop monitoring."""

from __future__ import annotations

import threading
from collections.abc import Callable


class EmergencyStopMonitor:
    """Latch one emergency stop without waiting for the normal scan cadence."""

    def __init__(self, pointer_is_in_corner: Callable[[], bool], emergency_stop: Callable[[], None], *, interval_seconds: float = 0.1):
        self._pointer_is_in_corner = pointer_is_in_corner
        self._emergency_stop = emergency_stop
        self._interval_seconds = interval_seconds
        self._halt = threading.Event()
        self._triggered = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._halt.clear()
        self._thread = threading.Thread(target=self._run, name="cohelper-automation-emergency-stop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._halt.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None

    def _run(self) -> None:
        while not self._halt.is_set() and not self._triggered.is_set():
            if self._pointer_is_in_corner():
                self._triggered.set()
                self._emergency_stop()
                return
            self._halt.wait(self._interval_seconds)
