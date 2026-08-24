"""Sentence buffering and local speech output for grounded answers."""

from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable


_SENTENCE_END = re.compile(r"[。！？!?；;\n]+")


class AnswerSentenceBuffer:
    """Keep at most a small queue of complete answer sentences per generation."""

    def __init__(self, *, max_pending: int = 1):
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self._max_pending = max_pending
        self._generation: int | None = None
        self._tail = ""
        self._pending: deque[tuple[int, str]] = deque()

    def feed(self, generation: int, delta: str) -> list[tuple[int, str]]:
        self._switch_generation(generation)
        if not isinstance(delta, str) or not delta:
            return []
        self._tail += delta
        return self._drain_complete()

    def finish(self, generation: int) -> list[tuple[int, str]]:
        self._switch_generation(generation)
        if not self._tail.strip():
            return []
        sentence = self._tail.strip()
        self._tail = ""
        return [(generation, sentence)]

    def clear(self) -> None:
        self._tail = ""
        self._pending.clear()
        self._generation = None

    def _switch_generation(self, generation: int) -> None:
        if not isinstance(generation, int) or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if self._generation != generation:
            self._generation = generation
            self._tail = ""
            self._pending.clear()

    def _drain_complete(self) -> list[tuple[int, str]]:
        emitted = []
        while True:
            match = _SENTENCE_END.search(self._tail)
            if match is None:
                break
            sentence = self._tail[: match.end()].strip()
            self._tail = self._tail[match.end() :]
            if sentence:
                self._pending.append((self._generation, sentence))
        while len(self._pending) > self._max_pending:
            self._pending.popleft()
        while self._pending:
            emitted.append(self._pending.popleft())
        return emitted


class SpeechOutputError(RuntimeError):
    pass


try:  # Keep pure tests importable outside macOS.
    from AVFoundation import AVSpeechSynthesizer, AVSpeechSynthesisVoice, AVSpeechUtterance
except ImportError:  # pragma: no cover - only used outside macOS
    AVSpeechSynthesizer = None
    AVSpeechSynthesisVoice = None
    AVSpeechUtterance = None


class MacSpeechOutput:
    """Interruptible local AVSpeechSynthesizer output with one pending sentence."""

    def __init__(self, *, on_error: Callable[[Exception], None] | None = None):
        self._on_error = on_error or (lambda _error: None)
        self._synthesizer = None
        self._generation = -1
        self._lock = threading.Lock()

    def speak(self, generation: int, sentence: str) -> None:
        if not sentence.strip():
            return
        if AVSpeechSynthesizer is None:
            raise SpeechOutputError("AVSpeechSynthesizer is unavailable")
        try:
            with self._lock:
                if self._synthesizer is None:
                    self._synthesizer = AVSpeechSynthesizer.alloc().init()
                if generation != self._generation:
                    self._generation = generation
                    self._synthesizer.stopSpeakingAtBoundary_(0)
                utterance = AVSpeechUtterance.alloc().initWithString_(sentence)
                voice = AVSpeechSynthesisVoice.voiceWithLanguage_("zh-CN")
                if voice is not None:
                    utterance.setVoice_(voice)
                self._synthesizer.speakUtterance_(utterance)
        except Exception as exc:
            error = SpeechOutputError(f"speech synthesis failed: {type(exc).__name__}")
            self._on_error(error)
            raise error from exc

    def interrupt(self) -> None:
        with self._lock:
            if self._synthesizer is not None:
                self._synthesizer.stopSpeakingAtBoundary_(0)
            self._generation = -1
