from ai_drive.output import (
    OutputEvent,
    OutputKind,
    OutputSeverity,
    OutputSource,
    OverlayModel,
)


def event(event_id, kind, message, *, generation=1, source=OutputSource.VOICE):
    return OutputEvent(
        event_id=event_id,
        kind=kind,
        source=source,
        occurred_at=10.0,
        title="语音",
        message=message,
        severity=OutputSeverity.INFO,
        generation=generation,
        metadata={},
    )


def test_partial_transcript_replaces_the_draft_and_only_final_text_enters_the_timeline():
    model = OverlayModel(idle_timeout_seconds=12)

    first = model.apply(event("partial-1", OutputKind.TRANSCRIPT_PARTIAL, "刷新"), now=10)
    revised = model.apply(event("partial-2", OutputKind.TRANSCRIPT_PARTIAL, "刷新 Safari"), now=10.4)
    final = model.apply(event("final-1", OutputKind.TRANSCRIPT_FINAL, "刷新 Safari"), now=11)

    assert first.active_transcript == "刷新"
    assert first.entries == ()
    assert revised.active_transcript == "刷新 Safari"
    assert revised.entries == ()
    assert final.active_transcript == ""
    assert [(entry.kind, entry.message) for entry in final.entries] == [
        (OutputKind.TRANSCRIPT_FINAL, "刷新 Safari")
    ]
    assert final.visible is True


def test_answer_deltas_accumulate_per_generation_and_stale_work_cannot_overwrite_newer_work():
    model = OverlayModel()

    model.apply(event("answer-1", OutputKind.ANSWER_DELTA, "旧回答", generation=1), now=10)
    current = model.apply(event("answer-2", OutputKind.ANSWER_DELTA, "Flink 是", generation=2), now=11)
    stale = model.apply(event("answer-3", OutputKind.ANSWER_DELTA, "不应出现", generation=1), now=12)
    complete = model.apply(
        event("answer-4", OutputKind.ANSWER_FINAL, "Flink 是流处理框架。", generation=2),
        now=13,
    )
    late = model.apply(event("answer-5", OutputKind.ANSWER_DELTA, "迟到内容", generation=2), now=14)

    assert current.active_answer == "Flink 是"
    assert stale.active_answer == "Flink 是"
    assert complete.active_answer == ""
    assert late.active_answer == ""
    assert [(entry.kind, entry.message) for entry in complete.entries] == [
        (OutputKind.ANSWER_FINAL, "Flink 是流处理框架。")
    ]


def test_streaming_answer_buffer_has_a_hard_display_bound():
    model = OverlayModel(max_active_answer_chars=8)

    model.apply(event("answer-1", OutputKind.ANSWER_DELTA, "abcd", generation=1), now=1)
    model.apply(event("answer-2", OutputKind.ANSWER_DELTA, "efgh", generation=1), now=2)
    snapshot = model.apply(event("answer-3", OutputKind.ANSWER_DELTA, "ijkl", generation=1), now=3)

    assert snapshot.active_answer == "abcdefg…"


def test_closed_answer_generation_remains_closed_after_many_other_streams():
    model = OverlayModel()
    model.apply(
        event(
            "knowledge-final-1",
            OutputKind.ANSWER_FINAL,
            "完成",
            generation=1,
            source=OutputSource.KNOWLEDGE,
        ),
        now=1,
    )
    for generation in range(1, 300):
        model.apply(
            event(
                f"voice-final-{generation}",
                OutputKind.ANSWER_FINAL,
                "完成",
                generation=generation,
                source=OutputSource.VOICE,
            ),
            now=generation + 1,
        )

    late = model.apply(
        event(
            "knowledge-late-1",
            OutputKind.ANSWER_DELTA,
            "LATE",
            generation=1,
            source=OutputSource.KNOWLEDGE,
        ),
        now=500,
    )

    assert late.active_answer == ""


def test_timeline_is_bounded_and_duplicate_events_are_idempotent():
    model = OverlayModel(max_entries=2)

    model.apply(event("final-1", OutputKind.TRANSCRIPT_FINAL, "第一条"), now=1)
    model.apply(event("final-2", OutputKind.TRANSCRIPT_FINAL, "第二条"), now=2)
    model.apply(event("final-2", OutputKind.TRANSCRIPT_FINAL, "不应重复"), now=3)
    snapshot = model.apply(event("final-3", OutputKind.TRANSCRIPT_FINAL, "第三条"), now=4)

    assert [entry.event_id for entry in snapshot.entries] == ["final-2", "final-3"]
    assert [entry.message for entry in snapshot.entries] == ["第二条", "第三条"]


def test_idle_overlay_hides_but_emergency_state_remains_until_explicitly_cleared():
    model = OverlayModel(max_entries=2, idle_timeout_seconds=12)
    model.apply(event("final-1", OutputKind.TRANSCRIPT_FINAL, "普通内容"), now=10)

    assert model.tick(now=21.9).visible is True
    assert model.tick(now=22).visible is False

    stopped = model.apply(event("stop-1", OutputKind.EMERGENCY_STOP, "紧急停止"), now=30)
    model.apply(event("final-2", OutputKind.TRANSCRIPT_FINAL, "后续一"), now=31)
    model.apply(event("final-3", OutputKind.TRANSCRIPT_FINAL, "后续二"), now=32)
    displaced = model.apply(event("final-4", OutputKind.TRANSCRIPT_FINAL, "后续三"), now=33)
    still_stopped = model.tick(now=300)
    cleared = model.apply(
        event("clear-1", OutputKind.EMERGENCY_CLEARED, "已手动恢复", generation=2),
        now=301,
    )

    assert stopped.sticky is True
    assert displaced.emergency_event.event_id == "stop-1"
    assert still_stopped.visible is True
    assert cleared.sticky is False
    assert cleared.emergency_event is None
    assert model.tick(now=313).visible is False


def test_emergency_revision_prevents_out_of_order_clear_and_stop_events():
    model = OverlayModel()

    stopped = model.apply(
        event("stop-2", OutputKind.EMERGENCY_STOP, "较新的停止", generation=2),
        now=10,
    )
    stale_clear = model.apply(
        event("clear-1", OutputKind.EMERGENCY_CLEARED, "迟到的旧恢复", generation=1),
        now=11,
    )
    cleared = model.apply(
        event("clear-3", OutputKind.EMERGENCY_CLEARED, "新的恢复", generation=3),
        now=12,
    )
    stale_stop = model.apply(
        event("stop-2-late", OutputKind.EMERGENCY_STOP, "迟到的旧停止", generation=2),
        now=13,
    )

    assert stopped.sticky is True
    assert stale_clear.sticky is True
    assert stale_clear.emergency_event.event_id == "stop-2"
    assert cleared.sticky is False
    assert stale_stop.sticky is False
    assert [entry.event_id for entry in stale_stop.entries] == ["stop-2", "clear-3"]


def test_action_errors_remain_visible_for_at_least_twenty_seconds():
    model = OverlayModel(idle_timeout_seconds=12, action_error_timeout_seconds=20)
    action_error = OutputEvent(
        event_id="action-error-1",
        kind=OutputKind.ACTION,
        source=OutputSource.ACTIONS,
        occurred_at=10,
        title="操作失败",
        message="目标已移动",
        severity=OutputSeverity.ERROR,
        generation=None,
        metadata={},
    )

    model.apply(action_error, now=10)

    assert model.tick(now=29.9).visible is True
    assert model.tick(now=30).visible is False
