"""Explicit voice-session lifecycle and transcript boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


PUSH_TO_TALK_SECONDS = 60.0
LONG_INPUT_SECONDS = 10 * 60.0


class VoiceSessionError(RuntimeError):
    """Raised when an audio adapter attempts an invalid session transition."""


class VoiceSessionState(str, Enum):
    LISTENING = "listening"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True)
class VoiceTranscript:
    session_id: str
    sequence: int
    text: str
    finalized: bool
    occurred_at: float


class VoiceSession:
    """Own one explicit recording window; no transcript is persisted here."""

    def __init__(self, *, session_id: str, started_at: float, long_input: bool = False):
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        if started_at < 0:
            raise ValueError("started_at must not be negative")
        self.session_id = session_id
        self.started_at = float(started_at)
        self.long_input = bool(long_input)
        self.state = VoiceSessionState.LISTENING
        self._sequence = 0

    @property
    def deadline(self) -> float:
        return self.started_at + (LONG_INPUT_SECONDS if self.long_input else PUSH_TO_TALK_SECONDS)

    def expired(self, now: float) -> bool:
        return float(now) >= self.deadline

    def accept_partial(self, text: str, *, occurred_at: float) -> VoiceTranscript:
        if self.state not in {VoiceSessionState.LISTENING, VoiceSessionState.FINALIZING}:
            raise VoiceSessionError("voice session is not listening")
        if self.expired(occurred_at):
            self.state = VoiceSessionState.EXPIRED
            raise VoiceSessionError("voice session expired")
        normalized = _normalize_text(text)
        if not normalized:
            raise VoiceSessionError("partial transcript is empty")
        return self._event(normalized, finalized=False, occurred_at=occurred_at)

    def finalize(self, occurred_at: float) -> None:
        self._ensure_listening(occurred_at)
        self.state = VoiceSessionState.FINALIZING

    def accept_final(self, text: str, *, occurred_at: float) -> VoiceTranscript:
        if self.state not in {VoiceSessionState.LISTENING, VoiceSessionState.FINALIZING}:
            raise VoiceSessionError("voice session is not listening")
        if self.expired(occurred_at):
            self.state = VoiceSessionState.EXPIRED
            raise VoiceSessionError("voice session expired")
        normalized = _normalize_text(text)
        if not normalized:
            raise VoiceSessionError("final transcript is empty")
        event = self._event(normalized, finalized=True, occurred_at=occurred_at)
        self.state = VoiceSessionState.COMPLETED
        return event

    def cancel(self) -> None:
        if self.state in {VoiceSessionState.COMPLETED, VoiceSessionState.CANCELLED}:
            return
        self.state = VoiceSessionState.CANCELLED

    def _ensure_listening(self, occurred_at: float) -> None:
        if self.state is not VoiceSessionState.LISTENING:
            raise VoiceSessionError("voice session is not listening")
        if self.expired(occurred_at):
            self.state = VoiceSessionState.EXPIRED
            raise VoiceSessionError("voice session expired")

    def _event(self, text: str, *, finalized: bool, occurred_at: float) -> VoiceTranscript:
        self._sequence += 1
        return VoiceTranscript(self.session_id, self._sequence, text, finalized, float(occurred_at))


def _normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise VoiceSessionError("transcript must be text")
    return " ".join(text.split())
