"""Guarded, fail-stop execution for declarative automation actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ai_drive.automation.config import ActionSpec, RuleSpec, TemplateSpec


class AutomationOutput(Protocol):
    def click(self, template: Path, offset: tuple[float, float]) -> None: ...
    def type_text(self, text: str) -> None: ...
    def press_key(self, key: str) -> None: ...
    def sound(self, mode: str) -> None: ...
    def telegram(self) -> None: ...


@dataclass(frozen=True)
class ActionOutcome:
    succeeded: bool
    failed_step: str | None = None
    detail: str = ""


class GuardedActionExecutor:
    """Execute only configured action types and stop at the first unsafe step."""

    def __init__(
        self,
        output: AutomationOutput,
        *,
        guard_matches: Callable[[Path], bool],
        get_secret: Callable[[str], str | None] = lambda _: None,
        wait_for: Callable[[TemplateSpec, str, float], bool] = lambda *_: False,
        should_stop: Callable[[], bool] = lambda: False,
    ):
        self._output = output
        self._guard_matches = guard_matches
        self._get_secret = get_secret
        self._wait_for = wait_for
        self._should_stop = should_stop

    def execute(self, rule: RuleSpec) -> ActionOutcome:
        for action in rule.actions:
            if self._should_stop():
                return ActionOutcome(False, action.kind, "automation is stopped")
            try:
                self._execute_action(action)
            except (OSError, RuntimeError, ValueError) as exc:
                return ActionOutcome(False, action.kind, str(exc))
        return ActionOutcome(True)

    def _execute_action(self, action: ActionSpec) -> None:
        if action.kind in {"click", "type_text", "press_key"}:
            assert action.guard_template is not None
            if not self._guard_matches(action.guard_template):
                raise RuntimeError("guard template is not present")
        if action.kind == "click":
            assert action.guard_template is not None
            self._output.click(action.guard_template, action.offset)
        elif action.kind == "type_text":
            text = action.text
            if action.keychain_ref is not None:
                text = self._get_secret(action.keychain_ref)
            if not text:
                raise RuntimeError("configured text is unavailable")
            self._output.type_text(text)
        elif action.kind == "press_key":
            assert action.key is not None
            self._output.press_key(action.key)
        elif action.kind == "wait_for_template":
            assert action.wait_for is not None and action.mode is not None and action.timeout_seconds is not None
            if not self._wait_for(action.wait_for, action.mode, action.timeout_seconds):
                raise RuntimeError("template wait timed out")
        elif action.kind == "sound":
            assert action.mode is not None
            self._output.sound(action.mode)
        elif action.kind == "telegram":
            self._output.telegram()
        else:  # pragma: no cover - configuration validation prevents this path.
            raise ValueError(f"unsupported action type: {action.kind}")
