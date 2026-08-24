"""App-owned AVAudioEngine microphone capture.

The adapter exposes only transient 16-bit mono PCM chunks to its caller. It
does not write audio to disk and refuses to start when the requested format
cannot be established.
"""

from __future__ import annotations

from collections.abc import Callable

try:  # Importing the pure core must remain possible in non-macOS test runs.
    from AVFoundation import AVAudioApplication, AVAudioEngine
except ImportError:  # pragma: no cover - exercised only outside macOS
    AVAudioApplication = None
    AVAudioEngine = None


class MicrophoneCaptureError(RuntimeError):
    """Raised when the app cannot own or start the microphone."""


class MacMicrophoneCapture:
    SAMPLE_RATE = 16_000
    CHANNELS = 1
    BUFFER_FRAMES = 4_096

    def __init__(self, on_pcm: Callable[[bytes], None], *, on_error: Callable[[Exception], None] | None = None):
        self._on_pcm = on_pcm
        self._on_error = on_error or (lambda _error: None)
        self._engine = None
        self._input_node = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            raise MicrophoneCaptureError("microphone capture is already running")
        if AVAudioEngine is None:
            raise MicrophoneCaptureError("AVAudioEngine is unavailable")
        try:
            engine = AVAudioEngine.alloc().init()
            input_node = engine.inputNode()
            format_ = input_node.outputFormatForBus_(0)
            if int(format_.channelCount()) != self.CHANNELS:
                raise MicrophoneCaptureError("microphone must provide one channel")
            if round(float(format_.sampleRate())) != self.SAMPLE_RATE:
                raise MicrophoneCaptureError("microphone input must be 16 kHz; resampling is not enabled")
            self._engine = engine
            self._input_node = input_node
            input_node.installTapOnBus_bufferSize_format_block_(
                0,
                self.BUFFER_FRAMES,
                format_,
                self._receive_buffer,
            )
            engine.prepare()
            started = engine.startAndReturnError_(None)
            if isinstance(started, tuple) and not started[0]:
                raise MicrophoneCaptureError("AVAudioEngine refused to start")
        except MicrophoneCaptureError:
            self.stop()
            raise
        except Exception as exc:
            self.stop()
            raise MicrophoneCaptureError(f"microphone start failed: {type(exc).__name__}") from exc
        self._running = True

    def request_permission(self, callback: Callable[[bool], None]) -> None:
        if AVAudioApplication is None:
            callback(False)
            return
        try:
            AVAudioApplication.sharedInstance().requestRecordPermissionWithCompletionHandler_(callback)
        except Exception as exc:
            self._on_error(MicrophoneCaptureError(f"microphone permission failed: {type(exc).__name__}"))
            callback(False)

    def stop(self) -> None:
        input_node, engine = self._input_node, self._engine
        self._input_node = None
        self._engine = None
        self._running = False
        if input_node is not None:
            try:
                input_node.removeTapOnBus_(0)
            except Exception:
                pass
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass

    def _receive_buffer(self, buffer, _when) -> None:
        if not self._running:
            return
        try:
            # The macOS input node's native format is normally Float32. The
            # conversion is explicit and clipped before exposing int16 PCM.
            frames = int(buffer.frameLength())
            channels = buffer.floatChannelData()
            if frames <= 0 or channels is None:
                return
            channel = channels[0]
            values = []
            for index in range(frames):
                sample = max(-1.0, min(1.0, float(channel[index])))
                values.append(int(sample * 32767.0))
            import struct

            self._on_pcm(struct.pack("<" + "h" * len(values), *values))
        except Exception as exc:
            self._on_error(MicrophoneCaptureError(f"microphone buffer failed: {type(exc).__name__}"))
            self.stop()
