"""Local vision-language target analysis."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .models import NormalizedPoint, Screenshot


class VisionAnalysisError(ValueError):
    pass


class VisionClient(Protocol):
    def analyze(self, model: str, image: bytes, prompt: str) -> str: ...


@dataclass(frozen=True)
class TargetCandidate:
    point: NormalizedPoint
    confidence: float
    description: str


class VisionAnalyzer:
    def __init__(self, client: VisionClient, model: str = "qwen2.5vl:7b"):
        if model != "qwen2.5vl:7b":
            raise ValueError("vision model is fixed to qwen2.5vl:7b")
        self._client = client
        self._model = model

    def locate(self, screenshot: Screenshot, instruction: str) -> TargetCandidate:
        instruction = instruction.strip()
        if not instruction:
            raise VisionAnalysisError("target instruction must not be empty")
        raw = self._client.analyze(self._model, screenshot.image, self._prompt(instruction))
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise VisionAnalysisError("vision model did not return JSON") from exc
        required = {"found", "x", "y", "confidence", "description"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise VisionAnalysisError("vision response schema is invalid")
        if payload["found"] is not True:
            raise VisionAnalysisError("vision target was not found")
        if any(isinstance(payload[key], bool) for key in ("x", "y", "confidence")):
            raise VisionAnalysisError("vision response contains invalid values")
        try:
            x = float(payload["x"])
            y = float(payload["y"])
            confidence = float(payload["confidence"])
            description = str(payload["description"]).strip()
        except (TypeError, ValueError) as exc:
            raise VisionAnalysisError("vision response contains invalid values") from exc
        point = self._to_normalized_point(screenshot, x, y)
        if not 0 <= confidence <= 1 or not description:
            raise VisionAnalysisError("vision response contains invalid values")
        return TargetCandidate(point, confidence, description)

    @staticmethod
    def _to_normalized_point(screenshot: Screenshot, x: float, y: float) -> NormalizedPoint:
        """Accept the documented normalized space and a bounded local pixel fallback.

        Qwen occasionally returns coordinates in the physical pixel space of
        the exact image it received. The fallback is deliberately unavailable
        for values outside the captured image; downstream Accessibility
        validation still has to identify the configured native capability.
        """
        try:
            return NormalizedPoint(x, y)
        except ValueError:
            if 0 <= x <= screenshot.pixel_width and 0 <= y <= screenshot.pixel_height:
                return NormalizedPoint(
                    x * 1000 / screenshot.pixel_width,
                    y * 1000 / screenshot.pixel_height,
                )
            raise VisionAnalysisError("vision response contains invalid values") from None

    @staticmethod
    def _prompt(instruction: str) -> str:
        return (
            "Locate exactly one clickable UI target on this screenshot for the instruction: "
            f"{instruction!r}. Return only one JSON object with exactly these keys: "
            'found, x, y (0-1000 normalized coordinates), confidence (0-1), and description. '
            "When absent, set found=false and the remaining values to null. Do not guess."
        )
