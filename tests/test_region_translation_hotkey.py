import pytest

import ai_drive.region_translation_hotkey as hotkey_module
import ai_drive.region_translation_runtime as runtime_module
import cohelper_app
from ai_drive.region_translation_hotkey import (
    HotKeyRegistrationError,
    MacRegionTranslationHotKey,
)
from cohelper_app import CohelperApp
from cohelper_core import Config


class RecordingBackend:
    def __init__(self, error=None):
        self.error = error
        self.registrations = []
        self.unregistered = []

    def register(self, key_code, modifiers, callback, *, exclusive):
        if self.error is not None:
            raise self.error
        token = object()
        self.registrations.append((key_code, modifiers, callback, exclusive, token))
        return token

    def unregister(self, token):
        self.unregistered.append(token)


def test_option_shift_t_triggers_region_selection_and_unregisters_cleanly():
    backend = RecordingBackend()
    triggers = []
    hotkey = MacRegionTranslationHotKey(lambda: triggers.append("translate"), backend=backend)

    hotkey.start()
    hotkey.start()

    assert len(backend.registrations) == 1
    key_code, modifiers, callback, exclusive, token = backend.registrations[0]
    assert key_code == 0x11
    assert modifiers == (1 << 11) | (1 << 9)
    assert exclusive is True
    callback()
    assert triggers == ["translate"]

    hotkey.stop()
    hotkey.stop()

    assert backend.unregistered == [token]
    assert hotkey.is_running is False


def test_shortcut_conflict_is_reported_without_partial_registration():
    backend = RecordingBackend(HotKeyRegistrationError("Option-Shift-T is unavailable"))
    hotkey = MacRegionTranslationHotKey(lambda: None, backend=backend)

    with pytest.raises(HotKeyRegistrationError, match="unavailable"):
        hotkey.start()

    assert hotkey.is_running is False


def test_enabled_app_hotkey_triggers_selection_and_stops_with_feature(monkeypatch):
    created_hotkeys = []
    created_runtimes = []

    class Runtime:
        def __init__(self, _config, **_callbacks):
            self.trigger_count = 0
            self.closed = False
            created_runtimes.append(self)

        def trigger(self):
            self.trigger_count += 1

        def close(self):
            self.closed = True

    class HotKey:
        def __init__(self, callback):
            self.callback = callback
            self.started = False
            self.stopped = False
            created_hotkeys.append(self)

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(runtime_module, "RegionTranslationRuntime", Runtime)
    monkeypatch.setattr(hotkey_module, "MacRegionTranslationHotKey", HotKey)
    monkeypatch.setattr(
        cohelper_app.AppHelper,
        "callAfter",
        lambda callback, *args: callback(*args),
    )
    app = CohelperApp.alloc().init()
    app.config = Config({"features": {"region_translation": True}})

    app._start_region_translation_feature()
    created_hotkeys[0].callback()

    assert created_hotkeys[0].started is True
    assert created_runtimes[0].trigger_count == 1

    app._stop_region_translation_feature()

    assert created_hotkeys[0].stopped is True
    assert created_runtimes[0].closed is True


def test_disabled_feature_does_not_construct_global_hotkey(monkeypatch):
    monkeypatch.setattr(
        hotkey_module,
        "MacRegionTranslationHotKey",
        lambda _callback: pytest.fail("disabled feature must not construct a hotkey"),
    )
    app = CohelperApp.alloc().init()
    app.config = Config({"features": {"region_translation": False}})

    app._start_region_translation_feature()

    assert app.region_translation_hotkey is None
