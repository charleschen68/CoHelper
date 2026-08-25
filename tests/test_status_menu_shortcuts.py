from cohelper_app import build_status_menu
from cohelper_core import Config


def test_region_translation_menu_displays_option_shift_t():
    menu, region_item = build_status_menu(
        Config({"features": {"region_translation": True}})
    )

    assert menu.itemWithTitle_("翻译屏幕区域") is region_item
    assert str(region_item.keyEquivalent()) == "t"
    assert int(region_item.keyEquivalentModifierMask()) == (1 << 19) | (1 << 17)


def test_region_translation_menu_uses_configured_shortcut():
    _menu, region_item = build_status_menu(
        Config({"region_translation": {"shortcut": "Command-Option-R"}})
    )

    assert str(region_item.keyEquivalent()) == "r"
    assert int(region_item.keyEquivalentModifierMask()) == (1 << 20) | (1 << 19)


def test_voice_menu_describes_hold_gesture_without_binding_a_menu_action():
    menu, _region_item = build_status_menu(Config({}))
    item = menu.itemWithTitle_("开始语音（按住 ⌥Space）")

    assert item is not None
    assert str(item.keyEquivalent()) == ""
