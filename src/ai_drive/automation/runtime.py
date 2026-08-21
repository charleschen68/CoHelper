"""Composition root for one locally armed automation service."""

from __future__ import annotations

from typing import Mapping, Protocol

from ai_drive.automation.actions import ActionOutcome
from ai_drive.automation.config import RuleGroup, RuleSpec
from ai_drive.automation.engine import AutomationEngine
from ai_drive.automation.state import AutomationStateStore


class RuleExecutor(Protocol):
    def execute(self, rule: RuleSpec) -> ActionOutcome: ...


class AutomationRuntime:
    """Owns armed groups, serial scanning, and terminal state transitions."""

    def __init__(self, groups: Mapping[str, RuleGroup], state: AutomationStateStore, executor: RuleExecutor):
        self._groups = dict(groups)
        self._state = state
        self._executor = executor
        self._armed: set[str] = set()

    def arm(self, group: str) -> None:
        if group not in self._groups:
            raise ValueError(f"unknown automation group: {group}")
        self._armed.add(group)

    def disarm(self, group: str) -> None:
        if group == "all":
            self._armed.clear()
        elif group in self._groups:
            self._armed.discard(group)
        else:
            raise ValueError(f"unknown automation group: {group}")

    def status(self) -> str:
        return "\n".join(f"{name}: {'armed' if name in self._armed else 'disarmed'}" for name in sorted(self._groups))

    def scan(self, matches: Mapping[str, bool]) -> ActionOutcome | None:
        rules = tuple(rule for name in sorted(self._armed) for rule in self._groups[name].rules)
        if not rules:
            return None
        decision = AutomationEngine(rules, self._state).scan(matches)
        if decision is None:
            return None
        outcome = self._executor.execute(decision.rule)
        self._state.finish(decision.rule_id, outcome.succeeded)
        return outcome
