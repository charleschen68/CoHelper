from dataclasses import dataclass

import pytest

from ai_drive.voice import (
    VoiceActionBridgeError,
    VoiceCommandActionBridge,
    VoiceRoute,
    VoiceRouteKind,
)


@dataclass(frozen=True)
class Prepared:
    action_id: str


class Workflow:
    def __init__(self):
        self.calls = []

    def prepare(self, instruction, user_id, chat_id):
        self.calls.append(("prepare", instruction, user_id, chat_id))
        return Prepared("A1")

    def confirm(self, action_id, user_id, chat_id):
        self.calls.append(("confirm", action_id, user_id, chat_id))
        return "clicked"

    def cancel(self, action_id, user_id, chat_id):
        self.calls.append(("cancel", action_id, user_id, chat_id))


def command_route():
    return VoiceRoute(VoiceRouteKind.COMMAND, "刷新 Safari 执行", "refresh_safari")


def test_prepare_uses_guarded_workflow_but_does_not_confirm():
    workflow = Workflow()
    bridge = VoiceCommandActionBridge(workflow, {"refresh_safari": "刷新 Safari"})

    prepared = bridge.prepare(command_route(), utterance_id="voice-1", user_id=7, chat_id=9)

    assert prepared.action_id == "A1"
    assert workflow.calls == [("prepare", "刷新 Safari", 7, 9)]


def test_confirm_is_explicit_and_one_time():
    workflow = Workflow()
    bridge = VoiceCommandActionBridge(workflow, {"refresh_safari": "刷新 Safari"})
    prepared = bridge.prepare(command_route(), utterance_id="voice-1", user_id=7, chat_id=9)

    assert bridge.confirm(prepared, user_id=7, chat_id=9) == "clicked"
    with pytest.raises(VoiceActionBridgeError, match="unknown"):
        bridge.confirm(prepared, user_id=7, chat_id=9)


def test_non_command_missing_instruction_and_disabled_paths_are_rejected():
    workflow = Workflow()
    bridge = VoiceCommandActionBridge(workflow, {})
    with pytest.raises(VoiceActionBridgeError, match="only"):
        bridge.prepare(VoiceRoute(VoiceRouteKind.KNOWLEDGE, "问题"), utterance_id="v", user_id=1, chat_id=1)
    with pytest.raises(VoiceActionBridgeError, match="instruction"):
        bridge.prepare(command_route(), utterance_id="v", user_id=1, chat_id=1)
    disabled = VoiceCommandActionBridge(workflow, {"refresh_safari": "刷新 Safari"}, enabled=False)
    with pytest.raises(VoiceActionBridgeError, match="disabled"):
        disabled.prepare(command_route(), utterance_id="v", user_id=1, chat_id=1)
