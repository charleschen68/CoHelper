"""External, validated configuration for the screen automation service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


DEFAULT_CONFIG_PATH = Path.home() / "Library" / "Application Support" / "cohelper" / "automation" / "rules.yaml"
_ACTION_TYPES = frozenset({"click", "type_text", "press_key", "wait_for_template", "sound", "telegram"})
_SOUND_MODES = frozenset({"once", "while_present", "latched"})


class AutomationConfigError(ValueError):
    """Raised when an automation configuration cannot safely run."""


@dataclass(frozen=True)
class TemplateSpec:
    path: Path
    confidence: float
    region: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class ActionSpec:
    kind: str
    guard_template: Path | None = None
    offset: tuple[float, float] = (0.0, 0.0)
    mode: str | None = None
    text: str | None = None
    keychain_ref: str | None = None
    key: str | None = None
    wait_for: TemplateSpec | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class SuccessCondition:
    template: Path
    present: bool
    timeout_seconds: float


@dataclass(frozen=True)
class RuleSpec:
    id: str
    templates: tuple[TemplateSpec, ...]
    actions: tuple[ActionSpec, ...]
    success_when: SuccessCondition | None = None
    priority: int = 0


@dataclass(frozen=True)
class RuleGroup:
    name: str
    rules: tuple[RuleSpec, ...]


@dataclass(frozen=True)
class AutomationConfig:
    scan_interval_seconds: float
    groups: Mapping[str, RuleGroup]
    source_path: Path | None = None

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "AutomationConfig":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise AutomationConfigError(f"cannot read automation configuration: {exc}") from exc
        except yaml.YAMLError as exc:
            raise AutomationConfigError(f"cannot parse automation configuration: {exc}") from exc
        config = parse_automation_config(payload, base_dir=path.parent)
        return cls(config.scan_interval_seconds, config.groups, path)


def parse_automation_config(payload: object, *, base_dir: Path) -> AutomationConfig:
    root = _mapping(payload, "configuration")
    interval = _number(root.get("scan_interval_seconds", 5), "scan_interval_seconds")
    if not 1 <= interval <= 300:
        raise AutomationConfigError("scan_interval_seconds must be between 1 and 300")
    groups_payload = _mapping(root.get("groups", {}), "groups")
    groups: dict[str, RuleGroup] = {}
    rule_ids: set[str] = set()
    for group_name, group_payload in groups_payload.items():
        if not isinstance(group_name, str) or not group_name.strip():
            raise AutomationConfigError("group names must be non-empty strings")
        group_map = _mapping(group_payload, f"groups.{group_name}")
        rules_payload = _list(group_map.get("rules", []), f"groups.{group_name}.rules")
        rules = tuple(_parse_rule(item, base_dir, group_name, rule_ids) for item in rules_payload)
        if not rules:
            raise AutomationConfigError(f"groups.{group_name}.rules must not be empty")
        groups[group_name] = RuleGroup(group_name, rules)
    return AutomationConfig(interval, groups)


def _parse_rule(payload: object, base_dir: Path, group_name: str, known_ids: set[str]) -> RuleSpec:
    value = _mapping(payload, f"groups.{group_name}.rules")
    rule_id = _string(value.get("id"), "rule id")
    if rule_id in known_ids:
        raise AutomationConfigError(f"duplicate rule id: {rule_id}")
    known_ids.add(rule_id)
    templates = tuple(_parse_template(item, base_dir) for item in _list(value.get("templates"), f"rule {rule_id}.templates"))
    if not templates:
        raise AutomationConfigError(f"rule {rule_id} requires at least one template")
    actions = tuple(_parse_action(item, base_dir, rule_id) for item in _list(value.get("actions"), f"rule {rule_id}.actions"))
    if not actions:
        raise AutomationConfigError(f"rule {rule_id} requires at least one action")
    success = _parse_success(value.get("success_when"), base_dir, rule_id)
    if success is None and any(action.kind in {"click", "type_text", "press_key"} for action in actions):
        raise AutomationConfigError(f"rule {rule_id} requires success_when for screen output")
    priority = value.get("priority", 0)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise AutomationConfigError(f"rule {rule_id}.priority must be an integer")
    return RuleSpec(rule_id, templates, actions, success, priority)


def _parse_template(payload: object, base_dir: Path) -> TemplateSpec:
    value = _mapping(payload, "template")
    path = _path(value.get("path"), base_dir, "template.path")
    confidence = _number(value.get("confidence", 0.9), "template.confidence")
    if not 0.8 <= confidence <= 0.99:
        raise AutomationConfigError("template confidence must be between 0.80 and 0.99")
    region_value = value.get("region")
    region = None
    if region_value is not None:
        values = _list(region_value, "template.region")
        if len(values) != 4 or any(not isinstance(item, int) or isinstance(item, bool) for item in values):
            raise AutomationConfigError("template.region must contain four integers")
        x, y, width, height = values
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise AutomationConfigError("template.region must be within the main display")
        region = (x, y, width, height)
    return TemplateSpec(path, confidence, region)


def _parse_action(payload: object, base_dir: Path, rule_id: str) -> ActionSpec:
    value = _mapping(payload, f"rule {rule_id}.action")
    kind = _string(value.get("type"), f"rule {rule_id}.action.type")
    if kind not in _ACTION_TYPES:
        raise AutomationConfigError(f"unsupported action type: {kind}")
    guard = _path(value["guard_template"], base_dir, "action.guard_template") if "guard_template" in value else None
    if kind in {"click", "type_text", "press_key"} and guard is None:
        raise AutomationConfigError(f"{kind} requires guard_template")
    if kind == "click":
        offset = _offset(value.get("offset", [0, 0]))
        return ActionSpec(kind, guard_template=guard, offset=offset)
    if kind == "type_text":
        text = value.get("text")
        keychain_ref = value.get("keychain_ref")
        if bool(text is not None) == bool(keychain_ref is not None):
            raise AutomationConfigError("type_text requires exactly one of text or keychain_ref")
        return ActionSpec(kind, guard_template=guard, text=_string(text, "action.text") if text is not None else None, keychain_ref=_string(keychain_ref, "action.keychain_ref") if keychain_ref is not None else None)
    if kind == "press_key":
        return ActionSpec(kind, guard_template=guard, key=_string(value.get("key"), "action.key"))
    if kind == "sound":
        mode = _string(value.get("mode"), "sound.mode")
        if mode not in _SOUND_MODES:
            raise AutomationConfigError("sound.mode must be once, while_present, or latched")
        return ActionSpec(kind, mode=mode)
    if kind == "telegram":
        return ActionSpec(kind)
    wait_for = _parse_template(value.get("template"), base_dir)
    state = _string(value.get("state"), "wait_for_template.state")
    if state not in {"present", "absent"}:
        raise AutomationConfigError("wait_for_template.state must be present or absent")
    return ActionSpec(kind, wait_for=wait_for, mode=state, timeout_seconds=_timeout(value.get("timeout_seconds", 10), "wait_for_template.timeout_seconds"))


def _parse_success(payload: object, base_dir: Path, rule_id: str) -> SuccessCondition | None:
    if payload is None:
        return None
    value = _mapping(payload, f"rule {rule_id}.success_when")
    present = "template_appears" in value
    absent = "template_disappears" in value
    if present == absent:
        raise AutomationConfigError("success_when requires exactly one template_appears or template_disappears")
    path = _path(value["template_appears"] if present else value["template_disappears"], base_dir, "success_when template")
    return SuccessCondition(path, present, _timeout(value.get("timeout_seconds", 10), "success_when.timeout_seconds"))


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AutomationConfigError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise AutomationConfigError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AutomationConfigError(f"{name} must be a non-empty string")
    return value.strip()


def _number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AutomationConfigError(f"{name} must be a number")
    return float(value)


def _timeout(value: object, name: str) -> float:
    timeout = _number(value, name)
    if not 1 <= timeout <= 300:
        raise AutomationConfigError(f"{name} must be between 1 and 300")
    return timeout


def _path(value: object, base_dir: Path, name: str) -> Path:
    text = _string(value, name)
    path = Path(text).expanduser()
    return path if path.is_absolute() else base_dir / path


def _offset(value: object) -> tuple[float, float]:
    values = _list(value, "click.offset")
    if len(values) != 2:
        raise AutomationConfigError("click.offset must contain two numbers")
    return (_number(values[0], "click.offset[0]"), _number(values[1], "click.offset[1]"))
