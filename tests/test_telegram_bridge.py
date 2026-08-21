from dataclasses import dataclass
import asyncio

from apps.telegram_bridge.service import TelegramCommandHandler
from apps.telegram_bridge.runtime import _cancel_watcher, _deliver_notifications, _runtime_config, _watch_runtime_config
from ai_drive.automation.notifications import NotificationQueue
from cohelper_core import Config


@dataclass
class Prepared:
    action_id: str = "A7K3"
    preview: bytes = b"preview"


class FakeWorkflow:
    def __init__(self):
        self.prepared = []

    def prepare(self, instruction, user_id, chat_id):
        self.prepared.append((instruction, user_id, chat_id))
        return Prepared()

    def confirm(self, action_id, user_id, chat_id):
        return b"result"

    def cancel(self, action_id, user_id, chat_id):
        return None


class FakeChat:
    def answer(self, text):
        return f"chat:{text}"


def test_click_command_returns_bound_confirmation_protocol():
    workflow = FakeWorkflow()
    handler = TelegramCommandHandler(workflow, FakeChat())

    reply = handler.handle("/click Safari 的刷新按钮", user_id=42, chat_id=7)

    assert workflow.prepared == [("Safari 的刷新按钮", 42, 7)]
    assert reply.image == b"preview"
    assert "/confirm A7K3" in reply.text
    assert "/cancel A7K3" in reply.text


def test_ordinary_message_never_enters_action_workflow():
    workflow = FakeWorkflow()
    handler = TelegramCommandHandler(workflow, FakeChat())

    reply = handler.handle("点击刷新按钮", user_id=42, chat_id=7)

    assert reply.text == "chat:点击刷新按钮"
    assert workflow.prepared == []


def test_runtime_config_changes_when_bridge_is_disabled_or_identity_changes():
    original = Config({"telegram": {"enabled": True, "allowed_user_id": 42, "allowed_chat_id": 7}})
    disabled = Config({"telegram": {"enabled": False, "allowed_user_id": 42, "allowed_chat_id": 7}})
    another_user = Config({"telegram": {"enabled": True, "allowed_user_id": 99, "allowed_chat_id": 7}})

    assert _runtime_config(original) != _runtime_config(disabled)
    assert _runtime_config(original) != _runtime_config(another_user)


def test_runtime_watcher_stops_polling_when_config_changes():
    original = Config({"telegram": {"enabled": True, "allowed_user_id": 42, "allowed_chat_id": 7}})
    changed = Config({"telegram": {"enabled": False, "allowed_user_id": 42, "allowed_chat_id": 7}})

    class Application:
        stopped = False

        def stop_running(self):
            self.stopped = True

    async def no_wait(seconds):
        assert seconds == 1

    application = Application()
    revocations = []
    asyncio.run(
        _watch_runtime_config(
            application,
            _runtime_config(original),
            load_config=lambda: changed,
            pause=no_wait,
            revoke_actions=lambda: revocations.append("revoked"),
        )
    )

    assert application.stopped
    assert revocations == ["revoked"]


def test_runtime_watcher_stops_when_configuration_cannot_be_loaded():
    original = Config({"telegram": {"enabled": True, "allowed_user_id": 42, "allowed_chat_id": 7}})

    class Application:
        stopped = False

        def stop_running(self):
            self.stopped = True

    async def no_wait(seconds):
        pass

    def fail_load():
        raise OSError("unreadable")

    application = Application()
    revocations = []
    asyncio.run(
        _watch_runtime_config(
            application,
            _runtime_config(original),
            load_config=fail_load,
            pause=no_wait,
            revoke_actions=lambda: revocations.append("revoked"),
        )
    )

    assert application.stopped
    assert revocations == ["revoked"]


def test_unchanged_config_watcher_is_cancelled_during_normal_shutdown():
    original = Config({"telegram": {"enabled": True, "allowed_user_id": 42, "allowed_chat_id": 7}})

    class Application:
        stopped = False

        def stop_running(self):
            self.stopped = True

    async def scenario():
        never = asyncio.Event()

        async def wait_forever(seconds):
            assert seconds == 1
            await never.wait()

        application = Application()
        task = asyncio.create_task(
            _watch_runtime_config(
                application,
                _runtime_config(original),
                load_config=lambda: original,
                pause=wait_forever,
            )
        )
        await asyncio.sleep(0)
        await _cancel_watcher(task)
        return application, task

    application, task = asyncio.run(scenario())

    assert task.cancelled()
    assert not application.stopped


def test_notification_delivery_acknowledges_only_sent_messages(tmp_path):
    class Bot:
        sent = []
        async def send_message(self, *, chat_id, text):
            self.sent.append((chat_id, text))

    async def pause(_):
        raise asyncio.CancelledError

    queue = NotificationQueue(tmp_path / "state.sqlite")
    queue.enqueue("rule succeeded")
    try:
        asyncio.run(_deliver_notifications(Bot(), 7, queue, pause=pause))
    except asyncio.CancelledError:
        pass

    assert queue.pending() == ()
