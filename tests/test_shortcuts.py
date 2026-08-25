import pytest

from ai_drive.shortcuts import parse_shortcut
from cohelper_core import Config, ConfigError


def test_option_shift_t_has_one_shared_menu_and_carbon_representation():
    shortcut = parse_shortcut("Option-Shift-T")

    assert shortcut.canonical == "Option-Shift-T"
    assert shortcut.display == "⌥⇧T"
    assert shortcut.key_equivalent == "t"
    assert shortcut.appkit_modifiers == (1 << 19) | (1 << 17)
    assert shortcut.carbon_key_code == 0x11
    assert shortcut.carbon_modifiers == (1 << 11) | (1 << 9)


def test_configurable_shortcut_accepts_supported_modified_ansi_keys():
    shortcut = parse_shortcut("command-option-r")

    assert shortcut.canonical == "Command-Option-R"
    assert shortcut.display == "⌘⌥R"
    assert shortcut.key_equivalent == "r"
    assert Config(
        {"region_translation": {"shortcut": "Command-Option-R"}}
    ).section("region_translation")["shortcut"] == "Command-Option-R"


@pytest.mark.parametrize(
    "value",
    ["T", "Option-Shift", "Option-Shift-Return", "Option-Option-T", ""],
)
def test_invalid_region_translation_shortcuts_fail_config_validation(value):
    with pytest.raises(ConfigError, match="shortcut"):
        Config({"region_translation": {"shortcut": value}})
