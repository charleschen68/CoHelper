from pathlib import Path

from ai_drive.automation.actions import GuardedActionExecutor
from ai_drive.automation.config import ActionSpec, RuleSpec, TemplateSpec


class FakeOutput:
    def __init__(self):
        self.calls = []

    def click(self, template, offset):
        self.calls.append(("click", template, offset))

    def type_text(self, text):
        self.calls.append(("type", text))

    def press_key(self, key):
        self.calls.append(("key", key))

    def sound(self, mode):
        self.calls.append(("sound", mode))

    def telegram(self):
        self.calls.append(("telegram",))


def _rule() -> RuleSpec:
    template = Path("/target.png")
    return RuleSpec(
        "auto",
        (TemplateSpec(template, 0.9),),
        (
            ActionSpec("click", guard_template=template, offset=(100, 0)),
            ActionSpec("type_text", guard_template=template, text="666666"),
            ActionSpec("press_key", guard_template=template, key="enter"),
        ),
    )


def test_executor_rechecks_each_irreversible_step_and_stops_on_guard_failure():
    output = FakeOutput()
    checks = iter((True, False))
    executor = GuardedActionExecutor(output, guard_matches=lambda _: next(checks))

    outcome = executor.execute(_rule())

    assert outcome.succeeded is False
    assert outcome.failed_step == "type_text"
    assert output.calls == [("click", Path("/target.png"), (100, 0))]


def test_executor_reads_keychain_reference_only_when_its_guard_matches():
    template = Path("/target.png")
    rule = RuleSpec(
        "secret",
        (TemplateSpec(template, 0.9),),
        (ActionSpec("type_text", guard_template=template, keychain_ref="automation.secret"),),
    )
    output = FakeOutput()
    reads = []
    executor = GuardedActionExecutor(
        output,
        guard_matches=lambda _: True,
        get_secret=lambda ref: reads.append(ref) or "secret-value",
    )

    assert executor.execute(rule).succeeded
    assert reads == ["automation.secret"]
    assert output.calls == [("type", "secret-value")]
