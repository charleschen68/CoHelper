"""Pure safety gate for fresh, one-time voice direct-action targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class VoiceDirectActionError(ValueError):
    pass


@dataclass(frozen=True)
class VoiceDirectTarget:
    target_id: str
    rule_id: str
    detected_at: float
    voice_direct: bool = False


@dataclass(frozen=True)
class VoiceDirectIntent:
    utterance_id: str
    target_id: str
    rule_id: str


class VoiceDirectTargetStore:
    """Keep only the current detection context; never performs an action."""

    def __init__(self, *, ttl_seconds: float = 3.0, enabled: bool = True):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._enabled = enabled
        self._targets: dict[str, VoiceDirectTarget] = {}

    def replace(self, targets: Iterable[VoiceDirectTarget]) -> None:
        if not self._enabled:
            self._targets.clear()
            return
        current: dict[str, VoiceDirectTarget] = {}
        for target in targets:
            if not target.target_id.strip() or not target.rule_id.strip():
                raise VoiceDirectActionError("target_id and rule_id must be non-empty")
            if target.target_id in current:
                raise VoiceDirectActionError("duplicate target_id")
            current[target.target_id] = target
        self._targets = current

    def prepare(self, utterance_id: str, *, now: float) -> VoiceDirectIntent:
        if not self._enabled:
            raise VoiceDirectActionError("voice direct actions are disabled")
        if not utterance_id.strip():
            raise VoiceDirectActionError("utterance_id must be non-empty")
        candidates = [
            target
            for target in self._targets.values()
            if target.voice_direct and 0 <= now - target.detected_at <= self._ttl_seconds
        ]
        if not candidates:
            raise VoiceDirectActionError("no fresh direct-action target")
        if len(candidates) != 1:
            raise VoiceDirectActionError("direct-action target is ambiguous")
        target = candidates[0]
        return VoiceDirectIntent(utterance_id, target.target_id, target.rule_id)

    def consume(self, intent: VoiceDirectIntent, *, now: float) -> VoiceDirectTarget:
        if not self._enabled:
            raise VoiceDirectActionError("voice direct actions are disabled")
        target = self._targets.get(intent.target_id)
        if target is None:
            raise VoiceDirectActionError("direct-action target was already consumed or replaced")
        if not 0 <= now - target.detected_at <= self._ttl_seconds:
            self._targets.pop(intent.target_id, None)
            raise VoiceDirectActionError("direct-action target expired")
        self._targets.pop(intent.target_id)
        return target

    def invalidate_all(self) -> None:
        self._targets.clear()
