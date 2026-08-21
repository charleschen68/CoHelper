"""OpenCV matching over a caller-provided, single captured frame."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ai_drive.automation.config import TemplateSpec


@dataclass(frozen=True)
class TemplateMatch:
    center: tuple[float, float]
    confidence: float


class OpenCVTemplateMatcher:
    def __init__(self):
        self._templates: dict[str, np.ndarray] = {}

    def locate(self, frame: np.ndarray, template: TemplateSpec) -> TemplateMatch | None:
        source = self._grayscale(frame)
        image = self._load(template)
        origin_x = origin_y = 0
        if template.region is not None:
            origin_x, origin_y, width, height = template.region
            source = source[origin_y : origin_y + height, origin_x : origin_x + width]
        if source.shape[0] < image.shape[0] or source.shape[1] < image.shape[1]:
            return None
        result = cv2.matchTemplate(source, image, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, location = cv2.minMaxLoc(result)
        if confidence < template.confidence:
            return None
        return TemplateMatch(
            (origin_x + location[0] + image.shape[1] / 2, origin_y + location[1] + image.shape[0] / 2),
            float(confidence),
        )

    def _load(self, template: TemplateSpec) -> np.ndarray:
        key = str(template.path)
        if key not in self._templates:
            image = cv2.imread(key, cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise FileNotFoundError(f"automation template does not exist or is unreadable: {template.path}")
            self._templates[key] = image
        return self._templates[key]

    @staticmethod
    def _grayscale(frame: np.ndarray) -> np.ndarray:
        if frame.ndim == 2:
            return frame
        if frame.ndim == 3 and frame.shape[2] in {3, 4}:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY if frame.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)
        raise ValueError("frame must be a grayscale, BGR, or BGRA image")
