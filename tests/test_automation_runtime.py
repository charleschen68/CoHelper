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
    assert runtime.status() == "service: ready\naccept: armed"


def test_emergency_stop_disarms_and_requires_explicit_resume(tmp_path: Path):
    rule = RuleSpec("accept", (TemplateSpec(Path("/target.png"), 0.9),), (ActionSpec("sound", mode="once"),))
    runtime = AutomationRuntime({"accept": RuleGroup("accept", (rule,))}, AutomationStateStore(tmp_path / "state.sqlite3"), FakeExecutor())
    runtime.arm("accept")
    runtime.emergency_stop()

    assert runtime.armed_rules() == ()
    try:
        runtime.arm("accept")
    except RuntimeError as exc:
        assert "emergency-stopped" in str(exc)
    else:
        raise AssertionError("arm must remain blocked until resume")
    runtime.resume()
    runtime.arm("accept")


def test_notification_failure_cannot_fail_or_repeat_a_completed_local_action(tmp_path: Path):
    rule = RuleSpec("accept", (TemplateSpec(Path("/target.png"), 0.9),), (ActionSpec("sound", mode="once"),))
    runtime = AutomationRuntime(
        {"accept": RuleGroup("accept", (rule,))},
        AutomationStateStore(tmp_path / "state.sqlite3"),
        FakeExecutor(),
        notify=lambda _: (_ for _ in ()).throw(OSError("queue unavailable")),
    )
    runtime.arm("accept")

    outcome = runtime.scan({"accept": True})

    assert outcome is not None and outcome.succeeded


def test_while_present_alarm_stops_when_its_rule_disappears(tmp_path: Path):
    rule = RuleSpec("warning", (TemplateSpec(Path("/target.png"), 0.9),), (ActionSpec("sound", mode="while_present"),))
    stopped = []
    runtime = AutomationRuntime(
        {"warning": RuleGroup("warning", (rule,))},
        AutomationStateStore(tmp_path / "state.sqlite3"),
        FakeExecutor(),
        stop_alarm=lambda: stopped.append(True),
    )
    runtime.arm("warning")

    runtime.scan({"warning": False})

    assert stopped == [True]
