from AppKit import NSApplication

import ai_drive.region_translation_panel_appkit as panel_appkit
from ai_drive.region_capture import RegionSelection
from ai_drive.region_translation_panel import RegionTranslationPanelSnapshot, RegionTranslationView
from ai_drive.region_translation_panel_appkit import RegionTranslationPanelController


class Runtime:
    def close(self):
        pass


def panel_snapshot():
    return RegionTranslationPanelSnapshot(
        generation=1,
        selection=RegionSelection(1, 100.0, 100.0, 360.0, 220.0),
        source_image=b"",
        recognized_text=None,
        detected_language=None,
        target=None,
        translated_text=None,
        active_view=RegionTranslationView.ORIGINAL,
        retry_available=False,
    )


def test_show_builds_a_real_visible_appkit_panel():
    NSApplication.sharedApplication()
    controller = RegionTranslationPanelController.alloc().initWithRuntime_onError_(Runtime(), None)

    try:
        controller.show(panel_snapshot())

        assert controller._panel is not None
        assert controller._panel.isVisible()
    finally:
        controller.close()


def test_presented_result_panel_is_key_and_activates_accessory_app(monkeypatch):
    calls = []

    class Panel:
        def setHidesOnDeactivate_(self, value):
            calls.append(("hides", value))

        def makeKeyAndOrderFront_(self, value):
            calls.append(("key", value))

        def orderFrontRegardless(self):
            calls.append(("front",))

    class Application:
        def activateIgnoringOtherApps_(self, value):
            calls.append(("activate", value))

    monkeypatch.setattr(panel_appkit, "NSApp", lambda: Application())
    RegionTranslationPanelController._present_panel(Panel())

    assert calls == [
        ("hides", False),
        ("key", None),
        ("front",),
        ("activate", True),
    ]
