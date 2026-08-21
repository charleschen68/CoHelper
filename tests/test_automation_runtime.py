from pathlib import Path

from ai_drive.automation.actions import ActionOutcome
from ai_drive.automation.config import ActionSpec, RuleGroup, RuleSpec, TemplateSpec
from ai_drive.automation.runtime import AutomationRuntime
from ai_drive.automation.state import AutomationStateStore, RunState


class FakeExecutor:
    def execute(self, rule):
        return ActionOutcome(True)


def test_runtime_arms_named_group_executes_once_and_finishes_state(tmp_path: Path):
    rule = RuleSpec("accept", (TemplateSpec(Path("/target.png"), 0.9),), (ActionSpec("sound", mode="once"),))
    state = AutomationStateStore(tmp_path / "state.sqlite3")
    runtime = AutomationRuntime({"accept": RuleGroup("accept", (rule,))}, state, FakeExecutor())

    runtime.arm("accept")

    outcome = runtime.scan({"accept": True})

    assert outcome is not None and outcome.succeeded
    assert state.snapshot("accept").run_state is RunState.SUCCEEDED
    assert runtime.status() == "accept: armed"
