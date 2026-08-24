from ai_drive.voice import PushToTalkController, PushToTalkEvent


def test_option_space_press_and_release_make_one_recording_window():
    actions = []
    controller = PushToTalkController(actions.append)

    assert controller.handle(PushToTalkEvent(key="space", option=True, pressed=True)) == "start"
    assert controller.handle(PushToTalkEvent(key="space", option=True, pressed=True)) is None
    assert controller.handle(PushToTalkEvent(key="space", option=True, pressed=False)) == "finish"
    assert actions == ["start", "finish"]


def test_non_option_or_other_keys_cannot_start_voice_input():
    actions = []
    controller = PushToTalkController(actions.append)

    assert controller.handle(PushToTalkEvent(key="space", option=False, pressed=True)) is None
    assert controller.handle(PushToTalkEvent(key="return", option=True, pressed=True)) is None
    assert controller.handle(PushToTalkEvent(key="space", option=True, pressed=False)) is None
    assert actions == []
