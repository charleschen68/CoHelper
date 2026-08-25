from types import SimpleNamespace

from apps.overlay.controller import OutputOverlayController


def test_overlay_controller_converts_panel_frame_to_logical_mask():
    frame = SimpleNamespace(
        origin=SimpleNamespace(x=12.0, y=34.0),
        size=SimpleNamespace(width=420.0, height=640.0),
    )

    mask = OutputOverlayController._mask_from_frame(frame)

    assert mask.x == 12.0
    assert mask.y == 34.0
    assert mask.width == 420.0
    assert mask.height == 640.0
