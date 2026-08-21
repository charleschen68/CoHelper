"""Deterministic rule selection independent of screen and input adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from ai_drive.automation.config import RuleSpec
from ai_drive.automation.state import AutomationStateStore, RunState


@dataclass(frozen=True)
class TriggerDecision:
    rule_id: str
    rule: RuleSpec


class AutomationEngine:
    """Select at most one matching rule from each fresh screen observation."""

    def __init__(self, rules: Sequence[RuleSpec], state: AutomationStateStore):
        self._rules = tuple(rules)
        self._state = state
        if len({rule.id for rule in self._rules}) != len(self._rules):
            raise ValueError("rule IDs must be unique")

    def scan(self, matches: Mapping[str, bool]) -> TriggerDecision | None:
        for rule in self._rules:
            if bool(matches.get(rule.id, False)):
                self._state.observe_present(rule.id)
            else:
                self._state.observe_absent(rule.id)
        eligible = [
            rule
            for rule in self._rules
            if matches.get(rule.id, False) and self._state.can_trigger(rule.id)
        ]
        if not eligible:
            return None
        selected = max(eligible, key=lambda rule: (rule.priority, rule.id))
        # Write ahead of any irreversible output. A restart will become UNKNOWN.
        self._state.begin(selected.id)
        return TriggerDecision(selected.id, selected)


__all__ = ("AutomationEngine", "RunState", "TriggerDecision")
