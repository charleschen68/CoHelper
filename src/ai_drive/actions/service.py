"""Safe, confirm-before-execute pointer action lifecycle."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import Callable, Protocol

from PIL import Image, UnidentifiedImageError

from ai_drive.vision import ScreenPoint, Screenshot, TargetCandidate


class ActionRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class AccessibleTarget:
    role: str
    title: str
    enabled: bool
    owner_bundle_id: str = ""
    identifier: str = ""
    ancestor_roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class LocatedAccessibleTarget:
    point: ScreenPoint
    target: AccessibleTarget


@dataclass(frozen=True)
class AccessibilityCapability:
    bundle_id: str
    role: str
    title: str
    ancestor_role: str
    identifier: str = ""


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
    target_description: str
    accessible_role: str
    accessible_title: str
    display_id: int
    frontmost_bundle_id: str
    created_at: float
    screenshot_digest: str
    authorization_generation: int = 0


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    point: ScreenPoint


class AccessibilityInspector(Protocol):
    def target_at(self, point: ScreenPoint) -> AccessibleTarget | None: ...

    def find_capability(
        self, capability: AccessibilityCapability
    ) -> LocatedAccessibleTarget | None: ...


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
_NATIVE_CAPABILITY_INTENTS = {
    "reload this page": (
        "reload this page",
        "refresh page",
        "刷新按钮",
        "刷新当前页面",
        "重新加载页面",
        "重新加载当前页面",
    ),
    "刷新按钮": (
        "reload this page",
        "refresh page",
        "刷新按钮",
        "刷新当前页面",
        "重新加载页面",
        "重新加载当前页面",
    ),
}
_TARGET_REGION_RADIUS_LOGICAL = 32.0


def _target_region_digest(screenshot: Screenshot, point: ScreenPoint) -> str:
    """Digest only the native target's surrounding pixels, not the full desktop.

    The target itself is separately revalidated through Accessibility at
    confirmation. Decoding before hashing avoids false mismatches from JPEG
    container metadata or encoder output while still rejecting a changed target
    region. Test fixtures that are not images retain the former byte digest.
    """
    try:
        image = Image.open(BytesIO(screenshot.image)).convert("RGB")
        scale_x = screenshot.pixel_width / screenshot.logical_width
        scale_y = screenshot.pixel_height / screenshot.logical_height
        center_x = (point.x - screenshot.origin_x) * scale_x
        center_y = (point.y - screenshot.origin_y) * scale_y
        radius_x = _TARGET_REGION_RADIUS_LOGICAL * scale_x
        radius_y = _TARGET_REGION_RADIUS_LOGICAL * scale_y
        box = (
            max(0, int(center_x - radius_x)),
            max(0, int(center_y - radius_y)),
            min(image.width, int(center_x + radius_x)),
            min(image.height, int(center_y + radius_y)),
        )
        if box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError("target region is outside the screenshot")
        return sha256(image.crop(box).tobytes()).hexdigest()
    except (OSError, UnidentifiedImageError, ValueError):
        return sha256(screenshot.image).hexdigest()


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


def _native_instruction_matches(instruction: str, capability: AccessibilityCapability) -> bool:
    """Require an explicit, capability-specific intent before native discovery."""
    intents = _NATIVE_CAPABILITY_INTENTS.get(capability.title.casefold(), ())
    normalized = instruction.casefold()
    return any(intent in normalized for intent in intents)


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
        allowed_capabilities: frozenset[AccessibilityCapability] = frozenset(
            {
                AccessibilityCapability(
                    "com.apple.Safari", "AXButton", "Reload this page", "AXToolbar"
                ),
                AccessibilityCapability(
                    "com.apple.Safari", "AXButton", "刷新按钮", "AXToolbar"
                ),
            }
        ),
        minimum_confidence: float = 0.75,
        screenshot_max_age: float = 5.0,
        confirmation_ttl: float = 30.0,
    ):
        if confirmation_ttl != 30.0:
            raise ValueError("confirmation TTL is fixed to 30 seconds")
        self._inspector = inspector
        self._desktop = desktop
        self._pointer = pointer
        self._now = now
        self._id_factory = id_factory
        self._allowed_bundle_ids = allowed_bundle_ids
        self._allowed_capabilities = allowed_capabilities
        self._minimum_confidence = minimum_confidence
        self._screenshot_max_age = screenshot_max_age
        self._confirmation_ttl = confirmation_ttl
        self._pending: dict[str, PendingAction] = {}
        self._pending_by_user: dict[int, str] = {}
        self._preparation_by_user: dict[int, int] = {}
        self._preparation_sequence = 0
        self._authorization_generation = 0
        self._pending_lock = threading.Lock()

    def revoke_all(self) -> None:
        """Make every prepared action unconfirmable after an authorization change."""
        with self._pending_lock:
            self._authorization_generation += 1
            self._pending.clear()
            self._pending_by_user.clear()
            self._preparation_by_user.clear()

    def begin_click(self, user_id: int) -> int:
        """Invalidate the user's old action as soon as a new click request arrives."""
        with self._pending_lock:
            self._preparation_sequence += 1
            preparation_id = self._preparation_sequence
            self._preparation_by_user[user_id] = preparation_id
            previous = self._pending_by_user.pop(user_id, None)
            if previous:
                self._pending.pop(previous, None)
            return preparation_id

    def prepare_click(
        self,
        screenshot: Screenshot,
        target: TargetCandidate,
        *,
        user_id: int,
        chat_id: int,
        preparation_id: int | None = None,
    ) -> PendingAction:
        if preparation_id is None:
            preparation_id = self.begin_click(user_id)
        self._validate_screenshot_desktop(screenshot)
        if target.confidence < self._minimum_confidence:
            raise ActionRejected("vision confidence is too low")
        point = screenshot.to_screen_point(target.point)
        accessible = self._inspector.target_at(point)
        self._validate_accessible(
            screenshot.frontmost_bundle_id, target.description, accessible
        )
        self._validate_screenshot_desktop(screenshot)
        assert accessible is not None
        return self._store_pending(
            screenshot,
            point,
            target.description,
            accessible,
            user_id=user_id,
            chat_id=chat_id,
            preparation_id=preparation_id,
        )

    def prepare_capability_click(
        self,
        screenshot: Screenshot,
        instruction: str,
        *,
        user_id: int,
        chat_id: int,
        preparation_id: int | None = None,
    ) -> PendingAction | None:
        """Prepare an explicitly allowlisted native control without vision.

        Returning ``None`` means native discovery did not resolve an approved
        capability, so callers may use the vision path. That path must still
        prove the exact same Accessibility capability before it can prepare an
        action.
        """
        if preparation_id is None:
            preparation_id = self.begin_click(user_id)
        bundle_id = screenshot.frontmost_bundle_id
        candidates = tuple(
            capability
            for capability in self._allowed_capabilities
            if capability.bundle_id == bundle_id
            and _native_instruction_matches(instruction, capability)
        )
        if not candidates:
            return None
        self._validate_screenshot_desktop(screenshot)
        for capability in candidates:
            located = self._inspector.find_capability(capability)
            if located is None:
                continue
            if not self._point_in_screenshot(screenshot, located.point):
                raise ActionRejected("native target is outside the captured main display")
            self._validate_accessible(bundle_id, instruction, located.target)
            self._validate_screenshot_desktop(screenshot)
            return self._store_pending(
                screenshot,
                located.point,
                instruction,
                located.target,
                user_id=user_id,
                chat_id=chat_id,
                preparation_id=preparation_id,
            )
        return None

    def _validate_screenshot_desktop(self, screenshot: Screenshot) -> None:
        if screenshot.frontmost_bundle_id not in self._allowed_bundle_ids:
            raise ActionRejected("frontmost application is not allowlisted")
        if self._desktop.state() != DesktopState(
            screenshot.display_id, screenshot.frontmost_bundle_id
        ):
            raise ActionRejected("desktop state changed after capture")
        if self._now() - screenshot.captured_at > self._screenshot_max_age:
            raise ActionRejected("screenshot is stale")

    def _store_pending(
        self,
        screenshot: Screenshot,
        point: ScreenPoint,
        target_description: str,
        accessible: AccessibleTarget,
        *,
        user_id: int,
        chat_id: int,
        preparation_id: int,
    ) -> PendingAction:
        now = self._now()
        with self._pending_lock:
            if self._preparation_by_user.get(user_id) != preparation_id:
                raise ActionRejected("a newer click request superseded this preparation")
            action_id = ""
            for _ in range(5):
                candidate_id = self._id_factory()
                if candidate_id not in self._pending:
                    action_id = candidate_id
                    break
            if not action_id:
                raise ActionRejected("could not allocate a unique action identifier")
            action = PendingAction(
                action_id,
                user_id,
                chat_id,
                point,
                target_description,
                accessible.role,
                accessible.title,
                screenshot.display_id,
                screenshot.frontmost_bundle_id,
                now,
                _target_region_digest(screenshot, point),
                self._authorization_generation,
            )
            self._pending[action.action_id] = action
            self._pending_by_user[user_id] = action.action_id
        return action

    def confirm(
        self,
        action_id: str,
        *,
        user_id: int,
        chat_id: int,
        screenshot: Screenshot,
    ) -> ActionResult:
        action = self._consume(action_id, user_id=user_id, chat_id=chat_id)
        now = self._now()
        if now - action.created_at > self._confirmation_ttl:
            raise ActionRejected("pending action expired")
        if now - screenshot.captured_at > self._screenshot_max_age:
            raise ActionRejected("confirmation screenshot is stale")
        if screenshot.display_id != action.display_id or screenshot.frontmost_bundle_id != action.frontmost_bundle_id:
            raise ActionRejected("confirmation screenshot desktop state changed")
        if _target_region_digest(screenshot, action.point) != action.screenshot_digest:
            raise ActionRejected("target region changed before confirmation")
        state = self._desktop.state()
        if state != DesktopState(action.display_id, action.frontmost_bundle_id):
            raise ActionRejected("desktop state changed before confirmation")
        accessible = self._inspector.target_at(action.point)
        self._validate_accessible(
            action.frontmost_bundle_id, action.target_description, accessible
        )
        if (
            accessible is None
            or accessible.role != action.accessible_role
            or accessible.title != action.accessible_title
        ):
            raise ActionRejected("Accessibility target changed before confirmation")
        # Keep the final authorization check and the irreversible click in the
        # same critical section. A config watcher can revoke permissions from
        # another thread while confirmation is still being validated.
        with self._pending_lock:
            if action.authorization_generation != self._authorization_generation:
                raise ActionRejected("action authorization was revoked")
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

    def _validate_accessible(
        self,
        frontmost_bundle_id: str,
        target_description: str,
        accessible: AccessibleTarget | None,
    ) -> None:
        if accessible is None or accessible.role not in _ACTIONABLE_ROLES or not accessible.enabled:
            raise ActionRejected("target is not a confirmed actionable Accessibility element")
        combined = f"{target_description} {accessible.title}".casefold()
        if any(term in combined for term in _SENSITIVE_TERMS):
            raise ActionRejected("sensitive action is forbidden")
        if accessible.owner_bundle_id != frontmost_bundle_id:
            raise ActionRejected("Accessibility target belongs to another application")
        if "AXWebArea" in accessible.ancestor_roles:
            raise ActionRejected("web-content Accessibility targets are forbidden")
        capability_match = any(
            capability.bundle_id == frontmost_bundle_id
            and capability.role == accessible.role
            and capability.title.casefold() == accessible.title.casefold()
            and capability.ancestor_role in accessible.ancestor_roles
            and (
                not capability.identifier
                or capability.identifier == accessible.identifier
            )
            for capability in self._allowed_capabilities
        )
        if not capability_match:
            raise ActionRejected("Accessibility target is not an allowlisted native capability")
        if not _labels_match(target_description, accessible.title):
            raise ActionRejected("vision target does not match Accessibility target")

    @staticmethod
    def _point_in_screenshot(screenshot: Screenshot, point: ScreenPoint) -> bool:
        return (
            screenshot.origin_x <= point.x < screenshot.origin_x + screenshot.logical_width
            and screenshot.origin_y <= point.y < screenshot.origin_y + screenshot.logical_height
        )
