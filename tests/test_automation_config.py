from pathlib import Path
import stat

import pytest

from ai_drive.automation.config import AutomationConfigError, REPOSITORY_ROOT, parse_automation_config, update_scan_interval


def test_parses_external_rule_groups_with_safe_defaults(tmp_path: Path):
    config = parse_automation_config(
        {
            "scan_interval_seconds": 5,
            "groups": {
                "accept": {
                    "rules": [
                        {
                            "id": "accept-confirm",
                            "templates": [
                                {
                                    "path": "templates/accept.png",
                                    "confidence": 0.9,
                                    "region": [10, 20, 300, 200],
                                }
                            ],
                            "actions": [
                                {"type": "click", "guard_template": "templates/accept.png"},
                                {"type": "sound", "mode": "latched"},
                            ],
                            "success_when": {"template_disappears": "templates/accept.png", "timeout_seconds": 10},
                        }
                    ]
                }
            },
        },
        base_dir=tmp_path,
    )
    rule = config.groups["accept"].rules[0]
    assert config.scan_interval_seconds == 5
    assert rule.templates[0].path == tmp_path / "templates/accept.png"
    assert rule.templates[0].region == (10, 20, 300, 200)
    assert rule.actions[1].mode == "latched"


@pytest.mark.parametrize(
    "payload, error",
    [
        ({"scan_interval_seconds": 0.5, "groups": {}}, "scan_interval_seconds"),
    ],
)
def test_rejects_unsafe_or_invalid_configuration(tmp_path: Path, payload, error: str):
    with pytest.raises(AutomationConfigError, match=error):
        parse_automation_config(payload, base_dir=tmp_path)


def test_rejects_template_paths_inside_the_repository():
    config = parse_automation_config(
        {"groups": {"safe": {"rules": [{"id": "rule", "templates": [{"path": str(REPOSITORY_ROOT / "template.png")}], "actions": [{"type": "sound", "mode": "once"}]}]}}},
        base_dir=Path("/private/tmp"),
    )

    assert "outside the repository" in config.disabled_groups["safe"]


def test_disables_only_the_invalid_group_when_other_groups_are_valid(tmp_path: Path):
    config = parse_automation_config(
        {
            "groups": {
                "valid": {"rules": [{"id": "notice", "templates": [{"path": "ok.png"}], "actions": [{"type": "sound", "mode": "once"}]}]},
                "invalid": {"rules": [{"id": "unsafe", "templates": [{"path": "bad.png"}], "actions": [{"type": "shell"}]}]},
            }
        },
        base_dir=tmp_path,
    )

    assert tuple(config.groups) == ("valid",)
    assert "unsupported action type" in config.disabled_groups["invalid"]


def test_update_scan_interval_preserves_rules_and_revalidates(tmp_path: Path):
    path = tmp_path / "rules.yaml"
    path.write_text(
        """scan_interval_seconds: 5
groups:
  notice:
    rules:
      - id: notice
        templates:
          - path: notice.png
            confidence: 0.9
        actions:
          - type: sound
            mode: once
""",
        encoding="utf-8",
    )
    path.chmod(0o600)

    updated = update_scan_interval(path, 12)

    assert updated.scan_interval_seconds == 12
    assert "notice" in updated.groups
    assert "scan_interval_seconds: 12" in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(AutomationConfigError, match="between 1 and 300"):
        update_scan_interval(path, 0)
