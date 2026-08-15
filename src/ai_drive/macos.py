"""Native macOS adapters for capture, desktop state, and pointer events."""

from __future__ import annotations

import time
from io import BytesIO

from ApplicationServices import (
    AXIsProcessTrusted,
    AXIsProcessTrustedWithOptions,
    AXUIElementCopyAttributeValue,
    AXUIElementCopyElementAtPosition,
    AXUIElementCreateSystemWide,
    AXUIElementGetPid,
    kAXEnabledAttribute,
    kAXDescriptionAttribute,
    kAXHelpAttribute,
    kAXIdentifierAttribute,
    kAXParentAttribute,
    kAXRoleAttribute,
    kAXTitleAttribute,
    kAXTrustedCheckOptionPrompt,
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

from ai_drive.actions import AccessibleTarget, DesktopState
from ai_drive.vision import ScreenPoint, Screenshot


class QuartzScreenCapture:
    def has_permission(self) -> bool:
        return bool(CGPreflightScreenCaptureAccess())

    def request_permission(self) -> bool:
        return bool(CGRequestScreenCaptureAccess())

    def capture_main_display(self) -> Screenshot:
        if not self.has_permission():
            raise PermissionError("macOS Screen Recording permission is required")
        display_id = int(CGMainDisplayID())
        bounds = CGDisplayBounds(display_id)
        image = CGDisplayCreateImage(display_id)
        if image is None:
            raise RuntimeError("failed to capture the main display")
        bitmap = NSBitmapImageRep.alloc().initWithCGImage_(image)
        data = bitmap.representationUsingType_properties_(NSBitmapImageFileTypeJPEG, {NSImageCompressionFactor: 0.82})
        frontmost = NSWorkspace.sharedWorkspace().frontmostApplication()
        bundle_id = str(frontmost.bundleIdentifier() or "") if frontmost else ""
        return Screenshot(
            bytes(data), int(bitmap.pixelsWide()), int(bitmap.pixelsHigh()),
            float(bounds.size.width), float(bounds.size.height), display_id,
            time.time(), bundle_id, float(bounds.origin.x), float(bounds.origin.y),
        )


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
