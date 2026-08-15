import pytest

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
