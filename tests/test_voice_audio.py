from __future__ import annotations

import json
import struct
import sys
import threading
import time

import pytest

from ai_drive.voice import (
    PcmRingBuffer,
    VoiceActivityDetector,
    VoiceWorker,
    VoiceWorkerConfig,
    VoiceWorkerError,
)


def pcm(value: int, samples: int) -> bytes:
    return struct.pack("<" + "h" * samples, *([value] * samples))


def test_pcm_ring_buffer_is_bounded_and_drops_oldest_frames():
    ring = PcmRingBuffer(max_bytes=8)

    ring.append(b"1234")
    ring.append(b"5678")
    ring.append(b"90ab")

    assert ring.size_bytes == 8
    assert ring.read() == b"567890ab"
    assert ring.read() == b""


def test_vad_reports_speech_and_silence_without_submitting_a_query():
    vad = VoiceActivityDetector(sample_rate=100, threshold=500, silence_seconds=0.2)

    assert vad.accept(pcm(0, 10)) is None
    assert vad.accept(pcm(1000, 10)) == "speech_started"
    assert vad.accept(pcm(1000, 10)) is None
    assert vad.accept(pcm(0, 20)) == "speech_silence"


def test_worker_accepts_only_versioned_transcript_lines_and_stops_child():
    code = (
        "import json,sys; "
        "json.loads(sys.stdin.readline()); "
        "print(json.dumps({'version':1,'kind':'partial','text':'你好'}), flush=True); "
        "sys.stdin.readline()"
    )
    received = []
    worker = VoiceWorker(
        VoiceWorkerConfig(command=(sys.executable, "-u", "-c", code)),
        received.append,
    )

    worker.start()
    try:
        worker.send_pcm(pcm(100, 2))
        assert _wait_until(lambda: len(received) == 1)
        assert received[0].text == "你好"
        assert received[0].finalized is False
    finally:
        worker.stop()

    assert worker.is_running is False


def test_worker_rejects_invalid_output_without_crashing_the_caller():
    code = "import sys; print('not json', flush=True); sys.stdin.readline()"
    errors = []
    worker = VoiceWorker(
        VoiceWorkerConfig(command=(sys.executable, "-u", "-c", code)),
        lambda _event: None,
        on_error=errors.append,
    )

    worker.start()
    try:
        worker.send_pcm(b"xx")
        assert _wait_until(lambda: errors)
        assert isinstance(errors[0], VoiceWorkerError)
    finally:
        worker.stop()


def _wait_until(predicate):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
