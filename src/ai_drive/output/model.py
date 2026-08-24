"""Platform-independent state model for the left-side output overlay."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .events import OutputEvent, OutputKind, OutputSeverity, OutputSource


@dataclass(frozen=True)
class AnswerStreamKey:
    source: OutputSource
    generation: int


@dataclass(frozen=True)
class OverlaySnapshot:
    entries: tuple[OutputEvent, ...]
    active_transcript: str
    active_answer: str
    emergency_event: OutputEvent | None
    visible: bool
    sticky: bool


class OverlayModel:
    def __init__(
        self,
        *,
        max_entries: int = 24,
        idle_timeout_seconds: float = 12,
        action_error_timeout_seconds: float = 20,
        max_active_answer_chars: int = 32_768,
    ):
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be positive")
        if action_error_timeout_seconds < idle_timeout_seconds:
            raise ValueError("action_error_timeout_seconds must be at least idle_timeout_seconds")
        if max_active_answer_chars < 2:
            raise ValueError("max_active_answer_chars must be at least 2")
        self._entries: deque[OutputEvent] = deque(maxlen=max_entries)
        self._seen_event_ids: set[str] = set()
        self._seen_event_order: deque[str] = deque()
        self._seen_event_limit = max(256, max_entries * 4)
        self._active_transcript = ""
        self._active_answer = ""
        self._max_active_answer_chars = max_active_answer_chars
        self._answer_stream: AnswerStreamKey | None = None
        self._latest_answer_generation: dict[OutputSource, int] = {}
        self._closed_answer_highwater: dict[OutputSource, int] = {}
        self._visible = False
        self._sticky = False
        self._emergency_event: OutputEvent | None = None
        self._emergency_kind: OutputKind | None = None
        self._latest_emergency_generation = -1
        self._idle_timeout_seconds = idle_timeout_seconds
        self._action_error_timeout_seconds = action_error_timeout_seconds
        self._visible_until = 0.0

    def apply(self, event: OutputEvent, *, now: float) -> OverlaySnapshot:
        if event.event_id in self._seen_event_ids:
            return self.snapshot()
        if event.kind in {OutputKind.ANSWER_DELTA, OutputKind.ANSWER_FINAL}:
            if event.generation is None:
                raise ValueError("answer events require a generation")
        self._remember(event.event_id)
        if event.kind in {OutputKind.EMERGENCY_STOP, OutputKind.EMERGENCY_CLEARED}:
            if not self._accept_emergency_event(event):
                return self.snapshot()
        if event.kind in {OutputKind.ANSWER_DELTA, OutputKind.ANSWER_FINAL}:
            assert event.generation is not None
            stream = AnswerStreamKey(event.source, event.generation)
            latest = self._latest_answer_generation.get(event.source)
            closed = self._closed_answer_highwater.get(event.source)
            if (closed is not None and event.generation <= closed) or (
                latest is not None and event.generation < latest
            ):
                return self.snapshot()
            if self._answer_stream != stream:
                if self._answer_stream is not None:
                    self._close_answer_stream(self._answer_stream)
                self._answer_stream = stream
                self._latest_answer_generation[event.source] = event.generation
                self._active_answer = ""
            if event.kind is OutputKind.ANSWER_DELTA:
                combined = self._active_answer + event.message
                if len(combined) > self._max_active_answer_chars:
                    combined = combined[: self._max_active_answer_chars - 1] + "…"
                self._active_answer = combined
            else:
                self._active_answer = ""
                self._entries.append(event)
                self._close_answer_stream(stream)
                self._answer_stream = None
        elif event.kind is OutputKind.TRANSCRIPT_PARTIAL:
            self._active_transcript = event.message
        else:
            if event.kind is OutputKind.TRANSCRIPT_FINAL:
                self._active_transcript = ""
            self._entries.append(event)
        if event.kind is OutputKind.EMERGENCY_STOP:
            self._sticky = True
            self._emergency_event = event
        elif event.kind is OutputKind.EMERGENCY_CLEARED:
            self._sticky = False
            self._emergency_event = None
        self._visible = True
        retention = self._idle_timeout_seconds
        if event.kind is OutputKind.ACTION and event.severity in {
            OutputSeverity.ERROR,
            OutputSeverity.CRITICAL,
        }:
            retention = self._action_error_timeout_seconds
        self._visible_until = max(self._visible_until, now + retention)
        return self.snapshot()

    def _accept_emergency_event(self, event: OutputEvent) -> bool:
        if event.generation is None:
            raise ValueError("emergency events require a generation")
        if event.generation < self._latest_emergency_generation:
            return False
        if event.generation == self._latest_emergency_generation:
            # At the same revision, safety wins: stop may override clear, but
            # clear cannot override stop and duplicate states are ignored.
            if not (
                event.kind is OutputKind.EMERGENCY_STOP
                and self._emergency_kind is OutputKind.EMERGENCY_CLEARED
            ):
                return False
        self._latest_emergency_generation = event.generation
        self._emergency_kind = event.kind
        return True

    def tick(self, *, now: float) -> OverlaySnapshot:
        if (
            self._visible
            and not self._sticky
            and not self._active_transcript
            and not self._active_answer
            and now >= self._visible_until
        ):
            self._visible = False
        return self.snapshot()

    def _remember(self, event_id: str) -> None:
        self._seen_event_ids.add(event_id)
        self._seen_event_order.append(event_id)
        if len(self._seen_event_order) > self._seen_event_limit:
            self._seen_event_ids.remove(self._seen_event_order.popleft())

    def _close_answer_stream(self, stream: AnswerStreamKey) -> None:
        closed = self._closed_answer_highwater.get(stream.source, -1)
        self._closed_answer_highwater[stream.source] = max(closed, stream.generation)

    def snapshot(self) -> OverlaySnapshot:
        return OverlaySnapshot(
            tuple(self._entries),
            self._active_transcript,
            self._active_answer,
            self._emergency_event,
            self._visible,
            self._sticky,
        )
