from io import BytesIO

import pytest
from PIL import Image

from ai_drive.vision import OverlayMask, Screenshot, ScreenshotMaskError, mask_screenshot


def screenshot():
    output = BytesIO()
    Image.new("RGB", (200, 100), (255, 255, 255)).save(output, format="PNG")
    return Screenshot(output.getvalue(), 200, 100, 100, 50, 7, 12.5, "com.apple.Safari", 10, 20)


def pixels(image):
    return Image.open(BytesIO(image.image)).convert("RGB")


def test_mask_maps_bottom_left_logical_coordinates_to_pixels_and_preserves_metadata():
    source = screenshot()
    masked = mask_screenshot(source, OverlayMask(35, 30, 20, 10))
    image = pixels(masked)

    assert image.getpixel((80, 70)) == (32, 32, 32)
    assert image.getpixel((20, 70)) == (255, 255, 255)
    assert masked.display_id == source.display_id
    assert masked.frontmost_bundle_id == source.frontmost_bundle_id
    assert masked.captured_at == source.captured_at


def test_mask_is_clipped_to_display_and_rejects_non_positive_dimensions():
    masked = mask_screenshot(screenshot(), OverlayMask(-10, 55, 30, 20))
    image = pixels(masked)
    assert image.getpixel((0, 0)) == (32, 32, 32)

    with pytest.raises(ScreenshotMaskError, match="positive"):
        OverlayMask(0, 0, 0, 10)
