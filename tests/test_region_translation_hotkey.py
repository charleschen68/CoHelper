import ctypes

import pytest

import ai_drive.region_translation_hotkey as hotkey_module
import ai_drive.region_translation_runtime as runtime_module
import cohelper_app
from ai_drive.region_translation_hotkey import (
    HotKeyRegistrationError,
    HotKeyFailureReason,
    MacRegionTranslationHotKey,
    _CarbonHotKeyBackend,
    _EventHotKeyID,
)
from ai_drive.shortcuts import parse_shortcut
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


class CarbonFunction:
    def __init__(self, implementation):
        self.implementation = implementation
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.implementation(*args)


class RecordingCarbon:
    def __init__(self):
        self.received_id = None
        self.get_parameter_status = 0
        self.install_status = 0
        self.register_status = 0
        self.unregister_status = 0
        self.remove_status = 0
        self.unregister_calls = 0
        self.remove_calls = 0
        self.handler = None
        self.GetApplicationEventTarget = CarbonFunction(lambda: 100)
        self.RegisterEventHotKey = CarbonFunction(self._register)
        self.InstallEventHandler = CarbonFunction(self._install)
        self.GetEventParameter = CarbonFunction(self._get_parameter)
        self.UnregisterEventHotKey = CarbonFunction(self._unregister)
        self.RemoveEventHandler = CarbonFunction(self._remove)

    @staticmethod
    def _set_ref(pointer, value):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(value)

    def _register(self, _key_code, _modifiers, hot_key_id, _target, _options, out_ref):
        self.received_id = hot_key_id
        if self.register_status != 0:
            return self.register_status
        self._set_ref(out_ref, 200)
        return 0

    def _install(self, _target, handler, _count, _event_types, _data, out_ref):
        if self.install_status != 0:
            return self.install_status
        self.handler = handler
        self._set_ref(out_ref, 300)
        return 0

    def _get_parameter(
        self,
        _event,
        _name,
        _kind,
        _actual_type,
        _size,
        _actual_size,
        out_data,
    ):
        if self.get_parameter_status != 0:
            return self.get_parameter_status
        ctypes.cast(out_data, ctypes.POINTER(_EventHotKeyID))[0] = self.received_id
        return 0

    def _unregister(self, _hot_key_ref):
        self.unregister_calls += 1
        return self.unregister_status

    def _remove(self, _handler_ref):
        self.remove_calls += 1
        return self.remove_status


def test_option_shift_t_triggers_region_selection_and_unregisters_cleanly():
    backend = RecordingBackend()
    triggers = []
    hotkey = MacRegionTranslationHotKey(
        lambda: triggers.append("translate"),
        parse_shortcut("Option-Shift-T"),
        backend=backend,
    )

    hotkey.start()
    hotkey.start()

    assert len(backend.registrations) == 1
    key_code, modifiers, callback, exclusive, token = backend.registrations[0]
    assert key_code == 0x11
    assert modifiers == (1 << 11) | (1 << 9)
    assert exclusive is False
    callback()
    assert triggers == ["translate"]

    hotkey.stop()
    hotkey.stop()

    assert backend.unregistered == [token]
    assert hotkey.is_running is False


def test_shortcut_conflict_is_reported_without_partial_registration():
    backend = RecordingBackend(HotKeyRegistrationError("Option-Shift-T is unavailable"))
    hotkey = MacRegionTranslationHotKey(
        lambda: None,
        parse_shortcut("Option-Shift-T"),
        backend=backend,
    )

    with pytest.raises(HotKeyRegistrationError, match="unavailable"):
        hotkey.start()

    assert hotkey.is_running is False


def test_carbon_conflict_is_classified_without_exposing_native_status():
    carbon = RecordingCarbon()
    carbon.register_status = _CarbonHotKeyBackend.EVENT_HOTKEY_EXISTS
    backend = _CarbonHotKeyBackend(carbon=carbon)

    with pytest.raises(HotKeyRegistrationError) as caught:
        backend.register(0x11, (1 << 11) | (1 << 9), lambda: None, exclusive=False)

    assert caught.value.reason == HotKeyFailureReason.CONFLICT


def test_failed_native_cleanup_retains_registration_for_safe_retry():
    backend = RecordingBackend()
    hotkey = MacRegionTranslationHotKey(
        lambda: None,
        parse_shortcut("Option-Shift-T"),
        backend=backend,
    )
    hotkey.start()
    registration = backend.registrations[0][-1]
    original_unregister = backend.unregister
    attempts = 0

    def fail_once(token):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise HotKeyRegistrationError("native cleanup failed")
        original_unregister(token)

    backend.unregister = fail_once

    with pytest.raises(HotKeyRegistrationError, match="cleanup"):
        hotkey.stop()

    assert hotkey.is_running is True

    hotkey.stop()

    assert hotkey.is_running is False
    assert backend.unregistered == [registration]


