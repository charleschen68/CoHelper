from cohelper_app import build_status_menu, region_translation_menu_binding
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


def test_menu_does_not_advertise_an_unavailable_global_shortcut():
    binding = region_translation_menu_binding(
        Config({"features": {"region_translation": True}}),
        region_translation_shortcut_active=False,
    )

    assert binding.title == "翻译屏幕区域（快捷键不可用）"
    assert binding.key_equivalent == ""
    assert binding.modifier_mask == 0
    assert binding.enabled is True
