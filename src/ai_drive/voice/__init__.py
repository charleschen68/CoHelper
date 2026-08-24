"""Pure voice-session contracts used by platform audio adapters."""

from .audio import (
    PcmRingBuffer,
    VoiceActivityDetector,
    VoiceWorker,
    VoiceWorkerConfig,
    VoiceWorkerError,
    WhisperCppWorker,
    WhisperCppWorkerConfig,
)
from .input import VoiceInputCoordinator, VoiceInputError
from .hotkey import PushToTalkController, PushToTalkEvent
from .session import VoiceSession, VoiceSessionError, VoiceSessionState, VoiceTranscript

__all__ = [
    "PcmRingBuffer",
    "VoiceActivityDetector",
    "VoiceSession",
    "VoiceSessionError",
    "VoiceSessionState",
    "VoiceTranscript",
    "VoiceWorker",
    "VoiceWorkerConfig",
    "VoiceWorkerError",
    "WhisperCppWorker",
    "WhisperCppWorkerConfig",
    "VoiceInputCoordinator",
    "VoiceInputError",
    "PushToTalkController",
    "PushToTalkEvent",
]
