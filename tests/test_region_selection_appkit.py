import pytest
from AppKit import NSMakeRect

import ai_drive.region_selection_appkit as selection_appkit
from ai_drive.region_selection_appkit import RegionSelectionOverlayController, _SelectionCanvas
from ai_drive.vision import Screenshot


class Capture:
    def __init__(self, permission):
        self.permission = permission

    def has_permission(self):
        return self.permission

    def capture_region(self, selection):
        return Screenshot(
            b"crop",
            int(selection.width),
            int(selection.height),
            selection.width,
            selection.height,
            selection.display_id,
            1.0,
            "app",
            selection.x,
            selection.y,
        )


def test_trigger_requires_screen_recording_before_creating_overlay():
    controller = RegionSelectionOverlayController(capture=Capture(False), screen_provider=lambda: [])

    with pytest.raises(PermissionError, match="Screen Recording"):
        controller.trigger()


def test_trigger_requires_a_display_under_the_pointer():
    controller = RegionSelectionOverlayController(
        capture=Capture(True), screen_provider=lambda: [], pointer_provider=lambda: None
    )

    with pytest.raises(RuntimeError, match="display"):
        controller.trigger()


def test_selection_canvas_forwards_drag_events_to_controller():
    class Controller:
        def __init__(self):
            self.events = []

        def mouse_down(self, point):
            self.events.append(("down", point))

        def mouse_dragged(self, point):
            self.events.append(("dragged", point))

        def mouse_up(self, point):
            self.events.append(("up", point))

    class Event:
        def __init__(self, point):
            self.point = type("Point", (), {"x": point[0], "y": point[1]})()

        def locationInWindow(self):
            return self.point

    class Frame:
        origin = type("Origin", (), {"x": 100.0, "y": 200.0})()

    class Window:
        def frame(self):
            return Frame()

    controller = Controller()
    canvas = _SelectionCanvas.alloc().initWithFrame_controller_(
        NSMakeRect(0, 0, 100, 100), controller
    )
    canvas.window = lambda: Window()

    _SelectionCanvas.mouseDown_(canvas, Event((10.0, 20.0)))
    _SelectionCanvas.mouseDragged_(canvas, Event((30.0, 40.0)))
    _SelectionCanvas.mouseUp_(canvas, Event((50.0, 60.0)))

    assert controller.events == [
        ("down", (110.0, 220.0)),
        ("dragged", (130.0, 240.0)),
        ("up", (150.0, 260.0)),
    ]


def test_presented_selection_window_stays_visible_after_app_deactivation():
    calls = []

    class Window:
        def setHidesOnDeactivate_(self, value):
            calls.append(("hides", value))

        def makeKeyAndOrderFront_(self, value):
            calls.append(("key", value))

        def orderFrontRegardless(self):
            calls.append(("front",))

    RegionSelectionOverlayController._present_window(Window())

    assert calls == [("hides", False), ("key", None), ("front",)]


def test_delayed_old_capture_worker_cannot_consume_new_selection(monkeypatch):
    selected = []
    errors = []
    monkeypatch.setattr(
        selection_appkit.AppHelper,
        "callAfter",
        lambda callback, *args: callback(*args),
    )
    controller = RegionSelectionOverlayController(
        capture=Capture(True),
        on_selected=lambda generation, selection, screenshot: selected.append(
            (generation, selection, screenshot)
        ),
        on_error=lambda generation, error: errors.append((generation, error)),
    )

    first = controller._session.begin(1, (0, 0), (1000, 800))
    controller._session.update_drag((100, 100), (300, 250))
    second = controller._session.begin(1, (0, 0), (1000, 800))
    second_selection = controller._session.update_drag((400, 300), (700, 500))

    controller._capture_worker(first)
    controller._capture_worker(second)

    assert errors == []
    assert [(generation, selection) for generation, selection, _screenshot in selected] == [
        (second, second_selection)
    ]
