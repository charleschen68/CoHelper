from pathlib import Path

from ai_drive.automation.config import ActionSpec, RuleSpec, TemplateSpec
from ai_drive.automation.engine import AutomationEngine, RunState
from ai_drive.automation.state import AutomationStateStore


def _rule(rule_id: str, priority: int = 0) -> RuleSpec:
    return RuleSpec(
        rule_id,
        (TemplateSpec(Path(f"/{rule_id}.png"), 0.9),),
        (ActionSpec("sound", mode="once"),),
        priority=priority,
    )


def test_only_highest_priority_matching_rule_starts_and_action_is_persisted(tmp_path: Path):
    store = AutomationStateStore(tmp_path / "state.sqlite3")
    engine = AutomationEngine((_rule("lower", 1), _rule("higher", 9)), store)

    decision = engine.scan({"lower": True, "higher": True})

    assert decision is not None
    assert decision.rule_id == "higher"
    assert store.snapshot("higher").run_state is RunState.EXECUTING
    assert store.snapshot("lower").run_state is RunState.IDLE


def test_rule_rearms_only_after_two_consecutive_absent_scans(tmp_path: Path):
    store = AutomationStateStore(tmp_path / "state.sqlite3")
    engine = AutomationEngine((_rule("accept"),), store)

    assert engine.scan({"accept": True}).rule_id == "accept"
    assert engine.scan({"accept": True}) is None
    assert engine.scan({"accept": False}) is None
    assert engine.scan({"accept": False}) is None

    assert engine.scan({"accept": True}).rule_id == "accept"


def test_restart_marks_incomplete_action_unknown_and_never_replays_until_rearmed(tmp_path: Path):
    path = tmp_path / "state.sqlite3"
    first = AutomationStateStore(path)
    first.begin("accept")
    first.close()

    restarted = AutomationStateStore(path)
    engine = AutomationEngine((_rule("accept"),), restarted)

    assert restarted.snapshot("accept").run_state is RunState.UNKNOWN
    assert engine.scan({"accept": True}) is None
    engine.scan({"accept": False})
    engine.scan({"accept": False})
    assert engine.scan({"accept": True}).rule_id == "accept"


def test_atomic_claim_allows_only_one_concurrent_candidate(tmp_path: Path):
    store = AutomationStateStore(tmp_path / "state.sqlite3")

    assert store.claim("accept") is True
    assert store.claim("accept") is False
