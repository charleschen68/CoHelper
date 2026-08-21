"""Explicit, identity-bound control protocol for a local automation service."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Callable, Protocol


class AutomationService(Protocol):
    def arm(self, group: str) -> None: ...
    def disarm(self, group: str) -> None: ...
    def status(self) -> str: ...


@dataclass(frozen=True)
class PendingStart:
    code: str
    user_id: int
    chat_id: int
    group: str
    expires_at: float


class AutomationController:
    """Command handler shared by local CLI adapters and Telegram transport."""

    def __init__(
        self,
        service: AutomationService,
        *,
        allowed_user_id: int,
        allowed_chat_id: int,
        now: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: secrets.token_hex(4).upper(),
        confirmation_ttl_seconds: float = 30.0,
    ):
        self._service = service
        self._allowed_user_id = allowed_user_id
        self._allowed_chat_id = allowed_chat_id
        self._now = now
        self._token_factory = token_factory
        self._ttl = confirmation_ttl_seconds
        self._pending: PendingStart | None = None

    def handle(self, text: str, *, user_id: int, chat_id: int) -> str:
        if user_id != self._allowed_user_id or chat_id != self._allowed_chat_id:
            return "权限不足。"
        command, _, argument = text.strip().partition(" ")
        argument = argument.strip()
        if command == "/automation_status":
            return self._service.status()
        if command == "/automation_start":
            if not argument or argument == "all":
                return "用法：/automation_start <规则组>"
            code = self._token_factory()
            self._pending = PendingStart(code, user_id, chat_id, argument, self._now() + self._ttl)
            return f"待确认启动 {argument}：/automation_confirm {code}"
        if command == "/automation_confirm":
            pending = self._pending
            self._pending = None
            if (
                pending is None
                or pending.code != argument
                or pending.user_id != user_id
                or pending.chat_id != chat_id
                or pending.expires_at < self._now()
            ):
                return "启动确认不存在或已过期。"
            self._service.arm(pending.group)
            return f"已启动 {pending.group}。"
        if command == "/automation_stop":
            if not argument:
                return "用法：/automation_stop <规则组|all>"
            self._pending = None
            self._service.disarm(argument)
            return "已停止全部规则组。" if argument == "all" else f"已停止 {argument}。"
        return "支持：/automation_status、/automation_start、/automation_confirm、/automation_stop"
