import pytest

from ai_drive.region_capture import RegionSelectionError
from ai_drive.region_selection import (
    RegionSelectionSession,
    RegionSelectionState,
)
from ai_drive.vision import Screenshot


def capture(selection):
    return Screenshot(
        b"cropped",
        600,
        400,
        selection.width,
        selection.height,
        selection.display_id,
        10.0,
        "app",
        selection.x,
        selection.y,
    )


def test_begin_is_explicit_and_does_not_capture_until_finish():
    session = RegionSelectionSession()
    generation = session.begin(7, (100, 50), (600, 400))

    assert generation == 1
    assert session.snapshot().state is RegionSelectionState.SELECTING
    assert session.snapshot().screenshot is None
    session.update_drag((200, 100), (400, 250))
    assert session.snapshot().screenshot is None
    result = session.finish(capture)
    assert result.display_id == 7
    assert session.snapshot().state is RegionSelectionState.CAPTURED


def test_escape_cancels_and_prevents_late_capture():
    session = RegionSelectionSession()
    session.begin(7, (100, 50), (600, 400))
    session.update_drag((200, 100), (400, 250))

    assert session.cancel() is True
    assert session.snapshot().state is RegionSelectionState.CANCELLED
    with pytest.raises(RuntimeError, match="not active"):
        session.finish(capture)


def test_retrigger_replaces_old_session_and_invalid_drag_never_captures():
    session = RegionSelectionSession()
    session.begin(7, (100, 50), (600, 400))
    session.update_drag((200, 100), (400, 250))
    generation = session.begin(8, (0, 0), (600, 400))
    assert generation == 2
    with pytest.raises(RegionSelectionError):
        session.update_drag((10, 10), (100, 50))
    assert session.snapshot().state is RegionSelectionState.SELECTING
    assert session.snapshot().screenshot is None


def test_capture_must_return_the_selected_display():
    session = RegionSelectionSession()
    session.begin(7, (100, 50), (600, 400))
    session.update_drag((200, 100), (400, 250))
    wrong_display = lambda selection: Screenshot(b"x", 1, 1, 1, 1, 8, 10.0, "app")

    with pytest.raises(RegionSelectionError, match="another display"):
        session.finish(wrong_display)
    assert session.snapshot().state is RegionSelectionState.FAILED
