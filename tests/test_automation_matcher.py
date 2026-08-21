from pathlib import Path

import cv2
import numpy as np

from ai_drive.automation.config import TemplateSpec
from ai_drive.automation.matcher import OpenCVTemplateMatcher


def test_matcher_uses_a_single_frame_and_reports_logical_template_center(tmp_path: Path):
    frame = np.zeros((100, 120, 3), dtype=np.uint8)
    frame[30:50, 40:70] = (255, 255, 255)
    frame[35:45, 50:60] = (0, 0, 0)
    template_path = tmp_path / "template.png"
    assert cv2.imwrite(str(template_path), frame[30:50, 40:70])
    matcher = OpenCVTemplateMatcher()

    result = matcher.locate(frame, TemplateSpec(template_path, 0.9))

    assert result is not None
    assert result.center == (55.0, 40.0)
    assert result.confidence >= 0.9
