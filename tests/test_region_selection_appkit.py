import pytest

from ai_drive.region_selection_appkit import RegionSelectionOverlayController


class Capture:
    def __init__(self, permission):
        self.permission = permission

    def has_permission(self):
        return self.permission


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
