import pytest
import threading

from ai_drive.actions import AccessibleTarget, ActionRejected, ActionService, DesktopState
from ai_drive.vision import NormalizedPoint, Screenshot, TargetCandidate


class FakeInspector:
    def target_at(self, point):
        return AccessibleTarget(role="AXButton", title="刷新按钮", enabled=True)


class FakeDesktop:
    def state(self):
        return DesktopState(display_id=1, frontmost_bundle_id="com.apple.Safari")


class FakePointer:
    def __init__(self):
        self.clicks = []

    def click(self, point):
        self.clicks.append(point)


def screenshot():
    return Screenshot(b"jpeg", 200, 100, 100, 50, 1, 98.0, "com.apple.Safari")


def test_valid_accessible_target_creates_bound_pending_action():
    service = ActionService(
        FakeInspector(), FakeDesktop(), FakePointer(), now=lambda: 100.0, id_factory=lambda: "A7K3"
    )

    pending = service.prepare_click(
        screenshot(),
        TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"),
        user_id=42,
        chat_id=7,
    )

    assert pending.action_id == "A7K3"
    assert pending.point.x == 50
    assert pending.point.y == 25
    assert pending.user_id == 42
    assert pending.chat_id == 7
    assert pending.accessible_title == "刷新按钮"
    assert len(pending.screenshot_digest) == 64


def test_confirm_executes_once_for_bound_user_and_chat():
    pointer = FakePointer()
    service = ActionService(
        FakeInspector(), FakeDesktop(), pointer, now=lambda: 100.0, id_factory=lambda: "A7K3"
    )
    service.prepare_click(
        screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"), user_id=42, chat_id=7
    )

    result = service.confirm("A7K3", user_id=42, chat_id=7)

    assert result.action_id == "A7K3"
    assert pointer.clicks == [result.point]

    with pytest.raises(ActionRejected, match="does not exist"):
        service.confirm("A7K3", user_id=42, chat_id=7)


class SensitiveInspector:
    def target_at(self, point):
        return AccessibleTarget(role="AXButton", title="删除", enabled=True)


class EnglishReloadInspector:
    def target_at(self, point):
        return AccessibleTarget(role="AXButton", title="Reload this page", enabled=True)


def test_known_safe_bilingual_accessibility_label_can_match():
    service = ActionService(
        EnglishReloadInspector(), FakeDesktop(), FakePointer(), now=lambda: 100.0, id_factory=lambda: "A7K3"
    )

    pending = service.prepare_click(
        screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.99, "刷新按钮"), user_id=42, chat_id=7
    )

    assert pending.accessible_title == "Reload this page"


def test_sensitive_accessibility_target_is_rejected():
    service = ActionService(
        SensitiveInspector(), FakeDesktop(), FakePointer(), now=lambda: 100.0, id_factory=lambda: "A7K3"
    )

    with pytest.raises(ActionRejected, match="sensitive"):
        service.prepare_click(
            screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.99, "删除"), user_id=42, chat_id=7
        )


def test_expired_confirmation_never_clicks():
    clock = iter((100.0, 131.0))
    pointer = FakePointer()
    service = ActionService(
        FakeInspector(), FakeDesktop(), pointer, now=lambda: next(clock), id_factory=lambda: "A7K3"
    )
    service.prepare_click(
        screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"), user_id=42, chat_id=7
    )

    with pytest.raises(ActionRejected, match="expired"):
        service.confirm("A7K3", user_id=42, chat_id=7)

    assert pointer.clicks == []


def test_concurrent_confirmation_can_click_only_once():
    pointer = FakePointer()
    service = ActionService(
        FakeInspector(), FakeDesktop(), pointer, now=lambda: 100.0, id_factory=lambda: "A7K3"
    )
    service.prepare_click(
        screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"), user_id=42, chat_id=7
    )
    barrier = threading.Barrier(3)
    outcomes = []

    def confirm():
        barrier.wait()
        try:
            service.confirm("A7K3", user_id=42, chat_id=7)
            outcomes.append("clicked")
        except ActionRejected:
            outcomes.append("rejected")

    threads = [threading.Thread(target=confirm) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["clicked", "rejected"]
    assert len(pointer.clicks) == 1
