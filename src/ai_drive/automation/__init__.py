"""Declarative, local-only screen automation capabilities."""

from ai_drive.automation.config import AutomationConfig, AutomationConfigError, parse_automation_config
from ai_drive.automation.actions import ActionOutcome, GuardedActionExecutor
from ai_drive.automation.control import AutomationController
from ai_drive.automation.matcher import OpenCVTemplateMatcher, TemplateMatch
from ai_drive.automation.engine import AutomationEngine, TriggerDecision
from ai_drive.automation.state import AutomationStateStore, RunState

__all__ = (
    "AutomationConfig",
    "AutomationConfigError",
    "AutomationController",
    "ActionOutcome",
    "AutomationEngine",
    "AutomationStateStore",
    "GuardedActionExecutor",
    "OpenCVTemplateMatcher",
    "RunState",
    "TriggerDecision",
    "TemplateMatch",
    "parse_automation_config",
)
