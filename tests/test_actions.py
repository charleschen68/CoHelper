import pytest
import threading
from io import BytesIO

from PIL import Image, PngImagePlugin

from ai_drive.actions import (
    AccessibleTarget,
    ActionRejected,
    ActionService,
    DesktopState,
    LocatedAccessibleTarget,
)
from ai_drive.vision import NormalizedPoint, ScreenPoint, Screenshot, TargetCandidate


class FakeInspector:
    def __init__(self):
        self.target = AccessibleTarget(
            role="AXButton",
            title="刷新按钮",
            enabled=True,
            owner_bundle_id="com.apple.Safari",
            ancestor_roles=("AXToolbar", "AXWindow"),
        )

    def target_at(self, point):
        return self.target

    def find_capability(self, capability):
        if capability.title != self.target.title:
            return None
        return LocatedAccessibleTarget(ScreenPoint(12, 15), self.target)


class FakeDesktop:
    def state(self):
        return DesktopState(display_id=1, frontmost_bundle_id="com.apple.Safari")


class FakePointer:
    def __init__(self):
        self.clicks = []

    def click(self, point):
        self.clicks.append(point)


def screenshot(image=b"jpeg", captured_at=98.0, display_id=1, bundle_id="com.apple.Safari"):
    return Screenshot(image, 200, 100, 100, 50, display_id, captured_at, bundle_id)


def image_bytes(color="white", metadata=None):
    output = BytesIO()
    info = PngImagePlugin.PngInfo()
    if metadata:
        info.add_text("diagnostic", metadata)
    Image.new("RGB", (200, 100), color).save(output, "PNG", pnginfo=info)
    return output.getvalue()


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


def test_allowlisted_native_capability_bypasses_vision_but_keeps_confirmation_guards():
    service = ActionService(
        FakeInspector(), FakeDesktop(), FakePointer(), now=lambda: 100.0, id_factory=lambda: "A7K3"
    )

    pending = service.prepare_capability_click(
        screenshot(), "Safari 的刷新按钮", user_id=42, chat_id=7
    )

    assert pending is not None
    assert pending.point == ScreenPoint(12, 15)
    assert pending.accessible_title == "刷新按钮"


def test_native_capability_outside_captured_main_display_is_rejected():
    class OffDisplayInspector(FakeInspector):
        def find_capability(self, capability):
            return LocatedAccessibleTarget(ScreenPoint(120, 15), self.target)

    service = ActionService(
        OffDisplayInspector(), FakeDesktop(), FakePointer(), now=lambda: 100.0, id_factory=lambda: "A7K3"
    )

    with pytest.raises(ActionRejected, match="outside the captured main display"):
        service.prepare_capability_click(screenshot(), "Safari 的刷新按钮", user_id=42, chat_id=7)


def test_unknown_instruction_does_not_enumerate_native_capabilities():
    service = ActionService(
        FakeInspector(), FakeDesktop(), FakePointer(), now=lambda: 100.0, id_factory=lambda: "A7K3"
    )

    assert service.prepare_capability_click(
        screenshot(), "关闭 Safari", user_id=42, chat_id=7
    ) is None


def test_ambiguous_refresh_word_does_not_trigger_native_capability_discovery():
    service = ActionService(
        FakeInspector(), FakeDesktop(), FakePointer(), now=lambda: 100.0, id_factory=lambda: "A7K3"
    )

    assert service.prepare_capability_click(
        screenshot(), "刷新这个表单中的数据", user_id=42, chat_id=7
    ) is None


def test_missing_native_capability_can_fall_back_to_the_separately_validated_vision_path():
    class UnavailableInspector(FakeInspector):
        def find_capability(self, capability):
            return None

    service = ActionService(
        UnavailableInspector(), FakeDesktop(), FakePointer(), now=lambda: 100.0, id_factory=lambda: "A7K3"
    )

    assert service.prepare_capability_click(
        screenshot(), "Safari 的刷新按钮", user_id=42, chat_id=7
    ) is None


def test_public_action_service_rejects_longer_confirmation_ttl():
    with pytest.raises(ValueError, match="fixed to 30 seconds"):
        ActionService(
            FakeInspector(), FakeDesktop(), FakePointer(), confirmation_ttl=31
        )


