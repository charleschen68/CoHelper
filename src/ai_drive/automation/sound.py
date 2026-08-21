"""Single-worker macOS system-sound lifecycle."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Callable


class SystemAlarm:
    def __init__(
        self,
        *,
        play_once: Callable[[], None] | None = None,
        interval_seconds: float = 0.25,
        player: Path = Path("/usr/bin/afplay"),
        sound: Path = Path("/System/Library/Sounds/Basso.aiff"),
    ):
        self._interval = interval_seconds
        self._player = player
        self._sound = sound
        self._play_once = play_once or self._play_system_sound
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, mode: str) -> None:
        if mode == "once":
            self._play_once()
            return
        if mode not in {"latched", "while_present"}:
            raise ValueError(f"unsupported sound mode: {mode}")
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cohelper-automation-alarm", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=6)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._play_once()
            except (OSError, RuntimeError, subprocess.SubprocessError):
                self._stop.wait(5)
                continue
            self._stop.wait(self._interval)

    def _play_system_sound(self) -> None:
        completed = subprocess.run([str(self._player), str(self._sound)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=5, check=False)
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or "afplay failed")
