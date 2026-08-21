from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image

from ai_drive.actions import ActionRejected, PendingAction
from ai_drive.vision import NormalizedPoint, ScreenPoint, Screenshot, TargetCandidate
from ai_drive.workflow import VisualClickWorkflow


def image_bytes(color="white"):
    output = BytesIO()
    Image.new("RGB", (100, 50), color).save(output, "JPEG")
    return output.getvalue()


def screenshot(image, captured_at):
    return Screenshot(image, 100, 50, 100, 50, 1, captured_at, "com.apple.Safari")


class Capture:
    def __init__(self, screenshots):
        self.screenshots = iter(screenshots)
        self.count = 0

    def capture_main_display(self):
        self.count += 1
        return next(self.screenshots)


class Analyzer:
    def locate(self, screenshot, instruction):
        return TargetCandidate(NormalizedPoint(500, 500), 0.99, "刷新按钮")


class Actions:
    def __init__(self):
        self.prepared_with = None
        self.confirmed_with = None

    def begin_click(self, user_id):
        return 9

    def prepare_capability_click(self, shot, instruction, *, user_id, chat_id, preparation_id):
        return None

    def prepare_click(self, shot, target, *, user_id, chat_id, preparation_id):
        self.prepared_with = (shot, preparation_id)
        return PendingAction(
            "A7K3", user_id, chat_id, ScreenPoint(50, 25), target.description,
            "AXButton", "刷新按钮", shot.display_id, shot.frontmost_bundle_id,
            shot.captured_at, sha256(shot.image).hexdigest(),
        )

    def confirm(self, action_id, *, user_id, chat_id, screenshot):
        self.confirmed_with = screenshot


def test_prepare_recaptures_after_inference_and_uses_fresh_capture():
    image = image_bytes()
    initial = screenshot(image, 0)
    fresh = screenshot(image, 100)
    capture = Capture([initial, fresh])
    actions = Actions()

    prepared = VisualClickWorkflow(capture, Analyzer(), actions).prepare("刷新", 42, 7)

    assert prepared.action_id == "A7K3"
    assert actions.prepared_with == (fresh, 9)
    assert capture.count == 2


class NativeActions(Actions):
    def prepare_capability_click(self, shot, instruction, *, user_id, chat_id, preparation_id):
        return PendingAction(
            "A7K3", user_id, chat_id, ScreenPoint(50, 25), instruction,
            "AXButton", "刷新按钮", shot.display_id, shot.frontmost_bundle_id,
            shot.captured_at, sha256(shot.image).hexdigest(),
        )


class FailingAnalyzer:
    def locate(self, screenshot, instruction):
        raise AssertionError("vision must not run for an allowlisted native capability")


def test_prepare_uses_allowlisted_native_capability_before_vision():
    image = image_bytes()
    capture = Capture([screenshot(image, 100)])

    prepared = VisualClickWorkflow(capture, FailingAnalyzer(), NativeActions()).prepare("刷新", 42, 7)

    assert prepared.action_id == "A7K3"
    assert capture.count == 1


def test_prepare_rejects_screen_change_during_inference():
    capture = Capture([screenshot(image_bytes("white"), 0), screenshot(image_bytes("black"), 100)])
    actions = Actions()

    with pytest.raises(ActionRejected, match="screen changed during visual analysis"):
        VisualClickWorkflow(capture, Analyzer(), actions).prepare("刷新", 42, 7)
    assert actions.prepared_with is None


def test_confirm_recaptures_before_click_and_returns_post_click_image():
    before = screenshot(image_bytes("white"), 100)
    after = screenshot(image_bytes("black"), 101)
    capture = Capture([before, after])
    actions = Actions()

    result = VisualClickWorkflow(capture, Analyzer(), actions).confirm("A7K3", 42, 7)

    assert actions.confirmed_with is before
    assert result
    assert capture.count == 2
