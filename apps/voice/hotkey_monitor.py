"""Global/local macOS monitor for the explicit Option-Space gesture."""

from __future__ import annotations

from collections.abc import Callable

from ai_drive.voice import PushToTalkController, PushToTalkEvent

try:  # Keep the pure voice package importable outside macOS.
    from AppKit import NSEvent
except ImportError:  # pragma: no cover - only used on non-macOS hosts
    NSEvent = None


class HotkeyMonitorError(RuntimeError):
    """Raised when AppKit cannot install or remove the event monitors."""


class MacPushToTalkMonitor:
    SPACE_KEY_CODE = 49
    OPTION_MASK = 1 << 19
    KEY_DOWN_MASK = 1 << 10
    KEY_UP_MASK = 1 << 11

    def __init__(self, on_start: Callable[[], None], on_finish: Callable[[], None]):
        self._controller = PushToTalkController(self._dispatch)
        self._on_start = on_start
        self._on_finish = on_finish
        self._global_monitor = None
        self._local_monitor = None

    @property
    def is_running(self) -> bool:
        return self._global_monitor is not None or self._local_monitor is not None

    def start(self) -> None:
        if self.is_running:
            return
        if NSEvent is None:
            raise HotkeyMonitorError("AppKit event monitor is unavailable")
        try:
            mask = self.KEY_DOWN_MASK | self.KEY_UP_MASK
            self._global_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, self._handle_event
            )
            self._local_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                mask, self._handle_local_event
            )
            if self._global_monitor is None and self._local_monitor is None:
                raise HotkeyMonitorError("AppKit returned no push-to-talk monitor")
        except Exception as exc:
            self.stop()
            raise HotkeyMonitorError(f"failed to install push-to-talk monitor: {type(exc).__name__}") from exc

    def stop(self) -> None:
        for monitor in (self._global_monitor, self._local_monitor):
            if monitor is not None and NSEvent is not None:
                try:
                    NSEvent.removeMonitor_(monitor)
                except Exception:
                    pass
        self._global_monitor = None
        self._local_monitor = None

    def _handle_local_event(self, event):
        self._handle_event(event)
        return event

    def _handle_event(self, event):
        if event.keyCode() != self.SPACE_KEY_CODE:
            return
        if event.modifierFlags() & self.OPTION_MASK == 0:
            return
        event_type = int(event.type())
        if event_type == 10:
            pressed = True
        elif event_type == 11:
            pressed = False
        else:
            return
        self._controller.handle(PushToTalkEvent("space", True, pressed))

    def _dispatch(self, action: str) -> None:
        if action == "start":
            self._on_start()
        elif action == "finish":
            self._on_finish()
