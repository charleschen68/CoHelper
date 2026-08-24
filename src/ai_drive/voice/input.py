"""Feature-gated orchestration between audio capture and the STT worker."""

from __future__ import annotations

from collections.abc import Callable

from .session import VoiceSession, VoiceSessionError, VoiceSessionState, VoiceTranscript


class VoiceInputError(RuntimeError):
    """Raised when voice input cannot accept a lifecycle or audio operation."""


class VoiceInputCoordinator:
    def __init__(
        self,
        worker,
        *,
        enabled: bool = True,
        on_transcript: Callable[[VoiceTranscript], None] | None = None,
    ):
        self._worker = worker
        self._enabled = bool(enabled)
        self._on_transcript = on_transcript or (lambda _event: None)
        self._session: VoiceSession | None = None

    @property
    def state(self) -> VoiceSessionState | None:
        return self._session.state if self._session is not None else None

    @property
    def session_id(self) -> str | None:
        return self._session.session_id if self._session is not None else None

    def start(self, started_at: float, *, session_id: str = "voice", long_input: bool = False) -> None:
        self._require_enabled()
        if self._session is not None and self._session.state in {
            VoiceSessionState.LISTENING,
            VoiceSessionState.FINALIZING,
        }:
            raise VoiceInputError("voice input is already active")
        session = VoiceSession(session_id=session_id, started_at=started_at, long_input=long_input)
        try:
            self._worker.start(session_id=session_id)
        except Exception as exc:
            session.cancel()
            raise VoiceInputError(f"voice worker unavailable: {type(exc).__name__}") from exc
        self._session = session

    def send_pcm(self, chunk: bytes) -> None:
        self._require_active()
        try:
            self._worker.send_pcm(chunk)
        except Exception as exc:
            self.cancel()
            raise VoiceInputError(f"voice audio unavailable: {type(exc).__name__}") from exc

    def finish_recording(self, occurred_at: float) -> None:
        session = self._require_active()
        try:
            session.finalize(occurred_at)
        except VoiceSessionError as exc:
            raise VoiceInputError(str(exc)) from exc
        finish = getattr(self._worker, "finish", None)
        if finish is not None:
            try:
                finish()
            except Exception as exc:
                self.cancel()
                raise VoiceInputError(f"voice worker finalization failed: {type(exc).__name__}") from exc

    def accept_worker_transcript(self, event: VoiceTranscript) -> None:
        session = self._require_active()
        if event.session_id != session.session_id:
            raise VoiceInputError("transcript belongs to another voice session")
        try:
            if event.finalized:
                accepted = session.accept_final(event.text, occurred_at=event.occurred_at)
            else:
                accepted = session.accept_partial(event.text, occurred_at=event.occurred_at)
        except VoiceSessionError as exc:
            raise VoiceInputError(str(exc)) from exc
        self._on_transcript(accepted)
        if accepted.finalized:
            self._worker.stop()

    def cancel(self) -> None:
        if self._session is None:
            return
        self._session.cancel()
        self._worker.stop()

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise VoiceInputError("voice input is disabled")

    def _require_active(self) -> VoiceSession:
        self._require_enabled()
        if self._session is None or self._session.state not in {
            VoiceSessionState.LISTENING,
            VoiceSessionState.FINALIZING,
        }:
            raise VoiceInputError("no active voice session")
        return self._session
