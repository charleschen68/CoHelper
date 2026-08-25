"""Mask UI-owned regions before screenshots reach vision or confirmation."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw

from .models import Screenshot


class ScreenshotMaskError(ValueError):
    pass


@dataclass(frozen=True)
class OverlayMask:
    """A logical screen rectangle using the display's bottom-left origin."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ScreenshotMaskError("overlay mask dimensions must be positive")


def mask_screenshot(screenshot: Screenshot, mask: OverlayMask) -> Screenshot:
    """Return a copy with the overlay rectangle filled, preserving capture metadata."""
    image = Image.open(BytesIO(screenshot.image)).convert("RGB")
    scale_x = screenshot.pixel_width / screenshot.logical_width
    scale_y = screenshot.pixel_height / screenshot.logical_height
    left = round((mask.x - screenshot.origin_x) * scale_x)
    right = round((mask.x + mask.width - screenshot.origin_x) * scale_x)
    top = round(
        screenshot.pixel_height
        - (mask.y + mask.height - screenshot.origin_y) * scale_y
    )
    bottom = round(screenshot.pixel_height - (mask.y - screenshot.origin_y) * scale_y)
    left = max(0, min(screenshot.pixel_width, left))
    right = max(0, min(screenshot.pixel_width, right))
    top = max(0, min(screenshot.pixel_height, top))
    bottom = max(0, min(screenshot.pixel_height, bottom))
    if left < right and top < bottom:
        ImageDraw.Draw(image).rectangle((left, top, right - 1, bottom - 1), fill=(32, 32, 32))
    output = BytesIO()
    image.save(output, format="PNG")
    return Screenshot(
        output.getvalue(),
        screenshot.pixel_width,
        screenshot.pixel_height,
        screenshot.logical_width,
        screenshot.logical_height,
        screenshot.display_id,
        screenshot.captured_at,
        screenshot.frontmost_bundle_id,
        screenshot.origin_x,
        screenshot.origin_y,
    )
