from cohelper_app import (
    build_status_menu,
    region_translation_error_message,
    region_translation_hotkey_unavailable_message,
    region_translation_menu_binding,
)
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


def test_unavailable_hotkey_message_is_actionable_without_native_error_text():
    message = region_translation_hotkey_unavailable_message("⌥⇧T")

    assert message == "无法启用 ⌥⇧T 全局快捷键。你仍可从菜单栏点击“翻译屏幕区域”手动开始。"


def test_conflicting_hotkey_message_suggests_a_safe_alternative():
    message = region_translation_hotkey_unavailable_message("⌥⇧T", conflict=True)

    assert message == (
        "⌥⇧T 可能已被其他应用或系统占用。"
        "请在高级配置中更换区域翻译快捷键，或从菜单栏点击“翻译屏幕区域”手动开始。"
    )


def test_region_error_message_does_not_expose_exception_text():
    assert region_translation_error_message(RuntimeError("private OCR content")) == (
        "区域翻译出现问题，请关闭结果面板后重试。"
    )
