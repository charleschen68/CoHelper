"""Quartz output adapter for guarded automation actions."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from Quartz import CGEventCreateKeyboardEvent, CGEventKeyboardSetUnicodeString, CGEventPost, kCGHIDEventTap


class QuartzAutomationOutput:
    def __init__(
        self,
        *,
        locate: Callable[[Path], tuple[float, float] | None],
        click: Callable[[tuple[float, float]], None],
        type_unicode: Callable[[str, Callable[[], bool]], None] | None = None,
        play_sound: Callable[[str], None],
        notify: Callable[[], None],
        should_stop: Callable[[], bool] = lambda: False,
    ):
        self._locate = locate
        self._click = click
        self._type_unicode = type_unicode or self._emit_unicode
        self._play_sound = play_sound
        self._notify = notify
        self._should_stop = should_stop

    def click(self, template: Path, offset: tuple[float, float]) -> None:
        point = self._locate(template)
        if point is None:
            raise RuntimeError("guard template is not present")
        self._click((point[0] + offset[0], point[1] + offset[1]))

    def type_text(self, text: str) -> None:
        self._type_unicode(text, self._should_stop)

    def press_key(self, key: str) -> None:
        if key != "enter":
            raise ValueError(f"unsupported key: {key}")
        if self._should_stop():
            raise RuntimeError("automation is stopped")
        for down in (True, False):
            event = CGEventCreateKeyboardEvent(None, 36, down)
            if event is None:
                raise RuntimeError("failed to create keyboard event")
            CGEventPost(kCGHIDEventTap, event)

    def sound(self, mode: str) -> None:
        self._play_sound(mode)

    def telegram(self) -> None:
        self._notify()

    @staticmethod
    def _emit_unicode(text: str, should_stop: Callable[[], bool]) -> None:
        for char in text:
            if should_stop():
                raise RuntimeError("automation is stopped")
            for down in (True, False):
                event = CGEventCreateKeyboardEvent(None, 0, down)
                if event is None:
                    raise RuntimeError("failed to create keyboard event")
                CGEventKeyboardSetUnicodeString(event, len(char), char)
                CGEventPost(kCGHIDEventTap, event)
