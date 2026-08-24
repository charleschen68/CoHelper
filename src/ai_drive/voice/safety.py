"""Fail-closed safety state for voice direct-action preparation."""

from __future__ import annotations

import threading


class VoiceActionSafetyError(RuntimeError):
    pass


class VoiceActionSafetyGate:
    def __init__(self, *, enabled: bool = True):
        self._enabled = enabled
        self._emergency_stopped = False
        self._lock = threading.Lock()

    def emergency_stop(self) -> None:
        with self._lock:
            self._emergency_stopped = True

    def resume(self, *, manual: bool) -> None:
        if not manual:
            raise VoiceActionSafetyError("emergency stop requires manual resume")
        with self._lock:
            self._emergency_stopped = False

    def assert_ready(self, *, overlay_masked: bool) -> None:
        with self._lock:
            if not self._enabled:
                raise VoiceActionSafetyError("voice direct actions are disabled")
            if self._emergency_stopped:
                raise VoiceActionSafetyError("voice direct actions are emergency-stopped")
        if not overlay_masked:
            raise VoiceActionSafetyError("action screenshot must mask the overlay")
