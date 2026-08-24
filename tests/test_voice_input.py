from __future__ import annotations

import pytest

from ai_drive.voice import (
    VoiceInputCoordinator,
    VoiceInputError,
    VoiceSessionState,
    VoiceTranscript,
)


class FakeWorker:
    def __init__(self):
        self.started = []
        self.stopped = 0
        self.pcm = []

    def start(self, *, session_id):
        self.started.append(session_id)

    def send_pcm(self, chunk):
        self.pcm.append(chunk)

    def stop(self):
        self.stopped += 1


def test_disabled_voice_input_does_not_start_worker_or_accept_audio():
    worker = FakeWorker()
    coordinator = VoiceInputCoordinator(worker, enabled=False)

    with pytest.raises(VoiceInputError, match="disabled"):
        coordinator.start(10.0)
    with pytest.raises(VoiceInputError, match="disabled"):
        coordinator.send_pcm(b"audio")
    assert worker.started == []


def test_coordinator_stops_worker_on_cancel_and_forwards_only_worker_transcripts():
    worker = FakeWorker()
    received = []
    coordinator = VoiceInputCoordinator(worker, on_transcript=received.append)

    coordinator.start(20.0, session_id="voice-20")
    coordinator.send_pcm(b"pcm")
    assert worker.started == ["voice-20"]
    assert worker.pcm == [b"pcm"]

    event = VoiceTranscript("voice-20", 1, "partial", False, 20.2)
    coordinator.accept_worker_transcript(event)
    assert received == [event]

    coordinator.cancel()
    assert worker.stopped == 1
    assert coordinator.state is VoiceSessionState.CANCELLED


def test_finalization_requires_a_final_worker_event_before_completion():
    worker = FakeWorker()
    received = []
    coordinator = VoiceInputCoordinator(worker, on_transcript=received.append)
    coordinator.start(30.0, session_id="voice-30")
    coordinator.finish_recording(31.0)

    partial = VoiceTranscript("voice-30", 1, "暂时", False, 31.1)
    coordinator.accept_worker_transcript(partial)
    assert coordinator.state is VoiceSessionState.FINALIZING
    assert received == [partial]

    final = VoiceTranscript("voice-30", 2, "最终问题", True, 31.3)
    coordinator.accept_worker_transcript(final)
    assert coordinator.state is VoiceSessionState.COMPLETED
    assert worker.stopped == 1
