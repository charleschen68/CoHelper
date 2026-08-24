"""Bounded PCM and supervised local STT seams for Phase 2."""

from __future__ import annotations

import json
import base64
import io
import math
import struct
import subprocess
import threading
import time
import wave
from pathlib import Path
from collections import deque
from dataclasses import dataclass
from typing import Callable

import requests

from .session import VoiceTranscript


class VoiceWorkerError(RuntimeError):
    """Raised or delivered when the local STT worker is unavailable or invalid."""


class PcmRingBuffer:
    def __init__(self, *, max_bytes: int):
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self._max_bytes = max_bytes
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._lock = threading.Lock()

    @property
    def size_bytes(self) -> int:
        with self._lock:
            return self._size

    def append(self, chunk: bytes) -> None:
        if not isinstance(chunk, bytes):
            raise TypeError("PCM chunk must be bytes")
        if len(chunk) > self._max_bytes:
            chunk = chunk[-self._max_bytes :]
        with self._lock:
            self._chunks.append(chunk)
            self._size += len(chunk)
            while self._size > self._max_bytes:
                removed = self._chunks.popleft()
                self._size -= len(removed)

    def read(self) -> bytes:
        with self._lock:
            result = b"".join(self._chunks)
            self._chunks.clear()
            self._size = 0
            return result


class VoiceActivityDetector:
    """Energy VAD; it only reports boundaries and never submits text."""

    def __init__(self, *, sample_rate: int, threshold: int, silence_seconds: float):
        if sample_rate <= 0 or threshold < 0 or silence_seconds <= 0:
            raise ValueError("invalid VAD configuration")
        self._sample_rate = sample_rate
        self._threshold = threshold
        self._silence_samples = int(sample_rate * silence_seconds)
        self._in_speech = False
        self._silent_samples = 0

    def accept(self, pcm: bytes) -> str | None:
        if len(pcm) % 2:
            raise ValueError("16-bit PCM must have an even byte length")
        if not pcm:
            return None
        samples = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)
        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
        if rms >= self._threshold:
            was_silent = not self._in_speech
            self._in_speech = True
            self._silent_samples = 0
            return "speech_started" if was_silent else None
        if not self._in_speech:
            return None
        self._silent_samples += len(samples)
        if self._silent_samples >= self._silence_samples:
            self._in_speech = False
            self._silent_samples = 0
            return "speech_silence"
        return None


@dataclass(frozen=True)
class VoiceWorkerConfig:
    command: tuple[str, ...]
    sample_rate: int = 16_000
    channels: int = 1
    max_pcm_bytes: int = 16_000 * 2 * 10 * 60

    def __post_init__(self):
        if not self.command or not all(isinstance(item, str) and item for item in self.command):
            raise ValueError("worker command must be a non-empty string tuple")
        if self.sample_rate != 16_000 or self.channels != 1:
            raise ValueError("voice worker input must be 16 kHz mono PCM")
        if self.max_pcm_bytes <= 0:
            raise ValueError("max_pcm_bytes must be positive")


@dataclass(frozen=True)
class WhisperCppWorkerConfig:
    executable: str
    model_path: str
    port: int = 18080
    language: str = "auto"
    max_pcm_bytes: int = 16_000 * 2 * 10 * 60

    def __post_init__(self):
        if not self.executable.strip() or not self.model_path.strip():
            raise ValueError("whisper executable and model path are required")
        if not 1024 <= self.port <= 65535:
            raise ValueError("whisper server port is invalid")
        if self.max_pcm_bytes <= 0:
            raise ValueError("max_pcm_bytes must be positive")


