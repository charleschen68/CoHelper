"""End-to-end visual click workflow used by local interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from ai_drive.actions import ActionRejected, ActionService
from ai_drive.macos import annotate_target, compress_screenshot
from ai_drive.vision import Screenshot, VisionAnalyzer


class ScreenCapture(Protocol):
    def capture_main_display(self) -> Screenshot: ...


@dataclass(frozen=True)
class PreparedVisualClick:
    action_id: str
    preview: bytes


class VisualClickWorkflow:
    def __init__(self, capture: ScreenCapture, analyzer: VisionAnalyzer, actions: ActionService):
        self._capture = capture
        self._analyzer = analyzer
        self._actions = actions

    def prepare(self, instruction: str, user_id: int, chat_id: int) -> PreparedVisualClick:
        preparation_id = self._actions.begin_click(user_id)
        screenshot = self._capture.capture_main_display()
        target = self._analyzer.locate(screenshot, instruction)
        verification = self._capture.capture_main_display()
        if (
            verification.display_id != screenshot.display_id
            or verification.frontmost_bundle_id != screenshot.frontmost_bundle_id
            or sha256(verification.image).digest() != sha256(screenshot.image).digest()
        ):
            raise ActionRejected("screen changed during visual analysis")
        pending = self._actions.prepare_click(
            verification,
            target,
            user_id=user_id,
            chat_id=chat_id,
            preparation_id=preparation_id,
        )
        preview = annotate_target(verification, pending.point, pending.accessible_title)
        return PreparedVisualClick(pending.action_id, preview)

    def confirm(self, action_id: str, user_id: int, chat_id: int) -> bytes:
        confirmation = self._capture.capture_main_display()
        self._actions.confirm(
            action_id,
            user_id=user_id,
            chat_id=chat_id,
            screenshot=confirmation,
        )
        return compress_screenshot(self._capture.capture_main_display())

    def cancel(self, action_id: str, user_id: int, chat_id: int) -> None:
        self._actions.cancel(action_id, user_id=user_id, chat_id=chat_id)
