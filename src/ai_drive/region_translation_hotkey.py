"""Permission-free macOS global hotkey for explicit region translation."""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass
from typing import Callable

from ai_drive.shortcuts import ShortcutSpec


_LOGGER = logging.getLogger(__name__)


class HotKeyRegistrationError(RuntimeError):
    """Raised when the region-translation shortcut cannot be registered."""

    def __init__(self, message: str, *, registration=None):
        super().__init__(message)
        self.registration = registration


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("event_class", ctypes.c_uint32), ("event_kind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("identifier", ctypes.c_uint32)]


_EventHandler = ctypes.CFUNCTYPE(
    ctypes.c_int32,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
)


@dataclass
class _CarbonRegistration:
    hot_key_ref: ctypes.c_void_p | None
    handler_ref: ctypes.c_void_p | None
    handler: object


def _four_char_code(value: str) -> int:
    if len(value) != 4 or not value.isascii():
        raise ValueError("four-character code must contain exactly four ASCII characters")
    return int.from_bytes(value.encode("ascii"), "big")


class _CarbonHotKeyBackend:
    """Small typed adapter around the macOS Carbon global-hotkey API."""

    EVENT_NOT_HANDLED = -9874
    EVENT_HOTKEY_EXISTS = -9878
    EVENT_CLASS_KEYBOARD = _four_char_code("keyb")
    EVENT_HOTKEY_PRESSED = 5
    EVENT_PARAM_DIRECT_OBJECT = _four_char_code("----")
    TYPE_EVENT_HOTKEY_ID = _four_char_code("hkid")
    SIGNATURE = _four_char_code("CoRT")
    IDENTIFIER = 1

    def __init__(self, carbon=None):
        if carbon is None:
            try:
                carbon = ctypes.CDLL(
                    "/System/Library/Frameworks/Carbon.framework/Carbon"
                )
            except OSError as exc:
                raise HotKeyRegistrationError(
                    "macOS global hotkey API is unavailable"
                ) from exc
        self._carbon = carbon
        self._configure_functions()

    def _configure_functions(self) -> None:
        self._carbon.GetApplicationEventTarget.argtypes = []
        self._carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        self._carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p,
            _EventHandler,
            ctypes.c_ulong,
            ctypes.POINTER(_EventTypeSpec),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._carbon.InstallEventHandler.restype = ctypes.c_int32
        self._carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        self._carbon.RemoveEventHandler.restype = ctypes.c_int32
        self._carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            _EventHotKeyID,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._carbon.RegisterEventHotKey.restype = ctypes.c_int32
        self._carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        self._carbon.UnregisterEventHotKey.restype = ctypes.c_int32
        self._carbon.GetEventParameter.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.c_void_p,
        ]
        self._carbon.GetEventParameter.restype = ctypes.c_int32

    def register(self, key_code, modifiers, callback, *, exclusive):
        hot_key_id = _EventHotKeyID(self.SIGNATURE, self.IDENTIFIER)

        @_EventHandler
        def handler(_call_ref, event_ref, _user_data):
            received = _EventHotKeyID()
            status = self._carbon.GetEventParameter(
                event_ref,
                self.EVENT_PARAM_DIRECT_OBJECT,
                self.TYPE_EVENT_HOTKEY_ID,
                None,
                ctypes.sizeof(received),
                None,
                ctypes.byref(received),
            )
            if status != 0 or (
                received.signature != hot_key_id.signature
                or received.identifier != hot_key_id.identifier
            ):
                return self.EVENT_NOT_HANDLED
            try:
                callback()
            except Exception as exc:
                _LOGGER.error(
                    "region translation hotkey callback failed with sanitized error type %s",
                    type(exc).__name__,
                )
            return 0

        target = self._carbon.GetApplicationEventTarget()
        hot_key_ref = ctypes.c_void_p()
        status = self._carbon.RegisterEventHotKey(
            int(key_code),
            int(modifiers),
            hot_key_id,
            target,
            1 if exclusive else 0,
            ctypes.byref(hot_key_ref),
        )
        if status != 0:
            if status == self.EVENT_HOTKEY_EXISTS:
                raise HotKeyRegistrationError(
                    "global shortcut is already registered by another application"
                )
            raise HotKeyRegistrationError(
                f"failed to register global shortcut (status {status})"
            )

        event_type = _EventTypeSpec(
            self.EVENT_CLASS_KEYBOARD,
            self.EVENT_HOTKEY_PRESSED,
        )
        handler_ref = ctypes.c_void_p()
        status = self._carbon.InstallEventHandler(
            target,
            handler,
            1,
            ctypes.byref(event_type),
            None,
            ctypes.byref(handler_ref),
        )
        if status != 0:
            rollback_status = self._carbon.UnregisterEventHotKey(hot_key_ref)
            if rollback_status != 0:
                registration = _CarbonRegistration(hot_key_ref, None, handler)
                raise HotKeyRegistrationError(
                    "failed to install macOS hotkey handler "
                    f"(status {status}); rollback also failed with status "
                    f"{rollback_status}",
                    registration=registration,
                )
            raise HotKeyRegistrationError(
                f"failed to install macOS hotkey handler (status {status})"
            )
        return _CarbonRegistration(hot_key_ref, handler_ref, handler)

    def unregister(self, registration) -> None:
        failures = []
        if registration.hot_key_ref is not None:
            status = self._carbon.UnregisterEventHotKey(registration.hot_key_ref)
            if status == 0:
                registration.hot_key_ref = None
            else:
                failures.append(f"unregister status {status}")
        if registration.handler_ref is not None:
            status = self._carbon.RemoveEventHandler(registration.handler_ref)
            if status == 0:
                registration.handler_ref = None
            else:
                failures.append(f"handler removal status {status}")
        if failures:
            raise HotKeyRegistrationError(
                "failed to clean up global shortcut: " + ", ".join(failures)
            )


class MacRegionTranslationHotKey:
    """Own one configured global shortcut registration for the enabled runtime."""

    def __init__(
        self,
        on_pressed: Callable[[], None],
        shortcut: ShortcutSpec,
        *,
        backend=None,
    ):
        self._on_pressed = on_pressed
        self._shortcut = shortcut
        self._backend = backend or _CarbonHotKeyBackend()
        self._registration = None

    @property
    def is_running(self) -> bool:
        return self._registration is not None

    @property
    def shortcut(self) -> ShortcutSpec:
        return self._shortcut

    def start(self) -> None:
        if self._registration is not None:
            return
        try:
            self._registration = self._backend.register(
                self._shortcut.carbon_key_code,
                self._shortcut.carbon_modifiers,
                self._on_pressed,
                # Never suppress another application's non-exclusive binding.
                exclusive=False,
            )
        except HotKeyRegistrationError as exc:
            if exc.registration is not None:
                self._registration = exc.registration
            raise

    def stop(self) -> None:
        if self._registration is None:
            return
        self._backend.unregister(self._registration)
        self._registration = None


__all__ = ["HotKeyRegistrationError", "MacRegionTranslationHotKey"]
