"""Display-local selection geometry and frozen screenshot cropping."""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO

from PIL import Image

from ai_drive.vision import Screenshot


MIN_SELECTION_WIDTH = 120.0
MIN_SELECTION_HEIGHT = 80.0


class RegionSelectionError(ValueError):
    """A selection is outside one display or too small to process."""


@dataclass(frozen=True)
class RegionSelection:
    display_id: int
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not all(
            math.isfinite(float(value))
            for value in (self.x, self.y, self.width, self.height)
        ):
            raise RegionSelectionError("selection coordinates must be finite")
        if self.width < MIN_SELECTION_WIDTH or self.height < MIN_SELECTION_HEIGHT:
            raise RegionSelectionError(
                "selection must be at least 120 by 80 logical points"
            )
        if self.width <= 0 or self.height <= 0:
            raise RegionSelectionError("selection dimensions must be positive")

    @classmethod
    def from_drag(
        cls,
        display_id: int,
        display_origin: tuple[float, float],
        display_size: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> "RegionSelection":
        origin_x, origin_y = display_origin
        display_width, display_height = display_size
        if not all(
            math.isfinite(float(value))
            for value in (*display_origin, *display_size, *start, *end)
        ):
            raise RegionSelectionError("selection coordinates must be finite")
        if display_width <= 0 or display_height <= 0:
            raise RegionSelectionError("display dimensions must be positive")
        left, right = sorted((float(start[0]), float(end[0])))
        top, bottom = sorted((float(start[1]), float(end[1])))
        display_right = origin_x + display_width
        display_bottom = origin_y + display_height
        if (
            left < origin_x
            or top < origin_y
            or right > display_right
            or bottom > display_bottom
        ):
            raise RegionSelectionError("selection must stay within one display")
        return cls(display_id, left, top, right - left, bottom - top)


def crop_screenshot(screenshot: Screenshot, selection: RegionSelection) -> Screenshot:
    if screenshot.display_id != selection.display_id:
        raise RegionSelectionError("selection belongs to another display")
    try:
        image = Image.open(BytesIO(screenshot.image))
        if image.size != (screenshot.pixel_width, screenshot.pixel_height):
            raise RegionSelectionError("captured screenshot dimensions do not match metadata")
    except (OSError, ValueError) as exc:
        if isinstance(exc, RegionSelectionError):
            raise
        raise RegionSelectionError("captured screenshot cannot be decoded") from exc
    right = selection.x + selection.width
    bottom = selection.y + selection.height
    screenshot_right = screenshot.origin_x + screenshot.logical_width
    screenshot_bottom = screenshot.origin_y + screenshot.logical_height
    if (
        selection.x < screenshot.origin_x
        or selection.y < screenshot.origin_y
        or right > screenshot_right
        or bottom > screenshot_bottom
    ):
        raise RegionSelectionError("selection must stay within the captured display")

    scale_x = screenshot.pixel_width / screenshot.logical_width
    scale_y = screenshot.pixel_height / screenshot.logical_height
    left_px = round((selection.x - screenshot.origin_x) * scale_x)
    # AppKit pointer/screen coordinates start at the display's bottom edge,
    # whereas Pillow image rows start at the top edge.
    top_px = round(
        (
            screenshot.logical_height
            - (selection.y - screenshot.origin_y)
            - selection.height
        )
        * scale_y
    )
    right_px = round((right - screenshot.origin_x) * scale_x)
    bottom_px = round(
        (screenshot.logical_height - (selection.y - screenshot.origin_y)) * scale_y
    )
    if right_px <= left_px or bottom_px <= top_px:
        raise RegionSelectionError("selection maps to an empty pixel region")

    try:
        cropped = image.crop((left_px, top_px, right_px, bottom_px))
    except (OSError, ValueError) as exc:
        raise RegionSelectionError("captured screenshot cannot be decoded") from exc
    output = BytesIO()
    cropped.save(output, format="PNG")
    return Screenshot(
        output.getvalue(),
        right_px - left_px,
        bottom_px - top_px,
        selection.width,
        selection.height,
        screenshot.display_id,
        screenshot.captured_at,
        screenshot.frontmost_bundle_id,
        selection.x,
        selection.y,
    )


__all__ = [
    "MIN_SELECTION_HEIGHT",
    "MIN_SELECTION_WIDTH",
    "RegionSelection",
    "RegionSelectionError",
    "crop_screenshot",
]
