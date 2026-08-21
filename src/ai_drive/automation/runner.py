"""Single-frame scan loop adapter for a configured automation runtime."""

from __future__ import annotations

from typing import Protocol, Sequence

from ai_drive.automation.actions import ActionOutcome
from ai_drive.automation.config import RuleSpec, TemplateSpec
from ai_drive.automation.runtime import AutomationRuntime


class FrameCapture(Protocol):
    def capture(self): ...


class TemplateMatcher(Protocol):
    def locate(self, frame, template: TemplateSpec): ...


class AutomationRunner:
    def __init__(self, runtime: AutomationRuntime, rules: Sequence[RuleSpec], capture: FrameCapture, matcher: TemplateMatcher):
        self._runtime = runtime
        self._rules = tuple(rules)
        self._capture = capture
        self._matcher = matcher

    def scan_once(self) -> ActionOutcome | None:
        frame = self._capture.capture()
        matches = {
            rule.id: any(self._matcher.locate(frame, template) is not None for template in rule.templates)
            for rule in self._rules
        }
        return self._runtime.scan(matches)
