import ai_drive.region_translation_panel_appkit as panel_appkit
from ai_drive.region_translation_panel_appkit import RegionTranslationPanelController


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
