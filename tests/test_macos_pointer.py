import pytest

import ai_drive.macos as macos
from ai_drive.actions import AccessibilityCapability, AccessibleTarget
from ai_drive.macos import MacAccessibilityInspector
from ai_drive.macos import QuartzPointerController
from ai_drive.vision import ScreenPoint


def test_pointer_posts_nothing_when_complete_gesture_cannot_be_created():
    created = []
    posted = []

    def factory(source, event_type, point, button):
        del source, point, button
        created.append(event_type)
        return object() if len(created) == 1 else None

    pointer = QuartzPointerController(factory, lambda tap, event: posted.append((tap, event)))

    with pytest.raises(RuntimeError, match="complete Quartz mouse gesture"):
        pointer.click(ScreenPoint(10, 20))
    assert posted == []


def test_accessibility_center_unboxes_axvalue_geometry(monkeypatch):
    class Inspector(MacAccessibilityInspector):
        @staticmethod
        def _attribute(element, attribute):
            return element[attribute]

    position_attribute = object()
    size_attribute = object()
    monkeypatch.setattr(macos, "kAXPositionAttribute", position_attribute)
    monkeypatch.setattr(macos, "kAXSizeAttribute", size_attribute)
    monkeypatch.setattr(macos, "AXValueGetValue", lambda value, value_type, output: (True, value))

    point = Inspector._center_point(
        {
            position_attribute: (10, 20),
            size_attribute: (30, 40),
        }
    )

    assert point == ScreenPoint(25, 40)


def test_find_capability_only_walks_the_focused_window(monkeypatch):
    root = object()
    focused_window = object()
    button = object()

    class Application:
        def processIdentifier(self):
            return 42

    class Inspector(MacAccessibilityInspector):
        def has_permission(self):
            return True

        @staticmethod
        def _application_element(application):
            assert application.processIdentifier() == 42
            return root

        @staticmethod
        def _attribute(element, attribute):
            if element is root and attribute == macos.kAXFocusedWindowAttribute:
                return focused_window
            return None

        @staticmethod
        def _walk_elements(window):
            assert window is focused_window
            return iter((button,))

        @staticmethod
        def _target_for_element(element):
            assert element is button
            return AccessibleTarget("AXButton", "Reload this page", True, "com.apple.Safari", "", ("AXToolbar",))

        @staticmethod
        def _center_point(element):
            assert element is button
            return ScreenPoint(10, 20)

    class RunningApplications:
        @staticmethod
        def runningApplicationsWithBundleIdentifier_(bundle_id):
            return [Application()] if bundle_id == "com.apple.Safari" else []

    monkeypatch.setattr(macos, "NSRunningApplication", RunningApplications)

    located = Inspector().find_capability(
        AccessibilityCapability("com.apple.Safari", "AXButton", "Reload this page", "AXToolbar")
    )

    assert located is not None
    assert located.point == ScreenPoint(10, 20)


def test_accessibility_walk_deduplicates_repeated_nodes():
    root = object()
    repeated = object()
    target = object()

    class Inspector(MacAccessibilityInspector):
        @staticmethod
        def _attribute(element, attribute):
            if element is root:
                return [repeated] * 1024 + [target]
            return None

    assert list(Inspector._walk_elements(root, max_nodes=3)) == [root, repeated, target]
