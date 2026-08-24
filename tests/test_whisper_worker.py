from __future__ import annotations

import wave
import threading
import time
from io import BytesIO

import pytest

from ai_drive.voice import WhisperCppWorker, WhisperCppWorkerConfig
from ai_drive.voice.audio import _pcm_to_wav


def test_whisper_cpp_worker_config_requires_a_local_server_and_valid_port():
    config = WhisperCppWorkerConfig("whisper-server", "/tmp/model.bin")
    assert config.language == "auto"

    with pytest.raises(ValueError, match="port"):
        WhisperCppWorkerConfig("whisper-server", "/tmp/model.bin", port=80)


def test_pcm_is_encoded_as_16khz_mono_wav_in_memory():
    payload = _pcm_to_wav(b"\x00\x00" * 160)

    with wave.open(BytesIO(payload), "rb") as wav:
        assert wav.getframerate() == 16_000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == 160


def test_whisper_worker_limits_partial_requests_and_emits_final(monkeypatch, tmp_path):
    class Process:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"text": "partial" if calls == 1 else "final"}

    calls = 0

    def post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return Response()

    monkeypatch.setattr("ai_drive.voice.audio.subprocess.Popen", lambda *_args, **_kwargs: Process())
    monkeypatch.setattr("ai_drive.voice.audio.requests.get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr("ai_drive.voice.audio.requests.post", post)
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    events = []
    received = threading.Event()
    worker = WhisperCppWorker(
        WhisperCppWorkerConfig("whisper-server", str(model)),
        lambda event: (events.append(event), received.set()),
    )

    worker.start(session_id="voice-partial")
    worker.send_pcm(b"\x00\x00" * 16_000)
    assert received.wait(1)
    worker.finish()
    deadline = time.monotonic() + 1
    while len(events) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.stop()

    assert [event.finalized for event in events] == [False, True]
    assert calls == 2
