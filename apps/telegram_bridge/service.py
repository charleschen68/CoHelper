"""Telegram command protocol independent of the network SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class PreparedClick(Protocol):
    action_id: str
    preview: bytes


class ClickWorkflow(Protocol):
    def prepare(self, instruction: str, user_id: int, chat_id: int) -> PreparedClick: ...
    def confirm(self, action_id: str, user_id: int, chat_id: int) -> bytes: ...
    def cancel(self, action_id: str, user_id: int, chat_id: int) -> None: ...


class ChatResponder(Protocol):
    def answer(self, text: str) -> str: ...


@dataclass(frozen=True)
class Reply:
    text: str
    image: bytes | None = None


class TelegramCommandHandler:
    def __init__(self, workflow: ClickWorkflow, chat: ChatResponder):
        self._workflow = workflow
        self._chat = chat

    def handle(self, text: str, *, user_id: int, chat_id: int) -> Reply:
        stripped = text.strip()
        command, _, argument = stripped.partition(" ")
        if command == "/click":
            if not argument.strip():
                return Reply("用法：/click <目标描述>")
            prepared = self._workflow.prepare(argument.strip(), user_id, chat_id)
            return Reply(
                f"待确认操作：{prepared.action_id}\n"
                f"/confirm {prepared.action_id}\n/cancel {prepared.action_id}",
                prepared.preview,
            )
        if command == "/confirm":
            if not argument.strip():
                return Reply("用法：/confirm <操作编号>")
            image = self._workflow.confirm(argument.strip(), user_id, chat_id)
            return Reply(f"操作 {argument.strip()} 已执行。", image)
        if command == "/cancel":
            if not argument.strip():
                return Reply("用法：/cancel <操作编号>")
            self._workflow.cancel(argument.strip(), user_id, chat_id)
            return Reply(f"操作 {argument.strip()} 已取消。")
        return Reply(self._chat.answer(stripped))
