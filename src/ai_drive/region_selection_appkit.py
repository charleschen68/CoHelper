"""AppKit adapter for the explicit region-selection session."""

from __future__ import annotations

import threading
from typing import Callable

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSEvent,
    NSFloatingWindowLevel,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSPointInRect
from PyObjCTools import AppHelper

from ai_drive.macos import QuartzScreenCapture
from ai_drive.region_selection import RegionSelectionSession


class _SelectionCanvas(NSView):
    def initWithFrame_controller_(self, frame, controller):
        self = objc.super(_SelectionCanvas, self).initWithFrame_(frame)
        if self is None:
            return None
        self.controller = controller
        return self

    def drawRect_(self, _rect):
        NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.35).setFill()
        NSBezierPath.fillRect_(self.bounds())
        selection = self.controller.current_selection
        if selection is None:
            return
        frame = self.window().frame()
        local = NSMakeRect(
            selection.x - frame.origin.x,
            selection.y - frame.origin.y,
            selection.width,
            selection.height,
        )
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.15, 0.45, 1.0, 0.18).setFill()
        NSBezierPath.fillRect_(local)
        path = NSBezierPath.bezierPathWithRect_(local)
        path.setLineWidth_(2.0)
        NSColor.systemBlueColor().setStroke()
        path.stroke()


class _SelectionWindow(NSPanel):
    def initWithController_frame_(self, controller, frame):
        style = NSWindowStyleMaskBorderless
        self = objc.super(_SelectionWindow, self).initWithContentRect_styleMask_backing_defer_(
            frame, style, NSBackingStoreBuffered, False
        )
        if self is None:
            return None
        self.controller = controller
        self.setOpaque_(False)
        self.setBackgroundColor_(NSColor.clearColor())
        self.setHasShadow_(False)
        self.setIgnoresMouseEvents_(False)
        self.setLevel_(NSFloatingWindowLevel)
        self.setAcceptsMouseMovedEvents_(True)
        self.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        self.setContentView_(_SelectionCanvas.alloc().initWithFrame_controller_(self.contentView().bounds(), controller))
        return self

    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return False

    def acceptsFirstMouse_(self, _event):
        return True

    def _global_point(self, event):
        point = event.locationInWindow()
        frame = self.frame()
        return (float(frame.origin.x + point.x), float(frame.origin.y + point.y))

    def mouseDown_(self, event):
        self.controller.mouse_down(self._global_point(event))

    def mouseDragged_(self, event):
        self.controller.mouse_dragged(self._global_point(event))

    def mouseUp_(self, event):
        self.controller.mouse_up(self._global_point(event))

    def keyDown_(self, event):
        if event.keyCode() == 53:
            self.controller.cancel()
            return
        objc.super(_SelectionWindow, self).keyDown_(event)


class RegionSelectionOverlayController:
    """Own one interactive full-display selection overlay."""

    def __init__(
        self,
        *,
        capture: QuartzScreenCapture | None = None,
        screen_provider: Callable[[], object] | None = None,
        pointer_provider: Callable[[], object] | None = None,
        on_selected: Callable[[int, object, object], None] | None = None,
        on_cancelled: Callable[[int], None] | None = None,
        on_error: Callable[[int, Exception], None] | None = None,
    ):
        self._capture = capture or QuartzScreenCapture()
        self._screen_provider = screen_provider or NSScreen.screens
        self._pointer_provider = pointer_provider or NSEvent.mouseLocation
        self._on_selected = on_selected or (lambda _generation, _selection, _screenshot: None)
        self._on_cancelled = on_cancelled or (lambda _generation: None)
        self._on_error = on_error or (lambda _generation, _error: None)
        self._session = RegionSelectionSession()
        self._window = None
        self._screen = None
        self._drag_start = None
        self._drag_end = None
        self.current_selection = None

    @property
    def generation(self) -> int:
        return self._session.snapshot().generation

    def set_callbacks(self, *, on_selected=None, on_cancelled=None, on_error=None) -> None:
        if on_selected is not None:
            self._on_selected = on_selected
        if on_cancelled is not None:
            self._on_cancelled = on_cancelled
        if on_error is not None:
            self._on_error = on_error

    def trigger(self) -> int:
        if not self._capture.has_permission():
            raise PermissionError("macOS Screen Recording permission is required")
        screen = self._screen_at(self._pointer_provider())
        if screen is None:
            raise RuntimeError("no display contains the pointer")
        frame = screen.frame()
        display_id = int(screen.deviceDescription()["NSScreenNumber"])
        generation = self._session.begin(
            display_id,
            (float(frame.origin.x), float(frame.origin.y)),
            (float(frame.size.width), float(frame.size.height)),
        )
        self._close_window()
        self._screen = screen
        self._drag_start = None
        self._drag_end = None
        self.current_selection = None
        self._window = _SelectionWindow.alloc().initWithController_frame_(self, frame)
        self._window.makeKeyAndOrderFront_(None)
        return generation

    def mouse_down(self, point: tuple[float, float]) -> None:
        if self._window is None:
            return
        self._drag_start = point
        self._drag_end = point
        self.current_selection = None
        self._redraw()

    def mouse_dragged(self, point: tuple[float, float]) -> None:
        if self._drag_start is None:
            return
        self._drag_end = point
        try:
            self.current_selection = self._session.update_drag(self._drag_start, point)
        except Exception:
            self.current_selection = None
        self._redraw()

    def mouse_up(self, point: tuple[float, float]) -> None:
        if self._drag_start is None:
            return
        self._drag_end = point
        try:
            self.current_selection = self._session.update_drag(self._drag_start, point)
        except Exception:
            self.current_selection = None
            self._redraw()
            return
        generation = self._session.snapshot().generation
        self._close_window()
        threading.Thread(
            target=self._capture_worker,
            args=(generation,),
            name="region-selection-capture",
            daemon=True,
        ).start()

    def cancel(self) -> bool:
        generation = self._session.snapshot().generation
        cancelled = self._session.cancel()
        self._close_window()
        if cancelled:
            AppHelper.callAfter(self._on_cancelled, generation)
        return cancelled

    def close(self) -> None:
        self.cancel()
        self._close_window()

    def _capture_worker(self, generation: int) -> None:
        try:
            screenshot = self._session.finish(self._capture.capture_region)
        except Exception as exc:
            AppHelper.callAfter(self._on_error, generation, exc)
            return
        snapshot = self._session.snapshot()
        if snapshot.generation != generation:
            return
        AppHelper.callAfter(self._on_selected, generation, snapshot.selection, screenshot)

    def _close_window(self) -> None:
        if self._window is not None:
            self._window.orderOut_(None)
            self._window.close()
            self._window = None

    def _redraw(self) -> None:
        if self._window is not None:
            self._window.contentView().setNeedsDisplay_(True)

    def _screen_at(self, point):
        for screen in self._screen_provider():
            if NSPointInRect(point, screen.frame()):
                return screen
        return None


__all__ = ["RegionSelectionOverlayController"]