def test_confirm_executes_once_for_bound_user_and_chat():
    pointer = FakePointer()
    service = ActionService(
        FakeInspector(), FakeDesktop(), pointer, now=lambda: 100.0, id_factory=lambda: "A7K3"
    )
    service.prepare_click(
        screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"), user_id=42, chat_id=7
    )

    result = service.confirm("A7K3", user_id=42, chat_id=7, screenshot=screenshot())

    assert result.action_id == "A7K3"
    assert pointer.clicks == [result.point]

    with pytest.raises(ActionRejected, match="does not exist"):
        service.confirm("A7K3", user_id=42, chat_id=7, screenshot=screenshot())


class SensitiveInspector:
    def target_at(self, point):
        return AccessibleTarget(
            role="AXButton",
            title="删除",
            enabled=True,
            owner_bundle_id="com.apple.Safari",
            ancestor_roles=("AXToolbar",),
        )


class EnglishReloadInspector:
    def target_at(self, point):
        return AccessibleTarget(
            role="AXButton",
            title="Reload this page",
            enabled=True,
            owner_bundle_id="com.apple.Safari",
            ancestor_roles=("AXToolbar",),
        )


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
    # Preparation validates the desktop before and after native inspection,
    # then stores the action; confirmation happens after the TTL.
    clock = iter((100.0, 100.0, 100.0, 131.0))
    pointer = FakePointer()
    service = ActionService(
        FakeInspector(), FakeDesktop(), pointer, now=lambda: next(clock), id_factory=lambda: "A7K3"
    )
    service.prepare_click(
        screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"), user_id=42, chat_id=7
    )

    with pytest.raises(ActionRejected, match="expired"):
        service.confirm("A7K3", user_id=42, chat_id=7, screenshot=screenshot(captured_at=131.0))

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
            service.confirm("A7K3", user_id=42, chat_id=7, screenshot=screenshot())
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


def test_changed_screen_content_consumes_token_without_clicking():
    pointer = FakePointer()
    service = ActionService(
        FakeInspector(), FakeDesktop(), pointer, now=lambda: 100.0, id_factory=lambda: "A7K3"
    )
    service.prepare_click(
        screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"), user_id=42, chat_id=7
    )

    with pytest.raises(ActionRejected, match="target region changed"):
        service.confirm("A7K3", user_id=42, chat_id=7, screenshot=screenshot(image=b"changed"))
    with pytest.raises(ActionRejected, match="does not exist"):
        service.confirm("A7K3", user_id=42, chat_id=7, screenshot=screenshot())
    assert pointer.clicks == []


def test_confirmation_ignores_image_container_metadata_when_target_pixels_are_unchanged():
    pointer = FakePointer()
    service = ActionService(
        FakeInspector(), FakeDesktop(), pointer, now=lambda: 100.0, id_factory=lambda: "A7K3"
    )
    service.prepare_click(
        screenshot(image_bytes(metadata="first")),
        TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"),
        user_id=42,
        chat_id=7,
    )

    service.confirm(
        "A7K3", user_id=42, chat_id=7, screenshot=screenshot(image_bytes(metadata="second"))
    )

    assert len(pointer.clicks) == 1


@pytest.mark.parametrize(
    ("confirmation", "message"),
    [
        (screenshot(display_id=2), "desktop state changed"),
        (screenshot(bundle_id="com.apple.TextEdit"), "desktop state changed"),
    ],
)
def test_confirmation_rejects_changed_display_or_application(confirmation, message):
    pointer = FakePointer()
    service = ActionService(
        FakeInspector(), FakeDesktop(), pointer, now=lambda: 100.0, id_factory=lambda: "A7K3"
    )
    service.prepare_click(
        screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"), user_id=42, chat_id=7
    )

    with pytest.raises(ActionRejected, match=message):
        service.confirm("A7K3", user_id=42, chat_id=7, screenshot=confirmation)
    assert pointer.clicks == []


