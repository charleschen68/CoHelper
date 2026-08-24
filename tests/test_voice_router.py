from __future__ import annotations

import pytest

from ai_drive.voice import VoiceCommandRouter, VoiceCommandRouterError, VoiceRouteKind


def test_exact_final_phrase_ending_in_execute_routes_to_registered_command():
    router = VoiceCommandRouter({"open_settings": ["打开设置，执行", "打开设置执行"]})

    route = router.route("打开设置，执行", finalized=True)

    assert route.kind is VoiceRouteKind.COMMAND
    assert route.command == "open_settings"
    assert route.text == "打开设置，执行"


def test_non_final_text_is_never_routed_as_a_command():
    router = VoiceCommandRouter({"open_settings": ["打开设置，执行"]})

    route = router.route("打开设置，执行", finalized=False)

    assert route.kind is VoiceRouteKind.PENDING
    assert route.command is None


def test_unregistered_or_mixed_command_text_is_rejected():
    router = VoiceCommandRouter({"open_settings": ["打开设置，执行"]})

    with pytest.raises(VoiceCommandRouterError, match="未注册"):
        router.route("关闭设置，执行", finalized=True)
    with pytest.raises(VoiceCommandRouterError, match="混合"):
        router.route("打开设置，执行，然后查询 Flink", finalized=True)


def test_duplicate_aliases_are_rejected_at_registration():
    with pytest.raises(VoiceCommandRouterError, match="冲突"):
        VoiceCommandRouter(
            {
                "first": ["打开第一项执行"],
                "second": ["打开第一项执行"],
            }
        )
