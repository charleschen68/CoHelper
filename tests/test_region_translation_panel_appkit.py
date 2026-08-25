from ai_drive.region_translation_panel_appkit import RegionTranslationPanelController


def test_presented_result_panel_does_not_take_focus_or_hide_on_deactivation():
    calls = []

    class Panel:
        def setHidesOnDeactivate_(self, value):
            calls.append(("hides", value))

        def orderFrontRegardless(self):
            calls.append(("front",))

    RegionTranslationPanelController._present_panel(Panel())

    assert calls == [("hides", False), ("front",)]
