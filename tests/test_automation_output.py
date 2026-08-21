from pathlib import Path

from ai_drive.automation.macos_output import QuartzAutomationOutput


def test_click_relocates_template_and_applies_logical_offset():
    clicks = []
    output = QuartzAutomationOutput(
        locate=lambda path: (50.0, 40.0) if path == Path("/guard.png") else None,
        click=lambda point: clicks.append(point),
        type_unicode=lambda text, should_stop: None,
        play_sound=lambda mode: None,
        notify=lambda: None,
    )

    output.click(Path("/guard.png"), (10, -5))

    assert clicks == [(60.0, 35.0)]


def test_text_input_stops_between_characters_when_emergency_stop_is_set():
    written = []
    checks = iter((False, True))

    def type_unicode(text, should_stop):
        for char in text:
            if should_stop():
                return
            written.append(char)

    output = QuartzAutomationOutput(
        locate=lambda _: None,
        click=lambda _: None,
        type_unicode=type_unicode,
        play_sound=lambda mode: None,
        notify=lambda: None,
        should_stop=lambda: next(checks),
    )

    output.type_text("abc")

    assert written == ["a"]
