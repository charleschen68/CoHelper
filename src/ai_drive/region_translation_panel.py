"""Platform-independent state for the region translation comparison panel."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ai_drive.region_capture import RegionSelection
from ai_drive.region_translation import (
    RegionTranslationSnapshot,
    RegionTranslationState,
)


class RegionTranslationView(str, Enum):
    ORIGINAL = "original"
    RECOGNIZED = "recognized"
    TRANSLATED = "translated"


@dataclass(frozen=True)
class RegionTranslationPanelSnapshot:
    generation: int
    selection: RegionSelection
    source_image: bytes
    recognized_text: str | None
    translated_text: str | None
    active_view: RegionTranslationView
    retry_available: bool


class RegionTranslationPanelModel:
    """Keep comparison-panel state separate from AppKit rendering."""

    def __init__(self, selection: RegionSelection):
        self._selection = selection
        self._generation = -1
        self._source_image = b""
        self._recognized_text: str | None = None
        self._translated_text: str | None = None
        self._active_view = RegionTranslationView.ORIGINAL
        self._retry_available = False

    @property
    def selection(self) -> RegionSelection:
        return self._selection

    def apply(self, snapshot: RegionTranslationSnapshot) -> bool:
        """Apply only the newest generation and retain the frozen image."""
        if snapshot.generation < self._generation:
            return False
        if snapshot.screenshot is None:
            self._generation = snapshot.generation
            self._source_image = b""
            self._recognized_text = None
            self._translated_text = None
            self._active_view = RegionTranslationView.ORIGINAL
            self._retry_available = False
            return True
        if snapshot.screenshot.display_id != self._selection.display_id:
            raise ValueError("translation panel snapshot belongs to another display")
        new_generation = snapshot.generation > self._generation
        self._generation = snapshot.generation
        self._source_image = snapshot.screenshot.image
        if new_generation:
            self._recognized_text = None
            self._translated_text = None
            self._active_view = RegionTranslationView.ORIGINAL
        if snapshot.source is not None:
            self._recognized_text = snapshot.source.text
        self._translated_text = snapshot.translation
        self._retry_available = snapshot.state in {
            RegionTranslationState.READY,
            RegionTranslationState.FAILED,
        }
        if snapshot.state is RegionTranslationState.READY:
            self._active_view = RegionTranslationView.TRANSLATED
        elif snapshot.source is not None and self._active_view is RegionTranslationView.ORIGINAL:
            self._active_view = RegionTranslationView.RECOGNIZED
        return True

    def select_view(self, view: RegionTranslationView) -> None:
        if view is RegionTranslationView.RECOGNIZED and self._recognized_text is None:
            raise ValueError("recognized text is not available")
        if view is RegionTranslationView.TRANSLATED and self._translated_text is None:
            raise ValueError("translation is not available")
        self._active_view = view

    def copy_text(self) -> str:
        if self._active_view is RegionTranslationView.RECOGNIZED:
            if self._recognized_text is None:
                raise ValueError("recognized text is not available")
            return self._recognized_text
        if self._active_view is RegionTranslationView.TRANSLATED:
            if self._translated_text is None:
                raise ValueError("translation is not available")
            return self._translated_text
        raise ValueError("the original image has no text to copy")

    def request_retry(self) -> bool:
        if not self._retry_available:
            return False
        self._retry_available = False
        return True

    def snapshot(self) -> RegionTranslationPanelSnapshot:
        return RegionTranslationPanelSnapshot(
            self._generation,
            self._selection,
            self._source_image,
            self._recognized_text,
            self._translated_text,
            self._active_view,
            self._retry_available,
        )


__all__ = [
    "RegionTranslationPanelModel",
    "RegionTranslationPanelSnapshot",
    "RegionTranslationView",
]
