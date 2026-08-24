from io import BytesIO

from PIL import Image

from ai_drive.macos import QuartzScreenCapture
from ai_drive.vision import OverlayMask, Screenshot


def test_quartz_capture_adapter_applies_injected_overlay_mask():
    output = BytesIO()
    Image.new("RGB", (20, 10), "white").save(output, format="PNG")
    screenshot = Screenshot(output.getvalue(), 20, 10, 20, 10, 1, 10.0, "com.apple.Safari")
    capture = QuartzScreenCapture(lambda: OverlayMask(2, 2, 4, 3))

    masked = capture.apply_overlay_mask(screenshot)

    image = Image.open(BytesIO(masked.image)).convert("RGB")
    assert image.getpixel((3, 6)) == (32, 32, 32)
    assert image.getpixel((15, 6)) == (255, 255, 255)
