"""Validated public data types shared by visual capture and actions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedPoint:
    """A model-space point where each axis ranges from 0 through 1000."""

    x: float
    y: float

    def __post_init__(self) -> None:
        if not 0 <= self.x <= 1000 or not 0 <= self.y <= 1000:
            raise ValueError("normalized coordinates must be between 0 and 1000")


@dataclass(frozen=True)
class ScreenPoint:
    x: float
    y: float


@dataclass(frozen=True)
class Screenshot:
    image: bytes
    pixel_width: int
    pixel_height: int
    logical_width: float
    logical_height: float
    display_id: int
    captured_at: float
    frontmost_bundle_id: str
    origin_x: float = 0
    origin_y: float = 0

    def __post_init__(self) -> None:
        if min(self.pixel_width, self.pixel_height, self.logical_width, self.logical_height) <= 0:
            raise ValueError("screenshot dimensions must be positive")
        if not self.image:
            raise ValueError("screenshot image must not be empty")

    def to_screen_point(self, point: NormalizedPoint) -> ScreenPoint:
        return ScreenPoint(
            x=self.origin_x + self.logical_width * point.x / 1000,
            y=self.origin_y + self.logical_height * point.y / 1000,
        )
