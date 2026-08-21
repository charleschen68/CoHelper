from cohelper_core import Config
from cohelper_core import ConfigError
import pytest


def test_legacy_summary_flag_migrates_to_answer_flag():
    config = Config({"features": {"knowledge_summary": False}})

    assert config.enabled("knowledge_answer") is False
    assert "knowledge_summary" not in config.section("features")


def test_ai_drive_defaults_are_local_and_safely_allowlisted():
    config = Config({})

    assert config.section("vision")["model"] == "qwen2.5vl:7b"
    assert config.section("summary")["model"] == "qwen3:8b"
    assert config.section("actions")["allowed_bundle_ids"] == ["com.apple.Safari", "com.apple.TextEdit"]
    assert any("Reload this page" in item for item in config.section("actions")["allowed_capabilities"])
    assert config.section("telegram")["enabled"] is False


def test_previous_bundled_summary_model_migrates_to_qwen3_8b():
    config = Config(
        {"summary": {"model": "rafw007/qwen3.6-35b-A3b-mlx-claude-coder-abliterated:latest"}}
    )

    assert config.section("summary")["model"] == "qwen3:8b"


def test_action_safety_thresholds_are_validated():
    with pytest.raises(ConfigError, match="minimum_confidence"):
        Config({"actions": {"minimum_confidence": 1.1}})


@pytest.mark.parametrize(
    "summary",
    [
        {"provider": "openai-compatible"},
        {"model": "another-model"},
        {"base_url": "https://api.example.com"},
    ],
)
def test_answer_model_is_fixed_to_local_qwen3(summary):
    with pytest.raises(ConfigError, match="summary"):
        Config({"summary": summary})


def test_vision_model_and_confirmation_ttl_are_fixed():
    with pytest.raises(ConfigError, match="qwen2.5vl:7b"):
        Config({"vision": {"model": "another-model"}})
    with pytest.raises(ConfigError, match="固定为 30"):
        Config({"actions": {"confirmation_ttl_seconds": 31}})
