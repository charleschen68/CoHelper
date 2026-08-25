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
from .speech import AnswerSentenceBuffer, MacSpeechOutput, SpeechOutputError
from .router import VoiceCommandRouter, VoiceCommandRouterError, VoiceRoute, VoiceRouteKind
from .direct import VoiceDirectActionError, VoiceDirectIntent, VoiceDirectTarget, VoiceDirectTargetStore
from .action_bridge import VoiceActionBridgeError, VoiceCommandActionBridge, PreparedVoiceAction
from .safety import VoiceActionSafetyError, VoiceActionSafetyGate
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
    "AnswerSentenceBuffer",
    "MacSpeechOutput",
    "SpeechOutputError",
    "VoiceCommandRouter",
    "VoiceCommandRouterError",
    "VoiceRoute",
    "VoiceRouteKind",
    "VoiceDirectActionError",
    "VoiceDirectIntent",
    "VoiceDirectTarget",
    "VoiceDirectTargetStore",
    "VoiceActionBridgeError",
    "VoiceCommandActionBridge",
    "PreparedVoiceAction",
    "VoiceActionSafetyError",
    "VoiceActionSafetyGate",
    "PushToTalkController",
    "PushToTalkEvent",
]
