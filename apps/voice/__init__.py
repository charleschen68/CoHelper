"""macOS-only microphone adapters for the voice input feature."""

from .microphone import MacMicrophoneCapture, MicrophoneCaptureError
from .hotkey_monitor import HotkeyMonitorError, MacPushToTalkMonitor

__all__ = [
    "HotkeyMonitorError",
    "MacMicrophoneCapture",
    "MacPushToTalkMonitor",
    "MicrophoneCaptureError",
]
