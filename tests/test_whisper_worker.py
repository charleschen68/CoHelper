from __future__ import annotations

import wave
from io import BytesIO

import pytest

from ai_drive.voice import WhisperCppWorkerConfig
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
