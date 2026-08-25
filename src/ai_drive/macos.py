"""Native macOS adapters for capture, desktop state, and pointer events."""

from __future__ import annotations

import time
from collections import deque
from io import BytesIO
from typing import Callable

from ApplicationServices import (
    AXIsProcessTrusted,
    AXIsProcessTrustedWithOptions,
    AXUIElementCopyAttributeValue,
    AXUIElementCopyElementAtPosition,
    AXUIElementCreateApplication,
    AXUIElementCreateSystemWide,
    AXUIElementGetPid,
    AXValueGetValue,
    kAXEnabledAttribute,
    kAXFocusedWindowAttribute,
    kAXDescriptionAttribute,
    kAXChildrenAttribute,
    kAXHelpAttribute,
    kAXIdentifierAttribute,
    kAXParentAttribute,
    kAXRoleAttribute,
    kAXPositionAttribute,
    kAXSizeAttribute,
    kAXTitleAttribute,
    kAXTrustedCheckOptionPrompt,
    kAXWindowsAttribute,
    kAXValueCGPointType,
    kAXValueCGSizeType,
)
from Cocoa import (
    NSBitmapImageFileTypeJPEG,
    NSBitmapImageRep,
    NSImageCompressionFactor,
    NSRunningApplication,
    NSWorkspace,
)
from PIL import Image, ImageDraw
from Quartz import (
    CGDisplayBounds,
    CGDisplayCreateImage,
    CGEventCreateMouseEvent,
    CGEventPost,
    CGMainDisplayID,
    CGPreflightScreenCaptureAccess,
    CGRequestScreenCaptureAccess,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseUp,
    kCGHIDEventTap,
    kCGMouseButtonLeft,
)

from ai_drive.actions import (
    AccessibilityCapability,
    AccessibleTarget,
    DesktopState,
    LocatedAccessibleTarget,
)
from ai_drive.region_capture import crop_screenshot
from ai_drive.region_selection import RegionSelection
from ai_drive.vision import OverlayMask, ScreenPoint, Screenshot, mask_screenshot


class QuartzScreenCapture:
    def __init__(self, overlay_mask_provider: Callable[[], OverlayMask | None] | None = None):
        self._overlay_mask_provider = overlay_mask_provider

    def has_permission(self) -> bool:
        return bool(CGPreflightScreenCaptureAccess())

    def request_permission(self) -> bool:
        return bool(CGRequestScreenCaptureAccess())

    def capture_main_display(self) -> Screenshot:
        return self.capture_display(int(CGMainDisplayID()))

    def capture_display(self, display_id: int) -> Screenshot:
        if not self.has_permission():
            raise PermissionError("macOS Screen Recording permission is required")
        display_id = int(display_id)
        bounds = CGDisplayBounds(display_id)
        image = CGDisplayCreateImage(display_id)
        if image is None:
            raise RuntimeError("failed to capture the main display")
        bitmap = NSBitmapImageRep.alloc().initWithCGImage_(image)
        data = bitmap.representationUsingType_properties_(NSBitmapImageFileTypeJPEG, {NSImageCompressionFactor: 0.82})
        frontmost = NSWorkspace.sharedWorkspace().frontmostApplication()
        bundle_id = str(frontmost.bundleIdentifier() or "") if frontmost else ""
        screenshot = Screenshot(
            bytes(data), int(bitmap.pixelsWide()), int(bitmap.pixelsHigh()),
            float(bounds.size.width), float(bounds.size.height), display_id,
            time.time(), bundle_id, float(bounds.origin.x), float(bounds.origin.y),
        )
        if self._overlay_mask_provider is None:
            return screenshot
        mask = self._overlay_mask_provider()
        return mask_screenshot(screenshot, mask) if mask is not None else screenshot

    def capture_region(self, selection: RegionSelection) -> Screenshot:
        """Capture one display and crop it to the committed logical selection."""
        return crop_screenshot(self.capture_display(selection.display_id), selection)

    def apply_overlay_mask(self, screenshot: Screenshot) -> Screenshot:
        """Apply the current mask to an already captured screenshot."""
        if self._overlay_mask_provider is None:
            return screenshot
        mask = self._overlay_mask_provider()
        return mask_screenshot(screenshot, mask) if mask is not None else screenshot


class QuartzDesktopObserver:
    def state(self) -> DesktopState:
        frontmost = NSWorkspace.sharedWorkspace().frontmostApplication()
        bundle_id = str(frontmost.bundleIdentifier() or "") if frontmost else ""
        return DesktopState(int(CGMainDisplayID()), bundle_id)


class QuartzPointerController:
    def __init__(self, event_factory=CGEventCreateMouseEvent, event_post=CGEventPost):
        self._event_factory = event_factory
        self._event_post = event_post

    def click(self, point: ScreenPoint) -> None:
        down = self._event_factory(None, kCGEventLeftMouseDown, (point.x, point.y), kCGMouseButtonLeft)
        up = self._event_factory(None, kCGEventLeftMouseUp, (point.x, point.y), kCGMouseButtonLeft)
        if down is None or up is None:
            raise RuntimeError("failed to create complete Quartz mouse gesture")
        self._event_post(kCGHIDEventTap, down)
        self._event_post(kCGHIDEventTap, up)


