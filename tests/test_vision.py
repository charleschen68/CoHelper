import pytest

from ai_drive.vision import NormalizedPoint, Screenshot, VisionAnalysisError, VisionAnalyzer


def test_normalized_model_point_maps_to_macos_logical_coordinates():
    screenshot = Screenshot(
        image=b"jpeg",
        pixel_width=3024,
        pixel_height=1964,
        logical_width=1512,
        logical_height=982,
        display_id=1,
        captured_at=100.0,
        frontmost_bundle_id="com.apple.Safari",
    )

    point = screenshot.to_screen_point(NormalizedPoint(x=500, y=250))

    assert point.x == 756
    assert point.y == 245.5


class FakeVisionClient:
    def analyze(self, model: str, image: bytes, prompt: str) -> str:
        return '{"found": true, "x": 125, "y": 250, "confidence": 0.91, "description": "刷新按钮"}'


def test_vision_analyzer_accepts_only_structured_target_output():
    screenshot = Screenshot(b"jpeg", 100, 100, 100, 100, 1, 100.0, "com.apple.Safari")

    target = VisionAnalyzer(FakeVisionClient()).locate(screenshot, "刷新页面")

    assert target.point == NormalizedPoint(125, 250)
    assert target.confidence == 0.91
    assert target.description == "刷新按钮"


class MarkdownVisionClient:
    def analyze(self, model: str, image: bytes, prompt: str) -> str:
        return '```json\n{"x": 1, "y": 2}\n```'


def test_vision_analyzer_rejects_markdown_or_partial_schema():
    screenshot = Screenshot(b"jpeg", 100, 100, 100, 100, 1, 100.0, "com.apple.Safari")

    with pytest.raises(VisionAnalysisError, match="JSON"):
        VisionAnalyzer(MarkdownVisionClient()).locate(screenshot, "刷新页面")