class WhisperCppWorker:
    """Run whisper.cpp locally and transcribe one bounded recording in memory."""

    def __init__(
        self,
        config: WhisperCppWorkerConfig,
        on_transcript: Callable[[VoiceTranscript], None],
        *,
        on_error: Callable[[VoiceWorkerError], None] | None = None,
    ):
        self._config = config
        self._on_transcript = on_transcript
        self._on_error = on_error or (lambda _error: None)
        self._process: subprocess.Popen[bytes] | None = None
        self._buffer = PcmRingBuffer(max_bytes=config.max_pcm_bytes)
        self._session_id = ""
        self._lock = threading.Lock()
        self._finalizing = False

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def start(self, *, session_id: str = "voice") -> None:
        with self._lock:
            if self.is_running:
                raise RuntimeError("whisper worker is already running")
            if not Path(self._config.model_path).is_file():
                raise VoiceWorkerError("Whisper model file is missing")
            try:
                process = subprocess.Popen(
                    [
                        self._config.executable,
                        "-m",
                        self._config.model_path,
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(self._config.port),
                        "-l",
                        self._config.language,
                        "-nt",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                raise VoiceWorkerError(f"failed to start whisper.cpp: {type(exc).__name__}") from exc
            self._process = process
            self._session_id = session_id
            self._buffer.read()
            self._finalizing = False
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not self.is_running:
                self._process = None
                raise VoiceWorkerError("whisper.cpp server exited during startup")
            try:
                requests.get(self._url("/"), timeout=0.25)
                return
            except requests.RequestException:
                time.sleep(0.1)
        self.stop()
        raise VoiceWorkerError("whisper.cpp server readiness timeout")

    def send_pcm(self, pcm: bytes) -> None:
        if not self.is_running:
            raise VoiceWorkerError("whisper worker is not running")
        self._buffer.append(pcm)

    def finish(self) -> None:
        with self._lock:
            if self._finalizing:
                return
            if not self.is_running:
                raise VoiceWorkerError("whisper worker is not running")
            self._finalizing = True
            pcm = self._buffer.read()
            session_id = self._session_id
        threading.Thread(
            target=self._transcribe,
            args=(session_id, pcm),
            name="cohelper-whisper-inference",
            daemon=True,
        ).start()

    def stop(self) -> None:
        process, self._process = self._process, None
        self._buffer.read()
        self._finalizing = False
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)

    def _transcribe(self, session_id: str, pcm: bytes) -> None:
        try:
            if not pcm:
                raise VoiceWorkerError("recording contains no PCM")
            wav = _pcm_to_wav(pcm)
            response = requests.post(
                self._url("/inference"),
                files={"file": ("voice.wav", wav, "audio/wav")},
                data={
                    "response_format": "json",
                    "temperature": "0.0",
                    "temperature_inc": "0.0",
                },
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
            text = payload.get("text") if isinstance(payload, dict) else None
            if not isinstance(text, str) or not text.strip():
                raise VoiceWorkerError("Whisper returned an empty transcript")
            self._on_transcript(VoiceTranscript(session_id, 1, " ".join(text.split()), True, time.time()))
        except (OSError, ValueError, requests.RequestException, VoiceWorkerError) as exc:
            self._report_error(VoiceWorkerError(f"Whisper transcription failed: {type(exc).__name__}"))

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._config.port}{path}"

    def _report_error(self, error: VoiceWorkerError) -> None:
        self._finalizing = False
        self._on_error(error)


def _pcm_to_wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(pcm)
    return output.getvalue()


class VoiceWorker:
    """Supervise a line-oriented local STT adapter over stdin/stdout.

    The adapter receives versioned JSON frames containing base64-encoded PCM
    and emits versioned JSON transcript lines. Explicit frames keep PCM and
    finalization control unambiguous; it cannot write audio or transcript data
    to disk through this interface.
    """

    def __init__(
        self,
        config: VoiceWorkerConfig,
        on_transcript: Callable[[VoiceTranscript], None],
        *,
        on_error: Callable[[VoiceWorkerError], None] | None = None,
    ):
        self._config = config
        self._on_transcript = on_transcript
        self._on_error = on_error or (lambda _error: None)
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._session_id = ""

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def start(self, *, session_id: str = "voice") -> None:
        with self._lock:
            if self.is_running:
                raise RuntimeError("voice worker is already running")
            try:
                process = subprocess.Popen(
                    self._config.command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                raise VoiceWorkerError(f"failed to start voice worker: {type(exc).__name__}") from exc
            self._process = process
            self._session_id = session_id
            self._stopping.clear()
            self._reader = threading.Thread(target=self._read_output, name="cohelper-voice-worker", daemon=True)
            self._reader.start()
            self._write_line({"version": 1, "kind": "start", "session_id": session_id})

    def send_pcm(self, pcm: bytes) -> None:
        if len(pcm) > self._config.max_pcm_bytes:
            raise VoiceWorkerError("PCM chunk exceeds bounded worker input")
        process = self._process
        if process is None or process.stdin is None or not self.is_running:
            raise VoiceWorkerError("voice worker is not running")
        try:
            self._write_line(
                {"version": 1, "kind": "pcm", "data": base64.b64encode(pcm).decode("ascii")},
                process=process,
            )
        except OSError as exc:
            self._report_error(VoiceWorkerError(f"voice worker input failed: {type(exc).__name__}"))

    def finish(self) -> None:
        """Tell the adapter that capture ended while keeping stdout alive for final text."""
        process = self._process
        if process is None or process.stdin is None or not self.is_running:
            raise VoiceWorkerError("voice worker is not running")
        try:
            self._write_line({"version": 1, "kind": "finalize"}, process=process)
        except OSError as exc:
            self._report_error(VoiceWorkerError(f"voice worker finalization failed: {type(exc).__name__}"))

    def stop(self) -> None:
        self._stopping.set()
        process, reader = self._process, self._reader
        self._process = None
        self._reader = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                self._close_stdin(process)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        finally:
            if reader is not None and reader is not threading.current_thread():
                reader.join(timeout=1)

    def _write_line(self, value: dict, *, process=None) -> None:
        process = process or self._process
        if process is None or process.stdin is None:
            return
        process.stdin.write((json.dumps(value, separators=(",", ":")) + "\n").encode())
        process.stdin.flush()

    @staticmethod
    def _close_stdin(process: subprocess.Popen[bytes]) -> None:
        stdin = process.stdin
        if stdin is None:
            return
        try:
            stdin.close()
        except OSError:
            # A worker that has already exited may leave BufferedWriter's
            # flush path broken; detach the wrapper so its finalizer cannot
            # retry the failed write.
            try:
                stdin.detach().close()
            except (OSError, ValueError):
                pass

    def _read_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for raw in iter(process.stdout.readline, b""):
            if self._stopping.is_set():
                break
            try:
                payload = json.loads(raw.decode("utf-8"))
                event = self._parse_event(payload)
                self._on_transcript(event)
            except (ValueError, UnicodeError, VoiceWorkerError) as exc:
                self._report_error(VoiceWorkerError(f"invalid voice worker output: {exc}"))
        if not self._stopping.is_set() and process.poll() not in (None, 0):
            self._report_error(VoiceWorkerError("voice worker exited unexpectedly"))

    def _parse_event(self, payload: object) -> VoiceTranscript:
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise VoiceWorkerError("unsupported worker event")
        kind = payload.get("kind")
        if kind not in {"partial", "final"}:
            raise VoiceWorkerError("unsupported transcript kind")
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise VoiceWorkerError("worker transcript is empty")
        return VoiceTranscript(
            self._session_id,
            int(payload.get("sequence", 0)),
            " ".join(text.split()),
            kind == "final",
            float(payload.get("occurred_at", 0.0)),
        )

    def _report_error(self, error: VoiceWorkerError) -> None:
        try:
            self._on_error(error)
        except Exception:
            pass
