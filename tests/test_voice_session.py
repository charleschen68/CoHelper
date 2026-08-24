from __future__ import annotations

import pytest

from ai_drive.voice import (
    VoiceSession,
    VoiceSessionError,
    VoiceSessionState,
    VoiceTranscript,
)


def test_push_to_talk_emits_partial_transcripts_but_only_release_accepts_final_text():
    session = VoiceSession(session_id="voice-1", started_at=10.0)

    assert session.state is VoiceSessionState.LISTENING
    partial = session.accept_partial("打开设置", occurred_at=10.4)
    assert partial == VoiceTranscript("voice-1", 1, "打开设置", False, 10.4)
    assert session.finalize(occurred_at=11.0) is None

    final = session.accept_final("打开设置，执行", occurred_at=11.2)
    assert final == VoiceTranscript("voice-1", 2, "打开设置，执行", True, 11.2)
    assert session.state is VoiceSessionState.COMPLETED


def test_partial_transcripts_are_not_available_after_cancel_or_completion():
    session = VoiceSession(session_id="voice-2", started_at=20.0)
    session.cancel()

    with pytest.raises(VoiceSessionError, match="not listening"):
        session.accept_partial("点它，执行", occurred_at=20.2)
    with pytest.raises(VoiceSessionError, match="not listening"):
        session.accept_final("点它，执行", occurred_at=20.3)


def test_push_to_talk_expires_at_sixty_seconds_and_long_session_has_ten_minute_cap():
    session = VoiceSession(session_id="voice-3", started_at=30.0)

    assert session.expired(89.9) is False
    assert session.expired(90.0) is True
    with pytest.raises(VoiceSessionError, match="expired"):
        session.accept_partial("太晚", occurred_at=90.0)

    long_session = VoiceSession(session_id="voice-4", started_at=30.0, long_input=True)
    assert long_session.expired(629.9) is False
    assert long_session.expired(630.0) is True


def test_empty_final_transcript_is_rejected_without_becoming_routable():
    session = VoiceSession(session_id="voice-5", started_at=40.0)
    session.finalize(41.0)

    with pytest.raises(VoiceSessionError, match="empty"):
        session.accept_final("  \n", occurred_at=41.1)
    assert session.state is VoiceSessionState.FINALIZING