def test_wrong_user_or_chat_cannot_consume_an_action():
    pointer = FakePointer()
    service = ActionService(
        FakeInspector(), FakeDesktop(), pointer, now=lambda: 100.0, id_factory=lambda: "A7K3"
    )
    service.prepare_click(
        screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"), user_id=42, chat_id=7
    )

    with pytest.raises(ActionRejected, match="belongs"):
        service.confirm("A7K3", user_id=99, chat_id=7, screenshot=screenshot())
    service.confirm("A7K3", user_id=42, chat_id=7, screenshot=screenshot())
    assert len(pointer.clicks) == 1


@pytest.mark.parametrize(
    "changed",
    [
        AccessibleTarget(
            role="AXStaticText", title="刷新按钮", enabled=True,
            owner_bundle_id="com.apple.Safari", ancestor_roles=("AXToolbar",)
        ),
        AccessibleTarget(
            role="AXButton", title="删除", enabled=True,
            owner_bundle_id="com.apple.Safari", ancestor_roles=("AXToolbar",)
        ),
    ],
)
def test_confirmation_revalidates_role_and_sensitive_semantics(changed):
    inspector = FakeInspector()
    pointer = FakePointer()
    service = ActionService(inspector, FakeDesktop(), pointer, now=lambda: 100.0, id_factory=lambda: "A7K3")
    service.prepare_click(
        screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"), user_id=42, chat_id=7
    )
    inspector.target = changed

    with pytest.raises(ActionRejected):
        service.confirm("A7K3", user_id=42, chat_id=7, screenshot=screenshot())
    assert pointer.clicks == []


def test_action_identifier_collision_never_overwrites_another_users_action():
    service = ActionService(
        FakeInspector(), FakeDesktop(), FakePointer(), now=lambda: 100.0, id_factory=lambda: "COLLIDE"
    )
    target = TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮")
    service.prepare_click(screenshot(), target, user_id=42, chat_id=7)

    with pytest.raises(ActionRejected, match="unique"):
        service.prepare_click(screenshot(), target, user_id=99, chat_id=8)


def test_web_content_cannot_impersonate_allowlisted_browser_toolbar_action():
    inspector = FakeInspector()
    inspector.target = AccessibleTarget(
        role="AXButton",
        title="刷新按钮",
        enabled=True,
        owner_bundle_id="com.apple.Safari",
        ancestor_roles=("AXWebArea", "AXGroup", "AXWindow"),
    )
    service = ActionService(
        inspector, FakeDesktop(), FakePointer(), now=lambda: 100.0, id_factory=lambda: "A7K3"
    )

    with pytest.raises(ActionRejected, match="web-content"):
        service.prepare_click(
            screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"), user_id=42, chat_id=7
        )


def test_new_request_immediately_cancels_old_action_and_supersedes_slow_prepare():
    service = ActionService(
        FakeInspector(), FakeDesktop(), FakePointer(), now=lambda: 100.0,
        id_factory=iter(("OLD", "NEW")).__next__,
    )
    target = TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮")
    old_prepare = service.begin_click(42)
    service.prepare_click(
        screenshot(), target, user_id=42, chat_id=7, preparation_id=old_prepare
    )

    new_prepare = service.begin_click(42)

    with pytest.raises(ActionRejected, match="does not exist"):
        service.confirm("OLD", user_id=42, chat_id=7, screenshot=screenshot())
    with pytest.raises(ActionRejected, match="superseded"):
        service.prepare_click(
            screenshot(), target, user_id=42, chat_id=7, preparation_id=old_prepare
        )
    pending = service.prepare_click(
        screenshot(), target, user_id=42, chat_id=7, preparation_id=new_prepare
    )
    assert pending.action_id == "NEW"


def test_authorization_revocation_invalidates_prepared_actions_before_clicking():
    pointer = FakePointer()
    service = ActionService(
        FakeInspector(), FakeDesktop(), pointer, now=lambda: 100.0, id_factory=lambda: "A7K3"
    )
    service.prepare_click(
        screenshot(), TargetCandidate(NormalizedPoint(500, 500), 0.91, "刷新按钮"), user_id=42, chat_id=7
    )

    service.revoke_all()

    with pytest.raises(ActionRejected, match="does not exist"):
        service.confirm("A7K3", user_id=42, chat_id=7, screenshot=screenshot())
    assert pointer.clicks == []
