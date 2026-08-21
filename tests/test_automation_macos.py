from io import BytesIO

import cv2
import numpy as np
from PIL import Image

from ai_drive.automation.macos import QuartzFrameCapture
from ai_drive.vision import Screenshot


def test_quartz_frame_capture_decodes_existing_cohelper_screenshot():
    image = Image.new("RGB", (3, 2), "red")
    output = BytesIO()
    image.save(output, "JPEG")
    screenshot = Screenshot(output.getvalue(), 3, 2, 3, 2, 1, 0, "", 0, 0)

    frame = QuartzFrameCapture(capture=lambda: screenshot).capture()

    assert frame.shape == (2, 3, 3)
    assert frame.dtype == np.uint8


def test_quartz_frame_capture_converts_retina_pixels_to_logical_coordinates():
    image = Image.new("RGB", (6, 4), "red")
    output = BytesIO()
    image.save(output, "JPEG")
    screenshot = Screenshot(output.getvalue(), 6, 4, 3, 2, 1, 0, "", 0, 0)
    capture = QuartzFrameCapture(capture=lambda: screenshot)

    capture.capture()

    assert capture.to_logical_point((4, 2)) == (2, 1)
