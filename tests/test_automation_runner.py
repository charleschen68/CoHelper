from pathlib import Path

from ai_drive.automation.actions import ActionOutcome
from ai_drive.automation.config import ActionSpec, RuleGroup, RuleSpec, TemplateSpec
from ai_drive.automation.runner import AutomationRunner
from ai_drive.automation.runtime import AutomationRuntime
from ai_drive.automation.state import AutomationStateStore


class Capture:
    def __init__(self): self.calls = 0
    def capture(self): self.calls += 1; return "frame"


class Matcher:
    def locate(self, frame, template): return object() if template.path.name == "match.png" else None


class Executor:
    def execute(self, rule): return ActionOutcome(True)


def test_runner_captures_once_and_matches_all_armed_rules(tmp_path: Path):
    rule = RuleSpec("accept", (TemplateSpec(Path("/match.png"), .9),), (ActionSpec("sound", mode="once"),))
    runtime = AutomationRuntime({"accept": RuleGroup("accept", (rule,))}, AutomationStateStore(tmp_path / "state.sqlite"), Executor())
    runtime.arm("accept")
    capture = Capture()

    outcome = AutomationRunner(runtime, (rule,), capture, Matcher()).scan_once()

    assert outcome is not None and outcome.succeeded
    assert capture.calls == 1
