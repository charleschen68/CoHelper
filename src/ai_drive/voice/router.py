"""Deterministic finalized-transcript routing without action execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Sequence


class VoiceCommandRouterError(ValueError):
    pass


class VoiceRouteKind(StrEnum):
    COMMAND = "command"
    KNOWLEDGE = "knowledge"
    PENDING = "pending"


@dataclass(frozen=True)
class VoiceRoute:
    kind: VoiceRouteKind
    text: str
    command: str | None = None


class VoiceCommandRouter:
    """Match exact registered phrases; this class has no action capability."""

    def __init__(self, commands: Mapping[str, Sequence[str]]):
        aliases: dict[str, str] = {}
        for command, phrases in commands.items():
            if not isinstance(command, str) or not command.strip():
                raise VoiceCommandRouterError("命令名不能为空")
            if not isinstance(phrases, Sequence) or isinstance(phrases, (str, bytes)) or not phrases:
                raise VoiceCommandRouterError(f"命令 {command} 必须有至少一个短语")
            for phrase in phrases:
                normalized = _normalize(phrase)
                if not normalized.endswith("执行"):
                    raise VoiceCommandRouterError(f"命令短语必须以执行结尾: {phrase}")
                previous = aliases.get(normalized)
                if previous is not None and previous != command:
                    raise VoiceCommandRouterError(f"命令短语冲突: {phrase}")
                aliases[normalized] = command
        self._aliases = aliases

    def route(self, text: str, *, finalized: bool) -> VoiceRoute:
        normalized = _normalize(text)
        if not finalized:
            return VoiceRoute(VoiceRouteKind.PENDING, normalized)
        if not normalized:
            raise VoiceCommandRouterError("最终语音文本不能为空")
        command = self._aliases.get(normalized)
        if command is not None:
            return VoiceRoute(VoiceRouteKind.COMMAND, normalized, command)
        if _looks_like_command(normalized):
            if _contains_mixed_request(normalized):
                raise VoiceCommandRouterError("语音文本混合了命令和知识问题")
            raise VoiceCommandRouterError("最终语音命令未注册")
        return VoiceRoute(VoiceRouteKind.KNOWLEDGE, normalized)


def _normalize(text: str) -> str:
    if not isinstance(text, str):
        raise VoiceCommandRouterError("语音文本必须是字符串")
    return " ".join(text.strip().split()).rstrip("。！？!?；;")


def _looks_like_command(text: str) -> bool:
    return text.endswith("执行") or "执行" in text or text.startswith(("打开", "关闭", "刷新", "点击", "点它"))


def _contains_mixed_request(text: str) -> bool:
    return any(marker in text for marker in ("然后", "并且", "同时", "再", "查询", "什么是", "解释"))
