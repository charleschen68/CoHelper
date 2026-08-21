"""Declarative, local-only screen automation capabilities."""

from ai_drive.automation.config import AutomationConfig, AutomationConfigError, parse_automation_config
from ai_drive.automation.actions import ActionOutcome, GuardedActionExecutor
from ai_drive.automation.control import AutomationController
from ai_drive.automation.matcher import OpenCVTemplateMatcher, TemplateMatch
from ai_drive.automation.runtime import AutomationRuntime
from ai_drive.automation.runner import AutomationRunner
from ai_drive.automation.macos import QuartzFrameCapture
from ai_drive.automation.macos_output import QuartzAutomationOutput
from ai_drive.automation.sound import SystemAlarm
from ai_drive.automation.socket import AutomationSocketProtocol
from ai_drive.automation.socket import AutomationUnixSocketServer
from ai_drive.automation.engine import AutomationEngine, TriggerDecision
from ai_drive.automation.state import AutomationStateStore, RunState

__all__ = (
    "AutomationConfig",
    "AutomationConfigError",
    "AutomationController",
    "ActionOutcome",
    "AutomationEngine",
    "AutomationStateStore",
    "AutomationRuntime",
    "AutomationRunner",
    "QuartzFrameCapture",
    "QuartzAutomationOutput",
    "SystemAlarm",
    "AutomationSocketProtocol",
    "AutomationUnixSocketServer",
    "GuardedActionExecutor",
    "OpenCVTemplateMatcher",
    "RunState",
    "TriggerDecision",
    "TemplateMatch",
    "parse_automation_config",
)
