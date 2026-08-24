"""Versioned local event contract for display and sound consumers."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


SCHEMA_VERSION = 1
MAX_WIRE_BYTES = 65_536
MAX_MESSAGE_CHARS = 16_384
_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_WIRE_FIELDS = {
    "schema_version",
    "event_id",
    "kind",
    "source",
    "occurred_at",
    "title",
    "message",
    "severity",
    "generation",
    "metadata",
}
_GENERATION_REQUIRED_KINDS = {
    "answer_delta",
    "answer_final",
    "emergency_cleared",
    "emergency_stop",
}


class OutputEventError(ValueError):
    pass


def _require_unicode_scalar_text(value: str, field: str) -> None:
    try:
        value.encode("utf-8")
        value.encode("utf-16-le")
    except UnicodeError as exc:
        raise OutputEventError(f"{field} must contain valid Unicode scalar text") from exc


class OutputKind(StrEnum):
    ACTION = "action"
    ANSWER_DELTA = "answer_delta"
    ANSWER_FINAL = "answer_final"
    DETECTION = "detection"
    EMERGENCY_CLEARED = "emergency_cleared"
    EMERGENCY_STOP = "emergency_stop"
    ERROR = "error"
    KNOWLEDGE_SOURCES = "knowledge_sources"
    STATUS = "status"
    TEXT_INPUT = "text_input"
    TRANSCRIPT_PARTIAL = "transcript_partial"
    TRANSCRIPT_FINAL = "transcript_final"
    TRANSLATION = "translation"


class OutputSource(StrEnum):
    ACTIONS = "actions"
    AUTOMATION = "automation"
    CLIPBOARD = "clipboard"
    KNOWLEDGE = "knowledge"
    SYSTEM = "system"
    VOICE = "voice"


class OutputSeverity(StrEnum):
    CRITICAL = "critical"
    ERROR = "error"
    INFO = "info"
    WARNING = "warning"


@dataclass(frozen=True)
class OutputEvent:
    event_id: str
    kind: OutputKind
    source: OutputSource
    occurred_at: float
    title: str
    message: str
    severity: OutputSeverity
    generation: int | None
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OutputKind):
            raise OutputEventError("kind must be an OutputKind")
        if not isinstance(self.source, OutputSource):
            raise OutputEventError("source must be an OutputSource")
        if not isinstance(self.severity, OutputSeverity):
            raise OutputEventError("severity must be an OutputSeverity")
        if not isinstance(self.event_id, str) or not _EVENT_ID.fullmatch(self.event_id):
            raise OutputEventError("event_id must be a safe non-empty identifier")
        if not isinstance(self.occurred_at, (int, float)) or isinstance(self.occurred_at, bool) or not math.isfinite(self.occurred_at) or self.occurred_at < 0:
            raise OutputEventError("occurred_at must be a finite non-negative number")
        if not isinstance(self.title, str) or not 1 <= len(self.title) <= 256:
            raise OutputEventError("title must contain between 1 and 256 characters")
        if not isinstance(self.message, str) or len(self.message) > MAX_MESSAGE_CHARS:
            raise OutputEventError(f"message must contain at most {MAX_MESSAGE_CHARS} characters")
        _require_unicode_scalar_text(self.title, "title")
        _require_unicode_scalar_text(self.message, "message")
        if self.generation is not None and (
            not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation < 0
        ):
            raise OutputEventError("generation must be a non-negative integer or null")
        if self.kind.value in _GENERATION_REQUIRED_KINDS and self.generation is None:
            raise OutputEventError(f"{self.kind.value} events require a generation")
        if not isinstance(self.metadata, dict) or not all(isinstance(key, str) for key in self.metadata):
            raise OutputEventError("metadata must be an object with string keys")
        try:
            encoded_metadata = json.dumps(self.metadata, ensure_ascii=False, allow_nan=False)
            _require_unicode_scalar_text(encoded_metadata, "metadata")
        except OutputEventError:
            raise
        except (TypeError, ValueError, RecursionError) as exc:
            raise OutputEventError("metadata must contain JSON-compatible finite values") from exc

    def to_json(self) -> str:
        # Frozen dataclasses do not freeze nested dictionaries; revalidate in
        # case a caller mutated metadata after construction.
        self.__post_init__()
        payload = {
            "schema_version": SCHEMA_VERSION,
            "event_id": self.event_id,
            "kind": self.kind.value,
            "source": self.source.value,
            "occurred_at": self.occurred_at,
            "title": self.title,
            "message": self.message,
            "severity": self.severity.value,
            "generation": self.generation,
            "metadata": self.metadata,
        }
        try:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            wire = encoded.encode("utf-8")
        except UnicodeError as exc:
            raise OutputEventError("event must contain valid Unicode scalar text") from exc
        except (TypeError, ValueError, RecursionError) as exc:
            raise OutputEventError("event must contain JSON-compatible finite values") from exc
        if len(wire) > MAX_WIRE_BYTES:
            raise OutputEventError(f"event exceeds {MAX_WIRE_BYTES} wire bytes")
        return encoded

    @classmethod
    def from_json(cls, raw: str) -> "OutputEvent":
        if not isinstance(raw, str):
            raise OutputEventError(f"event exceeds {MAX_WIRE_BYTES} wire bytes")
        try:
            wire = raw.encode("utf-8")
        except UnicodeError as exc:
            raise OutputEventError("event must contain valid Unicode scalar text") from exc
        if len(wire) > MAX_WIRE_BYTES:
            raise OutputEventError(f"event exceeds {MAX_WIRE_BYTES} wire bytes")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise OutputEventError("event must be a JSON object")
            if set(payload) != _WIRE_FIELDS:
                raise OutputEventError("event fields do not match schema")
            if type(payload["schema_version"]) is not int or payload["schema_version"] != SCHEMA_VERSION:
                raise OutputEventError(f"unsupported schema_version: {payload['schema_version']!r}")
            del payload["schema_version"]
            payload["kind"] = OutputKind(payload["kind"])
            payload["source"] = OutputSource(payload["source"])
            payload["severity"] = OutputSeverity(payload["severity"])
            return cls(**payload)
        except OutputEventError:
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OutputEventError(f"invalid output event: {exc}") from exc
