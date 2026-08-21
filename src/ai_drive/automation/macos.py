"""macOS frame adapter shared by the automation runner."""

from __future__ import annotations

import cv2
import numpy as np

from ai_drive.macos import QuartzScreenCapture


class QuartzFrameCapture:
    """Capture the main display once and decode it for OpenCV matching."""

    def __init__(self, capture=None):
        self._capture = capture or QuartzScreenCapture().capture_main_display

    def capture(self) -> np.ndarray:
        screenshot = self._capture()
        frame = cv2.imdecode(np.frombuffer(screenshot.image, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("failed to decode main-display screenshot")
        return frame
