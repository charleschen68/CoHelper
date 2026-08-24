"""Platform-neutral push-to-talk edge handling."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PushToTalkEvent:
    key: str
    option: bool
    pressed: bool


class PushToTalkController:
    def __init__(self, on_action: Callable[[str], None]):
        self._on_action = on_action
        self._pressed = False

    def handle(self, event: PushToTalkEvent) -> str | None:
        if event.key != "space" or not event.option:
            return None
        if event.pressed:
            if self._pressed:
                return None
            self._pressed = True
            action = "start"
        else:
            if not self._pressed:
                return None
            self._pressed = False
            action = "finish"
        self._on_action(action)
        return action
