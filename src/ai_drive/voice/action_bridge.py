"""Bridge finalized voice commands to the existing guarded action workflow."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from .router import VoiceRoute, VoiceRouteKind


class VoiceActionBridgeError(ValueError):
    pass


class GuardedActionWorkflow(Protocol):
    def prepare(self, instruction: str, user_id: int, chat_id: int): ...

    def confirm(self, action_id: str, user_id: int, chat_id: int): ...

    def cancel(self, action_id: str, user_id: int, chat_id: int) -> None: ...


@dataclass(frozen=True)
class PreparedVoiceAction:
    utterance_id: str
    command: str
    action_id: str


class VoiceCommandActionBridge:
    """Prepare or confirm only explicit routed commands; never auto-confirms."""

    def __init__(
        self,
        workflow: GuardedActionWorkflow,
        instructions: dict[str, str],
        *,
        enabled: bool = True,
    ):
        self._workflow = workflow
        self._instructions = dict(instructions)
        self._enabled = enabled
        self._pending: dict[str, PreparedVoiceAction] = {}
        self._lock = threading.Lock()

    def prepare(
        self,
        route: VoiceRoute,
        *,
        utterance_id: str,
        user_id: int,
        chat_id: int,
    ) -> PreparedVoiceAction:
        if not self._enabled:
            raise VoiceActionBridgeError("voice direct actions are disabled")
        if not utterance_id.strip():
            raise VoiceActionBridgeError("utterance_id must be non-empty")
        if route.kind is not VoiceRouteKind.COMMAND or not route.command:
            raise VoiceActionBridgeError("only a routed command can prepare an action")
        instruction = self._instructions.get(route.command)
        if not instruction:
            raise VoiceActionBridgeError("command has no guarded action instruction")
        with self._lock:
            if utterance_id in self._pending:
                raise VoiceActionBridgeError("utterance already has a pending action")
        prepared = self._workflow.prepare(instruction, user_id, chat_id)
        action_id = getattr(prepared, "action_id", None)
        if not isinstance(action_id, str) or not action_id:
            raise VoiceActionBridgeError("guarded workflow returned no action id")
        result = PreparedVoiceAction(utterance_id, route.command, action_id)
        with self._lock:
            if utterance_id in self._pending:
                self._workflow.cancel(action_id, user_id, chat_id)
                raise VoiceActionBridgeError("utterance already has a pending action")
            self._pending[utterance_id] = result
        return result

    def confirm(self, prepared: PreparedVoiceAction, *, user_id: int, chat_id: int):
        self._take_pending(prepared)
        return self._workflow.confirm(prepared.action_id, user_id, chat_id)

    def cancel(self, prepared: PreparedVoiceAction, *, user_id: int, chat_id: int) -> None:
        self._take_pending(prepared)
        self._workflow.cancel(prepared.action_id, user_id, chat_id)

    def _take_pending(self, prepared: PreparedVoiceAction) -> None:
        with self._lock:
            current = self._pending.get(prepared.utterance_id)
            if current != prepared:
                raise VoiceActionBridgeError("voice action is unknown or already consumed")
            self._pending.pop(prepared.utterance_id)