def test_failed_native_cleanup_disarms_shortcut_before_retry():
    backend = RecordingBackend()
    triggers = []
    hotkey = MacRegionTranslationHotKey(
        lambda: triggers.append("translate"),
        parse_shortcut("Option-Shift-T"),
        backend=backend,
    )
    hotkey.start()
    callback = backend.registrations[0][2]
    backend.unregister = lambda _token: (_ for _ in ()).throw(
        HotKeyRegistrationError("native cleanup failed")
    )

    with pytest.raises(HotKeyRegistrationError, match="cleanup"):
        hotkey.stop()

    callback()

    assert hotkey.is_running is True
    assert hotkey.is_armed is False
    assert triggers == []


def test_carbon_handler_dispatches_only_the_registered_hotkey_id():
    carbon = RecordingCarbon()
    backend = _CarbonHotKeyBackend(carbon=carbon)
    triggers = []
    registration = backend.register(
        0x11,
        (1 << 11) | (1 << 9),
        lambda: triggers.append("translate"),
        exclusive=True,
    )

    assert carbon.handler(None, ctypes.c_void_p(400), None) == 0
    assert triggers == ["translate"]

    carbon.received_id = _EventHotKeyID(carbon.received_id.signature, 999)
    assert carbon.handler(None, ctypes.c_void_p(400), None) == backend.EVENT_NOT_HANDLED
    assert triggers == ["translate"]

    carbon.get_parameter_status = -1
    assert carbon.handler(None, ctypes.c_void_p(400), None) == backend.EVENT_NOT_HANDLED
    assert triggers == ["translate"]

    carbon.get_parameter_status = 0
    carbon.received_id = _EventHotKeyID(backend.SIGNATURE, backend.IDENTIFIER)
    backend.unregister(registration)


def test_carbon_partial_cleanup_keeps_handler_alive_until_retry_succeeds():
    carbon = RecordingCarbon()
    backend = _CarbonHotKeyBackend(carbon=carbon)
    registration = backend.register(0x11, 1 << 11, lambda: None, exclusive=True)
    carbon.remove_status = -50

    with pytest.raises(HotKeyRegistrationError, match="handler removal"):
        backend.unregister(registration)

    assert registration.hot_key_ref is None
    assert registration.handler_ref is not None
    assert registration.handler is not None
    assert carbon.unregister_calls == 1
    assert carbon.remove_calls == 1

    carbon.remove_status = 0
    backend.unregister(registration)

    assert registration.handler_ref is None
    assert carbon.unregister_calls == 1
    assert carbon.remove_calls == 2


def test_failed_registration_rollback_retains_hotkey_ref_for_cleanup_retry():
    carbon = RecordingCarbon()
    carbon.install_status = -50
    carbon.unregister_status = -51
    backend = _CarbonHotKeyBackend(carbon=carbon)
    hotkey = MacRegionTranslationHotKey(
        lambda: None,
        parse_shortcut("Option-Shift-T"),
        backend=backend,
    )

    with pytest.raises(HotKeyRegistrationError, match="rollback also failed"):
        hotkey.start()

    assert hotkey.is_running is True
    carbon.unregister_status = 0

    hotkey.stop()

    assert hotkey.is_running is False
    assert carbon.unregister_calls == 2


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
        def __init__(self, callback, shortcut):
            self.callback = callback
            self.shortcut = shortcut
            self.started = False
            self.stopped = False
            created_hotkeys.append(self)

        def start(self):
            self.started = True

        @property
        def is_armed(self):
            return self.started and not self.stopped

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
    app.config = Config(
        {
            "features": {"region_translation": True},
            "region_translation": {"shortcut": "Command-Option-R"},
        }
    )

    app._start_region_translation_feature()
    created_hotkeys[0].callback()

    assert created_hotkeys[0].started is True
    assert created_hotkeys[0].shortcut.canonical == "Command-Option-R"
    assert created_runtimes[0].trigger_count == 1

    app._stop_region_translation_feature()

    assert created_hotkeys[0].stopped is True
    assert created_runtimes[0].closed is True


def test_disabled_feature_does_not_construct_global_hotkey(monkeypatch):
    monkeypatch.setattr(
        hotkey_module,
        "MacRegionTranslationHotKey",
        lambda _callback, _shortcut: pytest.fail(
            "disabled feature must not construct a hotkey"
        ),
    )
    app = CohelperApp.alloc().init()
    app.config = Config({"features": {"region_translation": False}})

    app._start_region_translation_feature()

    assert app.region_translation_hotkey is None


