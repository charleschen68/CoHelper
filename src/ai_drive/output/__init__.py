"""Typed output events shared by CoHelper user-facing processes."""

from .events import OutputEvent, OutputEventError, OutputKind, OutputSeverity, OutputSource
from .model import OverlayModel, OverlaySnapshot
from .socket import OutputEventSocketClient, OutputEventSocketProtocol, OutputEventUnixSocketServer

__all__ = [
    "OutputEvent",
    "OutputEventError",
    "OutputKind",
    "OutputSeverity",
    "OutputSource",
    "OutputEventSocketProtocol",
    "OutputEventSocketClient",
    "OutputEventUnixSocketServer",
    "OverlayModel",
    "OverlaySnapshot",
]
