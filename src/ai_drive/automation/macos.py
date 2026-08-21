"""macOS frame adapter shared by the automation runner."""

from __future__ import annotations

import cv2
import numpy as np

from ai_drive.macos import QuartzScreenCapture


class QuartzFrameCapture:
    """Capture the main display once and decode it for OpenCV matching."""

    def __init__(self, capture=None):
        self._capture = capture or QuartzScreenCapture().capture_main_display
        self._pixel_to_logical = (1.0, 1.0)

    def capture(self) -> np.ndarray:
        screenshot = self._capture()
        frame = cv2.imdecode(np.frombuffer(screenshot.image, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("failed to decode main-display screenshot")
        self._pixel_to_logical = (
            float(screenshot.logical_width) / frame.shape[1],
            float(screenshot.logical_height) / frame.shape[0],
        )
        return frame

    def to_logical_point(self, point: tuple[float, float]) -> tuple[float, float]:
        """Convert an OpenCV pixel location into main-display Quartz coordinates."""
        return (point[0] * self._pixel_to_logical[0], point[1] * self._pixel_to_logical[1])
