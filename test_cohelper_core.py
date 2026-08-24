import threading
import time

import pytest

from cohelper_core import (
    Config,
    ConfigError,
    KnowledgeHit,
    ModelService,
    OllamaProvider,
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
    assert config.enabled("overlay")


def test_overlay_feature_can_be_disabled_without_truthy_string_coercion():
    assert Config({"features": {"overlay": False}}).enabled("overlay") is False
    with pytest.raises(ConfigError, match="features.overlay"):
        Config({"features": {"overlay": "false"}})


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


def test_paragraph_prompt_uses_summary_routing():
    prompt = build_knowledge_prompt(
        "Flink keeps state. Checkpoints persist it。",
        [(KnowledgeHit("/wiki/flink.md"), "checkpoint content")],
        task="summarize",
    )

    assert "用户段落" in prompt
    assert "总结该段落与知识库来源的关系" in prompt
    assert "用户问题" not in prompt


def test_missing_sources_emit_insufficient_knowledge_answer(monkeypatch):
    config = Config({"features": {"translation": False}})
    answers = []
    finished = threading.Event()
    monkeypatch.setattr(QmdClient, "search", lambda self, query, cancel=None: [])
    coordinator = TaskCoordinator(
        config,
        TaskCallbacks(
            on_summary=lambda _generation, result: answers.append(result),
            on_finished=lambda _generation: finished.set(),
        ),
    )

    coordinator.submit("flink")

    assert finished.wait(1)
    assert [result.text for result in answers] == ["知识库中没有足够依据。"]


def test_disabled_modules_do_not_start(monkeypatch):
    config = Config({"features": {"translation": False, "knowledge_search": False, "knowledge_summary": False}})
    started = []
    coordinator = TaskCoordinator(
        config, TaskCallbacks(on_started=lambda _generation, _text: started.append(True))
    )
    coordinator.submit("hello")
    threading.Event().wait(0.02)
    assert not started


def test_task_callbacks_carry_the_submission_generation():
    config = Config(
        {"features": {"translation": True, "knowledge_search": False, "knowledge_answer": False}}
    )
    started = []
    coordinator = TaskCoordinator(
        config,
        TaskCallbacks(on_started=lambda generation, text: started.append((generation, text))),
    )

    first = coordinator.submit("first request")
    second = coordinator.submit("second request")

    assert (first, second) == (1, 2)
    assert started == [(1, "first request"), (2, "second request")]


def test_ignored_short_input_does_not_cancel_work_or_consume_a_generation():
    coordinator = TaskCoordinator(Config({"clipboard": {"min_chars": 3}}))

    assert coordinator.submit("x") is None
    assert coordinator._generation == 0


def test_oversized_clipboard_is_reported_without_starting_modules():
    config = Config({"clipboard": {"max_chars": 10}})
    started = []
    rejected = []
    coordinator = TaskCoordinator(
        config,
        TaskCallbacks(
            on_started=lambda _generation, text: started.append(text),
            on_rejected=lambda _generation, reason: rejected.append(reason),
        ),
    )
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


def test_ollama_stream_emits_deltas_and_closes_response(monkeypatch):
    class Response:
        is_redirect = False
        status_code = 200
        closed = False

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=False):
            assert decode_unicode is True
            return [
                '{"message":{"content":"第一句。"},"done":false}',
                '{"message":{"content":"第二句。"},"done":true}',
            ]

        def close(self):
            self.closed = True

    response = Response()
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: response)
    deltas = list(
        OllamaProvider().stream(
            "system",
            "user",
            model="qwen3:8b",
            base_url="http://127.0.0.1:11434",
            timeout=10,
        )
    )

    assert deltas == ["第一句。", "第二句。"]
    assert response.closed is True


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
    coordinator = TaskCoordinator(
        config,
        TaskCallbacks(on_translation=lambda _generation, result: results.append(result)),
    )
    cancelled = threading.Event()
    cancelled.set()
    coordinator._translation(0, "hello", cancelled)
    assert results == []
