"""Safe, confirm-before-execute pointer action lifecycle."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Protocol

from ai_drive.vision import ScreenPoint, Screenshot, TargetCandidate


class ActionRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class AccessibleTarget:
    role: str
    title: str
    enabled: bool


@dataclass(frozen=True)
class DesktopState:
    display_id: int
    frontmost_bundle_id: str


@dataclass(frozen=True)
class PendingAction:
    action_id: str
    user_id: int
    chat_id: int
    point: ScreenPoint
    accessible_title: str
    display_id: int
    frontmost_bundle_id: str
    created_at: float
    screenshot_digest: str


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    point: ScreenPoint


class AccessibilityInspector(Protocol):
    def target_at(self, point: ScreenPoint) -> AccessibleTarget | None: ...


class DesktopObserver(Protocol):
    def state(self) -> DesktopState: ...


class PointerController(Protocol):
    def click(self, point: ScreenPoint) -> None: ...


_ACTIONABLE_ROLES = {"AXButton", "AXLink", "AXMenuItem", "AXCheckBox", "AXRadioButton", "AXPopUpButton"}
_SENSITIVE_TERMS = {
    "delete",
    "erase",
    "purchase",
    "buy",
    "pay",
    "password",
    "keychain",
    "authorize",
    "allow",
    "删除",
    "抹掉",
    "购买",
    "支付",
    "密码",
    "钥匙串",
    "授权",
    "允许",
}
_SAFE_LABEL_ALIASES = (
    ({"刷新"}, {"reload", "refresh"}),
)


def _labels_match(vision_label: str, accessibility_label: str) -> bool:
    vision = vision_label.casefold()
    accessibility = accessibility_label.casefold()
    if vision in accessibility or accessibility in vision:
        return True
    return any(
        any(term in vision for term in localized)
        and any(term in accessibility for term in english)
        for localized, english in _SAFE_LABEL_ALIASES
    )


class ActionService:
    def __init__(
        self,
        inspector: AccessibilityInspector,
        desktop: DesktopObserver,
        pointer: PointerController,
        *,
        now: Callable[[], float] = time.time,
        id_factory: Callable[[], str] = lambda: secrets.token_hex(4).upper(),
        allowed_bundle_ids: frozenset[str] = frozenset({"com.apple.Safari", "com.apple.TextEdit"}),
        minimum_confidence: float = 0.75,
        screenshot_max_age: float = 5.0,
        confirmation_ttl: float = 30.0,
    ):
        self._inspector = inspector
        self._desktop = desktop
        self._pointer = pointer
        self._now = now
        self._id_factory = id_factory
        self._allowed_bundle_ids = allowed_bundle_ids
        self._minimum_confidence = minimum_confidence
        self._screenshot_max_age = screenshot_max_age
        self._confirmation_ttl = confirmation_ttl
        self._pending: dict[str, PendingAction] = {}
        self._pending_by_user: dict[int, str] = {}
        self._pending_lock = threading.Lock()

    def prepare_click(
        self, screenshot: Screenshot, target: TargetCandidate, *, user_id: int, chat_id: int
    ) -> PendingAction:
        state = self._desktop.state()
        if screenshot.frontmost_bundle_id not in self._allowed_bundle_ids:
            raise ActionRejected("frontmost application is not allowlisted")
        if state != DesktopState(screenshot.display_id, screenshot.frontmost_bundle_id):
            raise ActionRejected("desktop state changed after capture")
        now = self._now()
        if now - screenshot.captured_at > self._screenshot_max_age:
            raise ActionRejected("screenshot is stale")
        if target.confidence < self._minimum_confidence:
            raise ActionRejected("vision confidence is too low")
        point = screenshot.to_screen_point(target.point)
        accessible = self._inspector.target_at(point)
        if accessible is None or accessible.role not in _ACTIONABLE_ROLES or not accessible.enabled:
            raise ActionRejected("target is not a confirmed actionable Accessibility element")
        combined = f"{target.description} {accessible.title}".casefold()
        if any(term in combined for term in _SENSITIVE_TERMS):
            raise ActionRejected("sensitive action is forbidden")
        if not accessible.title or not _labels_match(target.description, accessible.title):
            raise ActionRejected("vision target does not match Accessibility target")
        action = PendingAction(
            self._id_factory(), user_id, chat_id, point, accessible.title,
            screenshot.display_id, screenshot.frontmost_bundle_id, now, sha256(screenshot.image).hexdigest(),
        )
        with self._pending_lock:
            previous = self._pending_by_user.get(user_id)
            if previous:
                self._pending.pop(previous, None)
            self._pending[action.action_id] = action
            self._pending_by_user[user_id] = action.action_id
        return action

    def confirm(self, action_id: str, *, user_id: int, chat_id: int) -> ActionResult:
        action = self._consume(action_id, user_id=user_id, chat_id=chat_id)
        if self._now() - action.created_at > self._confirmation_ttl:
            raise ActionRejected("pending action expired")
        state = self._desktop.state()
        if state != DesktopState(action.display_id, action.frontmost_bundle_id):
            raise ActionRejected("desktop state changed before confirmation")
        accessible = self._inspector.target_at(action.point)
        if accessible is None or not accessible.enabled or accessible.title != action.accessible_title:
            raise ActionRejected("Accessibility target changed before confirmation")
        self._pointer.click(action.point)
        return ActionResult(action.action_id, action.point)

    def cancel(self, action_id: str, *, user_id: int, chat_id: int) -> None:
        self._consume(action_id, user_id=user_id, chat_id=chat_id)

    def _consume(self, action_id: str, *, user_id: int, chat_id: int) -> PendingAction:
        with self._pending_lock:
            action = self._pending.get(action_id)
            if action is None:
                raise ActionRejected("pending action does not exist")
            if action.user_id != user_id or action.chat_id != chat_id:
                raise ActionRejected("pending action belongs to another user or chat")
            self._pending.pop(action.action_id, None)
            if self._pending_by_user.get(action.user_id) == action.action_id:
                self._pending_by_user.pop(action.user_id, None)
            return action
