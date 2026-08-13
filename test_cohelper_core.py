import threading
import time

import pytest

from cohelper_core import (
    Config,
    ConfigError,
    KnowledgeHit,
    ModelService,
    QmdClient,
    TaskCoordinator,
    TaskCallbacks,
    TRANSLATION_SYSTEM,
    build_knowledge_prompt,
    contains_secret,
)


def test_defaults_and_feature_flags():
    config = Config({"features": {"translation": False}})
    assert not config.enabled("translation")
    assert config.enabled("knowledge_search")


def test_invalid_clipboard_bounds_are_rejected():
    with pytest.raises(ConfigError):
        Config({"clipboard": {"min_chars": 10, "max_chars": 2}})


def test_summary_requires_knowledge_search():
    with pytest.raises(ConfigError):
        Config({"features": {"knowledge_search": False, "knowledge_summary": True}})


def test_invalid_yaml_becomes_config_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("features: [", encoding="utf-8")
    with pytest.raises(ConfigError, match="无法读取配置文件"):
        Config.load(path)


def test_invalid_provider_is_rejected():
    with pytest.raises(ConfigError, match="不支持"):
        Config({"translation": {"provider": "unknown"}})


def test_non_mapping_section_is_a_config_error():
    with pytest.raises(ConfigError, match="features 必须是 mapping"):
        Config({"features": []})


def test_secret_detection_covers_private_key_and_api_key():
    assert contains_secret("api_key = 'abcdefghijklmnop'")
    assert contains_secret("-----BEGIN PRIVATE KEY-----")
    assert contains_secret("Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
    assert contains_secret("ghp_abcdefghijklmnopqrstuvwxyz123456")
    assert contains_secret("eyJabcdefghijk.abcdefghijklmnop.abcdefghijklmnop")
    assert not contains_secret("Flink checkpoint alignment")


def test_knowledge_prompt_contains_sources():
    prompt = build_knowledge_prompt("checkpoint", [(KnowledgeHit("/wiki/flink.md"), "checkpoint content")])
    assert "/wiki/flink.md" in prompt
    assert "checkpoint content" in prompt


def test_knowledge_prompt_caps_source_content():
    content = "a" * 2000
    prompt = build_knowledge_prompt("checkpoint", [(KnowledgeHit("/wiki/flink.md"), content)], max_source_chars=1000)
    assert "a" * 1000 in prompt
    assert "a" * 1001 not in prompt


def test_disabled_modules_do_not_start(monkeypatch):
    config = Config({"features": {"translation": False, "knowledge_search": False, "knowledge_summary": False}})
    started = []
    coordinator = TaskCoordinator(config, TaskCallbacks(on_started=lambda _: started.append(True)))
    coordinator.submit("hello")
    threading.Event().wait(0.02)
    assert not started


def test_oversized_clipboard_is_reported_without_starting_modules():
    config = Config({"clipboard": {"max_chars": 10}})
    started = []
    rejected = []
    coordinator = TaskCoordinator(config, TaskCallbacks(on_started=started.append, on_rejected=rejected.append))
    coordinator.submit("x" * 11)
    assert started == []
    assert "超过配置上限" in rejected[0]


def test_local_openai_compatible_endpoint_is_not_treated_as_external():
    config = Config({"translation": {"provider": "openai-compatible", "base_url": "http://127.0.0.1:4000"}})
    coordinator = TaskCoordinator(config)
    assert not coordinator._external_blocked("ordinary text", "translation")


def test_external_endpoint_is_blocked_by_default():
    config = Config({"translation": {"provider": "openai-compatible", "base_url": "https://api.example.com/v1"}})
    coordinator = TaskCoordinator(config)
    assert coordinator._external_blocked("ordinary text", "translation")


def test_model_redirect_is_rejected(monkeypatch):
    class Response:
        is_redirect = True

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: Response())
    result = ModelService(Config({}), "translation").run("hello", TRANSLATION_SYSTEM)
    assert "重定向" in result.error


def test_same_model_requests_are_serialized():
    active = 0
    maximum = 0
    guard = threading.Lock()

    class Provider:
        def complete(self, *args, **kwargs):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with guard:
                active -= 1
            return "ok"

    config = Config({})
    services = [ModelService(config, "translation"), ModelService(config, "translation")]
    for service in services:
        service.provider = Provider()
    threads = [threading.Thread(target=service.run, args=("hello", TRANSLATION_SYSTEM)) for service in services]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert maximum == 1


def test_qmd_communicate_retries_without_polling_full_pipes():
    class Process:
        def __init__(self):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise __import__("subprocess").TimeoutExpired("qmd", timeout)
            return ("x" * 100000, "")

    process = Process()
    stdout, stderr = QmdClient._communicate(process, 1, None, "QMD")
    assert len(stdout) == 100000
    assert process.calls == 2


def test_stale_blocked_translation_does_not_callback():
    config = Config(
        {
            "privacy": {"allow_external_api": False},
            "translation": {"provider": "openai-compatible", "base_url": "https://api.example.com/v1"},
        }
    )
    results = []
    coordinator = TaskCoordinator(config, TaskCallbacks(on_translation=results.append))
    cancelled = threading.Event()
    cancelled.set()
    coordinator._translation(0, "hello", cancelled)
    assert results == []
