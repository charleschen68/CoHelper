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
    kAXEnabledAttribute,
    kAXDescriptionAttribute,
    kAXHelpAttribute,
    kAXRoleAttribute,
    kAXTitleAttribute,
    kAXTrustedCheckOptionPrompt,
)
from Cocoa import NSBitmapImageFileTypeJPEG, NSBitmapImageRep, NSImageCompressionFactor, NSWorkspace
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
    def click(self, point: ScreenPoint) -> None:
        for event_type in (kCGEventLeftMouseDown, kCGEventLeftMouseUp):
            event = CGEventCreateMouseEvent(None, event_type, (point.x, point.y), kCGMouseButtonLeft)
            if event is None:
                raise RuntimeError("failed to create Quartz mouse event")
            CGEventPost(kCGHIDEventTap, event)


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
        return AccessibleTarget(str(role or ""), str(title or ""), bool(enabled))

    @staticmethod
    def _attribute(element, attribute):
        error, value = AXUIElementCopyAttributeValue(element, attribute, None)
        return value if error == 0 else None


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
