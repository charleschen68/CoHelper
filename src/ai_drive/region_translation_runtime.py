"""Runtime seam joining AppKit selection, translation coordination, and panel state."""

from __future__ import annotations

from typing import Callable

from ai_drive.region_selection_appkit import RegionSelectionOverlayController
from ai_drive.region_translation import (
    RegionTranslationSnapshot,
    TranslationTarget,
    build_region_translation_coordinator,
)
from ai_drive.region_translation_panel import (
    RegionTranslationPanelModel,
    RegionTranslationPanelSnapshot,
    RegionTranslationView,
)


class RegionTranslationRuntime:
    """Own one selection/coordinator/panel pipeline for the enabled feature."""

    def __init__(
        self,
        config,
        *,
        selection_controller: RegionSelectionOverlayController | None = None,
        on_panel_change: Callable[[RegionTranslationPanelSnapshot], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ):
        self._config = config
        self._on_panel_change = on_panel_change or (lambda _snapshot: None)
        self._on_error = on_error or (lambda _error: None)
        self._selection = selection_controller or RegionSelectionOverlayController(
            on_selected=self._selection_finished,
            on_cancelled=lambda _generation: None,
            on_error=lambda _generation, error: self._on_error(error),
        )
        setter = getattr(self._selection, "set_callbacks", None)
        if setter is not None:
            setter(
                on_selected=self._selection_finished,
                on_cancelled=lambda _generation: None,
                on_error=lambda _generation, error: self._on_error(error),
            )
        self._coordinator = None
        self._panel: RegionTranslationPanelModel | None = None

    def trigger(self) -> int:
        if not self._config.enabled("region_translation"):
            raise RuntimeError("region translation feature is disabled")
        return self._selection.trigger()

    def cancel(self) -> bool:
        return self._selection.cancel()

    def close(self) -> None:
        self._selection.close()
        coordinator, self._coordinator = self._coordinator, None
        self._panel = None
        if coordinator is not None:
            coordinator.close()

    def retry(self) -> int | None:
        if self._panel is None or self._coordinator is None:
            return None
        if not self._panel.request_retry():
            return None
        return self._coordinator.retry()

    def change_target(self, target: TranslationTarget) -> int | None:
        if self._panel is None or self._coordinator is None:
            return None
        self._panel.select_target(target)
        return self._coordinator.change_target(target)

    def select_view(self, view: RegionTranslationView) -> None:
        if self._panel is None:
            raise RuntimeError("translation panel is not active")
        self._panel.select_view(view)
        self._on_panel_change(self._panel.snapshot())

    def copy_text(self) -> str:
        if self._panel is None:
            raise RuntimeError("translation panel is not active")
        return self._panel.copy_text()

    def _selection_finished(self, generation, selection, screenshot) -> None:
        try:
            if generation != self._selection.generation:
                return
            if self._coordinator is not None:
                self._coordinator.close()
            self._panel = RegionTranslationPanelModel(selection)
            self._coordinator = build_region_translation_coordinator(
                self._config,
                on_change=self._coordinator_changed,
            )
            self._coordinator.start(screenshot)
        except Exception as exc:
            self._on_error(exc)

    def _coordinator_changed(self, snapshot: RegionTranslationSnapshot) -> None:
        if self._panel is None:
            return
        try:
            if self._panel.apply(snapshot):
                self._on_panel_change(self._panel.snapshot())
        except Exception as exc:
            self._on_error(exc)


__all__ = ["RegionTranslationRuntime"]
