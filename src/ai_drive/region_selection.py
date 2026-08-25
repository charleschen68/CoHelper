"""Explicit, single-session region-selection state machine."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from ai_drive.region_capture import RegionSelection, RegionSelectionError
from ai_drive.vision import Screenshot


class RegionSelectionState(str, Enum):
    IDLE = "idle"
    SELECTING = "selecting"
    CAPTURED = "captured"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class RegionSelectionSnapshot:
    generation: int
    state: RegionSelectionState
    selection: RegionSelection | None = None
    screenshot: Screenshot | None = None
    error: str | None = None


class RegionSelectionSession:
    """Own one explicit selection and capture; never captures on trigger."""

    def __init__(self):
        self._lock = threading.RLock()
        self._generation = 0
        self._state = RegionSelectionState.IDLE
        self._display_id: int | None = None
        self._display_origin: tuple[float, float] | None = None
        self._display_size: tuple[float, float] | None = None
        self._start: tuple[float, float] | None = None
        self._end: tuple[float, float] | None = None
        self._selection: RegionSelection | None = None
        self._screenshot: Screenshot | None = None
        self._error: str | None = None

    def begin(
        self,
        display_id: int,
        display_origin: tuple[float, float],
        display_size: tuple[float, float],
    ) -> int:
        with self._lock:
            self._generation += 1
            self._state = RegionSelectionState.SELECTING
            self._display_id = display_id
            self._display_origin = display_origin
            self._display_size = display_size
            self._start = None
            self._end = None
            self._selection = None
            self._screenshot = None
            self._error = None
            return self._generation

    def update_drag(self, start: tuple[float, float], end: tuple[float, float]) -> RegionSelection:
        with self._lock:
            self._require_selecting()
            assert self._display_id is not None
            assert self._display_origin is not None and self._display_size is not None
            try:
                selection = RegionSelection.from_drag(
                    self._display_id,
                    self._display_origin,
                    self._display_size,
                    start,
                    end,
                )
            except RegionSelectionError:
                self._start = None
                self._end = None
                self._selection = None
                raise
            self._start = start
            self._end = end
            self._selection = selection
            return selection

    def finish(self, capture: Callable[[RegionSelection], Screenshot]) -> Screenshot:
        with self._lock:
            self._require_selecting()
            if self._selection is None:
                raise RegionSelectionError("a valid selection is required before capture")
            generation = self._generation
            selection = self._selection
        try:
            screenshot = capture(selection)
        except Exception as exc:
            with self._lock:
                if generation == self._generation and self._state is RegionSelectionState.SELECTING:
                    self._state = RegionSelectionState.FAILED
                    self._error = type(exc).__name__
            raise
        with self._lock:
            if generation != self._generation or self._state is not RegionSelectionState.SELECTING:
                raise RegionSelectionError("selection was superseded during capture")
            if screenshot.display_id != selection.display_id:
                self._state = RegionSelectionState.FAILED
                self._error = "capture returned another display"
                raise RegionSelectionError("capture returned another display")
        geometry = (
            screenshot.origin_x,
            screenshot.origin_y,
            screenshot.logical_width,
            screenshot.logical_height,
        )
        expected = (selection.x, selection.y, selection.width, selection.height)
        if not all(math.isclose(actual, wanted, rel_tol=0, abs_tol=0.01) for actual, wanted in zip(geometry, expected)):
            with self._lock:
                if generation == self._generation and self._state is RegionSelectionState.SELECTING:
                    self._state = RegionSelectionState.FAILED
                    self._error = "capture geometry does not match selection"
            raise RegionSelectionError("capture geometry does not match selection")
        with self._lock:
            if generation != self._generation or self._state is not RegionSelectionState.SELECTING:
                raise RegionSelectionError("selection was superseded during capture")
            self._screenshot = screenshot
            self._state = RegionSelectionState.CAPTURED
            return screenshot

    def cancel(self) -> bool:
        with self._lock:
            if self._state is not RegionSelectionState.SELECTING:
                return False
            self._state = RegionSelectionState.CANCELLED
            self._start = None
            self._end = None
            self._selection = None
            self._screenshot = None
            self._error = None
            return True

    def snapshot(self) -> RegionSelectionSnapshot:
        with self._lock:
            return RegionSelectionSnapshot(
                self._generation,
                self._state,
                self._selection,
                self._screenshot,
                self._error,
            )

    def _require_selecting(self) -> None:
        if self._state is not RegionSelectionState.SELECTING:
            raise RuntimeError("region selection is not active")


__all__ = [
    "RegionSelectionSession",
    "RegionSelectionSnapshot",
    "RegionSelectionState",
]
