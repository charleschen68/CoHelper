from AppKit import NSApplication

from cohelper_app import CohelperApp
from cohelper_core import Config


def test_region_translation_shortcut_is_editable_in_configuration_center():
    NSApplication.sharedApplication()
    app = CohelperApp.alloc().init()
    app.config = Config(
        {"region_translation": {"shortcut": "Command-Option-R"}}
    )

    try:
        app._build_advanced_config_window()

        control = app.advanced_controls["region_translation.shortcut"]
        assert str(control.stringValue()) == "Command-Option-R"
    finally:
        app.advanced_window.orderOut_(None)
        app.advanced_window.close()
        app.advanced_window = None


def test_voice_command_form_round_trips_human_readable_entries():
    aliases = CohelperApp._parse_command_aliases(
        "refresh_safari: 刷新 Safari 执行 / 刷新 Safari，执行 | open_settings: 打开设置执行"
    )
    instructions = CohelperApp._parse_command_instructions(
        "refresh_safari: Safari 的刷新按钮 | open_settings: 设置按钮"
    )

    assert aliases == {
        "refresh_safari": ["刷新 Safari 执行", "刷新 Safari，执行"],
        "open_settings": ["打开设置执行"],
    }
    assert instructions["refresh_safari"] == "Safari 的刷新按钮"
    assert "refresh_safari" in CohelperApp._format_command_aliases(aliases)


def test_voice_command_form_rejects_ambiguous_free_form_entries():
    try:
        CohelperApp._parse_command_aliases("刷新 Safari 执行")
    except ValueError as exc:
        assert "命令短语" in str(exc)
    else:
        raise AssertionError("invalid aliases must be rejected")

    try:
        CohelperApp._parse_command_instructions("refresh_safari")
    except ValueError as exc:
        assert "动作说明" in str(exc)
    else:
        raise AssertionError("invalid instructions must be rejected")