def test_hotkey_constructor_error_is_reported_without_masking_the_cause(monkeypatch):
    class Runtime:
        def __init__(self, _config, **_callbacks):
            pass

    def fail_constructor(_callback, _shortcut):
        raise HotKeyRegistrationError("Carbon is unavailable")

    errors = []
    monkeypatch.setattr(runtime_module, "RegionTranslationRuntime", Runtime)
    monkeypatch.setattr(hotkey_module, "MacRegionTranslationHotKey", fail_constructor)
    monkeypatch.setattr(
        cohelper_app.AppHelper,
        "callAfter",
        lambda callback, *args: callback(*args),
    )
    app = CohelperApp.alloc().init()
    app.config = Config({"features": {"region_translation": True}})
    app._region_translation_hotkey_error = lambda shortcut, error: errors.append(
        (shortcut, str(error))
    )

    app._start_region_translation_feature()

    assert app.region_translation_hotkey is None
    assert errors == [("⌥⇧T", "Carbon is unavailable")]


def test_hotkey_error_path_sanitizes_native_details_and_reports_conflict():
    app = CohelperApp.alloc().init()
    statuses = []
    errors = []
    app._set_status = lambda status: statuses.append(status)
    app._show_error = lambda title, message: errors.append((title, message))

    app._region_translation_hotkey_error(
        "⌥⇧T",
        HotKeyRegistrationError(
            "native status -9868", reason=HotKeyFailureReason.CONFLICT
        ),
    )

    assert statuses == ["cohelper (区域翻译快捷键不可用)"]
    assert errors == [
        (
            "区域翻译快捷键不可用",
            "⌥⇧T 可能已被其他应用或系统占用。"
            "请在高级配置中更换区域翻译快捷键，或从菜单栏点击“翻译屏幕区域”手动开始。",
        )
    ]


def test_manual_translation_latches_an_unavailable_hotkey_until_configuration_changes(monkeypatch):
    created_hotkeys = []

    class Runtime:
        def __init__(self, _config, **_callbacks):
            self.trigger_count = 0

        def trigger(self):
            self.trigger_count += 1

    class HotKey:
        def __init__(self, _callback, _shortcut):
            created_hotkeys.append(self)

        @property
        def is_running(self):
            return False

        def start(self):
            raise HotKeyRegistrationError("native status -9868")

    errors = []
    monkeypatch.setattr(runtime_module, "RegionTranslationRuntime", Runtime)
    monkeypatch.setattr(hotkey_module, "MacRegionTranslationHotKey", HotKey)
    monkeypatch.setattr(
        cohelper_app.AppHelper,
        "callAfter",
        lambda callback, *args: callback(*args),
    )
    app = CohelperApp.alloc().init()
    app.config = Config({"features": {"region_translation": True}})
    app._region_translation_hotkey_error = lambda *_args: errors.append("reported")

    app.translateRegion_(None)
    app.translateRegion_(None)

    assert len(created_hotkeys) == 1
    assert errors == ["reported"]
    assert app.region_translation_runtime.trigger_count == 2


def test_manual_region_translation_remains_usable_when_hotkey_cleanup_fails(monkeypatch):
    class Runtime:
        def __init__(self, _config, **_callbacks):
            self.trigger_count = 0

        def trigger(self):
            self.trigger_count += 1

    class HotKey:
        shortcut = parse_shortcut("Option-Shift-T")

        def __init__(self):
            self.stop_count = 0

        @property
        def is_armed(self):
            return False

        def stop(self):
            self.stop_count += 1
            raise HotKeyRegistrationError("native cleanup failed")

    monkeypatch.setattr(runtime_module, "RegionTranslationRuntime", Runtime)
    monkeypatch.setattr(
        cohelper_app.AppHelper,
        "callAfter",
        lambda callback, *args: callback(*args),
    )
    app = CohelperApp.alloc().init()
    app.config = Config({"features": {"region_translation": True}})
    hotkey = HotKey()
    errors = []
    app.region_translation_hotkey = hotkey
    app._region_translation_hotkey_error = lambda *_args: errors.append("reported")

    app.translateRegion_(None)
    app.translateRegion_(None)

    assert app.region_translation_runtime.trigger_count == 2
    assert hotkey.stop_count == 1
    assert errors == ["reported"]


def test_config_rebind_retries_a_previous_hotkey_cleanup_failure(monkeypatch):
    class HotKey:
        shortcut = parse_shortcut("Option-Shift-T")

        def __init__(self):
            self.stop_count = 0

        def stop(self):
            self.stop_count += 1
            if self.stop_count == 1:
                raise HotKeyRegistrationError("temporary native cleanup failure")

    monkeypatch.setattr(
        cohelper_app.AppHelper,
        "callAfter",
        lambda callback, *args: callback(*args),
    )
    app = CohelperApp.alloc().init()
    app.config = Config({"features": {"region_translation": True}})
    hotkey = HotKey()
    app.region_translation_hotkey = hotkey
    app._region_translation_hotkey_error = lambda *_args: None

    app._stop_region_translation_feature()
    app._stop_region_translation_feature(retry_hotkey_cleanup=True)

    assert hotkey.stop_count == 2
    assert app.region_translation_hotkey is None
    assert app.region_translation_hotkey_cleanup_failed is False