class MacAccessibilityInspector:
    def has_permission(self) -> bool:
        return bool(AXIsProcessTrusted())

    def request_permission(self) -> bool:
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))

    def target_at(self, point: ScreenPoint) -> AccessibleTarget | None:
        if not self.has_permission():
            raise PermissionError("macOS Accessibility permission is required")
        error, element = AXUIElementCopyElementAtPosition(
            AXUIElementCreateSystemWide(), float(point.x), float(point.y), None
        )
        if error != 0 or element is None:
            return None
        return self._target_for_element(element)

    def find_capability(
        self, capability: AccessibilityCapability
    ) -> LocatedAccessibleTarget | None:
        """Find one native, explicitly configured control in the frontmost app."""
        if not self.has_permission():
            raise PermissionError("macOS Accessibility permission is required")
        applications = NSRunningApplication.runningApplicationsWithBundleIdentifier_(
            capability.bundle_id
        )
        if not applications:
            return None
        application = applications[0]
        root = self._application_element(application)
        focused_window = self._attribute(root, kAXFocusedWindowAttribute)
        if focused_window is None:
            return None
        for element in self._walk_elements(focused_window):
            target = self._target_for_element(element)
            if target is None or not self._matches_capability(target, capability):
                continue
            point = self._center_point(element)
            if point is not None:
                return LocatedAccessibleTarget(point, target)
        return None

    @staticmethod
    def _application_element(application):
        return AXUIElementCreateApplication(int(application.processIdentifier()))

    def _target_for_element(self, element) -> AccessibleTarget | None:
        role = self._attribute(element, kAXRoleAttribute)
        title = (
            self._attribute(element, kAXTitleAttribute)
            or self._attribute(element, kAXDescriptionAttribute)
            or self._attribute(element, kAXHelpAttribute)
        )
        enabled = self._attribute(element, kAXEnabledAttribute)
        identifier = self._attribute(element, kAXIdentifierAttribute)
        pid_error, pid = AXUIElementGetPid(element, None)
        owner_bundle_id = ""
        if pid_error == 0:
            application = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if application is not None:
                owner_bundle_id = str(application.bundleIdentifier() or "")
        return AccessibleTarget(
            str(role or ""),
            str(title or ""),
            bool(enabled),
            owner_bundle_id,
            str(identifier or ""),
            self._ancestor_roles(element),
        )

    @classmethod
    def _walk_elements(cls, root, max_nodes: int = 512):
        pending = deque([root])
        seen: set[int] = set()
        visited = 0
        while pending and visited < max_nodes:
            element = pending.popleft()
            identity = id(element)
            if identity in seen:
                continue
            seen.add(identity)
            visited += 1
            yield element
            children = cls._attribute(element, kAXChildrenAttribute)
            windows = cls._attribute(element, kAXWindowsAttribute)
            for group in (children, windows):
                if group is None:
                    continue
                try:
                    pending.extend(group)
                except TypeError:
                    continue

    @staticmethod
    def _matches_capability(
        target: AccessibleTarget, capability: AccessibilityCapability
    ) -> bool:
        return (
            target.owner_bundle_id == capability.bundle_id
            and target.role == capability.role
            and target.title.casefold() == capability.title.casefold()
            and capability.ancestor_role in target.ancestor_roles
            and (not capability.identifier or target.identifier == capability.identifier)
        )

    @classmethod
    def _center_point(cls, element) -> ScreenPoint | None:
        position = cls._attribute(element, kAXPositionAttribute)
        size = cls._attribute(element, kAXSizeAttribute)
        try:
            point = cls._ax_value(position, kAXValueCGPointType)
            dimensions = cls._ax_value(size, kAXValueCGSizeType)
            x = float(cls._component(point, "x", 0))
            y = float(cls._component(point, "y", 1))
            width = float(cls._component(dimensions, "width", 0))
            height = float(cls._component(dimensions, "height", 1))
        except (AttributeError, IndexError, TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return ScreenPoint(x + width / 2, y + height / 2)

    @staticmethod
    def _component(value, attribute: str, index: int):
        if hasattr(value, attribute):
            return getattr(value, attribute)
        return value[index]

    @staticmethod
    def _ax_value(value, value_type):
        """Unbox an Accessibility AXValue into a Python CGPoint or CGSize."""
        result = AXValueGetValue(value, value_type, None)
        if isinstance(result, tuple) and len(result) == 2:
            success, unboxed = result
            if success:
                return unboxed
        raise ValueError("Accessibility geometry attribute is unavailable")

    @staticmethod
    def _attribute(element, attribute):
        error, value = AXUIElementCopyAttributeValue(element, attribute, None)
        return value if error == 0 else None

    @classmethod
    def _ancestor_roles(cls, element) -> tuple[str, ...]:
        roles = []
        current = element
        for _ in range(16):
            parent = cls._attribute(current, kAXParentAttribute)
            if parent is None:
                break
            role = cls._attribute(parent, kAXRoleAttribute)
            if role:
                roles.append(str(role))
            current = parent
        return tuple(roles)


def annotate_target(screenshot: Screenshot, point: ScreenPoint, label: str) -> bytes:
    image = Image.open(BytesIO(screenshot.image)).convert("RGB")
    scale_x = screenshot.pixel_width / screenshot.logical_width
    scale_y = screenshot.pixel_height / screenshot.logical_height
    x = int((point.x - screenshot.origin_x) * scale_x)
    y = int((point.y - screenshot.origin_y) * scale_y)
    draw = ImageDraw.Draw(image)
    radius = max(12, min(image.size) // 60)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline="red", width=max(3, radius // 4))
    draw.text((x + radius + 4, max(0, y - radius)), label, fill="red")
    image.thumbnail((1280, 1280))
    output = BytesIO()
    image.save(output, "JPEG", quality=72, optimize=True)
    return output.getvalue()


def compress_screenshot(screenshot: Screenshot) -> bytes:
    image = Image.open(BytesIO(screenshot.image)).convert("RGB")
    image.thumbnail((1280, 1280))
    output = BytesIO()
    image.save(output, "JPEG", quality=72, optimize=True)
    return output.getvalue()
