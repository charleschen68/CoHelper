from ai_drive.automation.control import AutomationController


class FakeService:
    def __init__(self):
        self.calls = []

    def arm(self, group):
        self.calls.append(("arm", group))

    def disarm(self, group):
        self.calls.append(("disarm", group))

    def status(self):
        return "accept: disarmed"


class RejectingService(FakeService):
    def arm(self, group):
        raise ValueError(group)

    def disarm(self, group):
        raise ValueError(group)


def test_start_requires_same_private_identity_to_confirm():
    service = FakeService()
    controller = AutomationController(service, allowed_user_id=7, allowed_chat_id=9, token_factory=lambda: "A1B2")

    reply = controller.handle("/automation_start accept", user_id=7, chat_id=9)

    assert "/automation_confirm A1B2" in reply
    assert service.calls == []
    assert controller.handle("/automation_confirm A1B2", user_id=7, chat_id=10) == "权限不足。"
    assert controller.handle("/automation_confirm A1B2", user_id=7, chat_id=9) == "已启动 accept。"
    assert service.calls == [("arm", "accept")]


def test_stop_all_is_immediate_and_cancels_pending_start():
    service = FakeService()
    controller = AutomationController(service, allowed_user_id=7, allowed_chat_id=9, token_factory=lambda: "A1B2")

    controller.handle("/automation_start accept", user_id=7, chat_id=9)

    assert controller.handle("/automation_stop all", user_id=7, chat_id=9) == "已停止全部规则组。"
    assert controller.handle("/automation_confirm A1B2", user_id=7, chat_id=9) == "启动确认不存在或已过期。"
    assert service.calls == [("disarm", "all")]


def test_unknown_group_is_rejected_without_leaking_an_internal_exception():
    service = RejectingService()
    controller = AutomationController(service, allowed_user_id=7, allowed_chat_id=9, token_factory=lambda: "A1B2")

    controller.handle("/automation_start unknown", user_id=7, chat_id=9)

    assert controller.handle("/automation_confirm A1B2", user_id=7, chat_id=9) == "未知规则组。"
    assert controller.handle("/automation_stop unknown", user_id=7, chat_id=9) == "未知规则组。"
