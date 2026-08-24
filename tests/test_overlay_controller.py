from ai_drive.output import (
    OutputEvent,
    OutputKind,
    OutputSeverity,
    OutputSource,
    OverlayModel,
)
from apps.overlay.controller import OutputOverlayController, _utf16_length


def event(event_id, kind, message, *, generation=None):
    return OutputEvent(
        event_id=event_id,
        kind=kind,
        source=OutputSource.SYSTEM,
        occurred_at=10,
        title="状态",
        message=message,
        severity=OutputSeverity.INFO,
        generation=generation,
        metadata={},
    )


def test_renderer_keeps_one_explicit_emergency_message_after_timeline_eviction():
    model = OverlayModel(max_entries=2)
    model.apply(
        event("stop-1", OutputKind.EMERGENCY_STOP, "所有动作已停止", generation=1),
        now=1,
    )
    model.apply(event("status-1", OutputKind.STATUS, "后续一"), now=2)
    model.apply(event("status-2", OutputKind.STATUS, "后续二"), now=3)
    snapshot = model.apply(event("status-3", OutputKind.STATUS, "后续三"), now=4)

    rendered = OutputOverlayController._render(snapshot)

    assert rendered.count("紧急停止\n所有动作已停止") == 1
    assert "后续三" in rendered


def test_renderer_removes_evicted_emergency_message_after_explicit_clear():
    model = OverlayModel(max_entries=2)
    model.apply(
        event("stop-1", OutputKind.EMERGENCY_STOP, "所有动作已停止", generation=1),
        now=1,
    )
    model.apply(event("status-1", OutputKind.STATUS, "后续一"), now=2)
    model.apply(event("status-2", OutputKind.STATUS, "后续二"), now=3)
    cleared = model.apply(
        event("clear-1", OutputKind.EMERGENCY_CLEARED, "已手动恢复", generation=2),
        now=4,
    )

    rendered = OutputOverlayController._render(cleared)

    assert "所有动作已停止" not in rendered
    assert "安全状态\n已手动恢复" in rendered


def test_appkit_ranges_count_utf16_code_units_instead_of_python_code_points():
    assert len("A😀B") == 3
    assert _utf16_length("A😀B") == 4
