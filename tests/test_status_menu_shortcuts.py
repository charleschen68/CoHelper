from AppKit import (
    NSApplication,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSStatusBar,
)

from cohelper_app import CohelperApp
from cohelper_core import Config


def test_region_translation_menu_displays_option_shift_t():
    NSApplication.sharedApplication()
    app = CohelperApp.alloc().init()
    app.config = Config({"features": {"region_translation": True}})
    app._build_status_item()

    try:
        item = app.status_item.menu().itemWithTitle_("翻译屏幕区域")

        assert item is not None
        assert str(item.keyEquivalent()) == "t"
        assert int(item.keyEquivalentModifierMask()) == int(
            NSEventModifierFlagOption | NSEventModifierFlagShift
        )
    finally:
        NSStatusBar.systemStatusBar().removeStatusItem_(app.status_item)


def test_voice_menu_displays_existing_option_space_gesture():
    NSApplication.sharedApplication()
    app = CohelperApp.alloc().init()
    app._build_status_item()

    try:
        item = app.status_item.menu().itemWithTitle_("开始语音")

        assert item is not None
        assert str(item.keyEquivalent()) == " "
        assert int(item.keyEquivalentModifierMask()) == int(NSEventModifierFlagOption)
    finally:
        NSStatusBar.systemStatusBar().removeStatusItem_(app.status_item)
