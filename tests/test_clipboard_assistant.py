from apps.clipboard_helper.service import (
    ClipboardAssistant,
    ClipboardFeatures,
    ClipboardKind,
    route_clipboard_text,
)
from cohelper_core import Config, ModelResult, ModelService, TaskCallbacks, TaskCoordinator
from ai_drive.model_scheduler import DEFAULT_MODEL_SCHEDULER


class FakeTranslator:
    def translate(self, text: str) -> str:
        return f"translated:{text}"


class FakeKnowledge:
    def search(self, query: str) -> list[str]:
        return [f"source:{query}"]


class FakeAnswerer:
    def answer(self, query: str, sources: list[str], kind: ClipboardKind) -> str:
        return f"answer:{kind.value}:{query}:{sources[0]}"


def test_question_is_translated_searched_and_answered():
    assistant = ClipboardAssistant(FakeTranslator(), FakeKnowledge(), FakeAnswerer())

    result = assistant.process("Flink 为什么需要 checkpoint？")

    assert result.kind is ClipboardKind.QUESTION
    assert result.translation == "translated:Flink 为什么需要 checkpoint？"
    assert result.query == "Flink 为什么需要 checkpoint？"
    assert result.sources == ["source:Flink 为什么需要 checkpoint？"]
    assert result.answer.startswith("answer:question:")


def test_short_term_is_rewritten_as_a_question():
    assistant = ClipboardAssistant(FakeTranslator(), FakeKnowledge(), FakeAnswerer())

    result = assistant.process("flink")

    assert result.kind is ClipboardKind.TERM
    assert result.translation == "translated:flink"
    assert result.query == "什么是 flink？"
    assert result.sources == ["source:什么是 flink？"]


class MustNotRun:
    def __getattr__(self, name):
        raise AssertionError(f"disabled dependency was accessed: {name}")


def test_disabled_features_do_not_access_corresponding_resources():
    assistant = ClipboardAssistant(
        MustNotRun(),
        MustNotRun(),
        MustNotRun(),
        ClipboardFeatures(translation=False, search=False, answer=False),
    )

    result = assistant.process("flink")

    assert result.translation is None
    assert result.sources == []
    assert result.answer is None


def test_chinese_interrogative_without_question_mark_is_a_question():
    assistant = ClipboardAssistant(FakeTranslator(), FakeKnowledge(), FakeAnswerer())

    result = assistant.process("为什么 Flink 需要 checkpoint")

    assert result.kind is ClipboardKind.QUESTION
    assert result.query == "为什么 Flink 需要 checkpoint"


def test_paragraph_routes_to_source_grounded_summary():
    route = route_clipboard_text("Flink 使用 checkpoint 保存一致性状态。它可以在故障后恢复。")

    assert route.kind is ClipboardKind.PARAGRAPH
    assert route.task == "summarize"


def test_config_update_cancels_inflight_task_and_new_task_uses_new_snapshot(monkeypatch):
    import cohelper_core

    started = __import__("threading").Event()
    release = __import__("threading").Event()
    results = []
    seen_models = []

    class BlockingModelService:
        def __init__(self, config, kind):
            seen_models.append((kind, config.section(kind)["model"]))

        def run(self, text, system, cancel):
            started.set()
            release.wait(1)
            return ModelResult(text="done", provider="test")

    monkeypatch.setattr(cohelper_core, "ModelService", BlockingModelService)
    config = Config({"features": {"translation": True, "knowledge_search": False, "knowledge_answer": False}, "translation": {"model": "old"}})
    coordinator = TaskCoordinator(
        config,
        TaskCallbacks(on_translation=lambda _generation, result: results.append(result)),
    )

    coordinator.submit("enough text")
    assert started.wait(1)
    config_generation = coordinator.update_config(Config({"features": {"translation": True, "knowledge_search": False, "knowledge_answer": False}, "translation": {"model": "new"}}))
    release.set()
    __import__("time").sleep(0.05)

    assert seen_models == [("translation", "old")]
    assert results == []
    assert config_generation == 2


def test_model_service_uses_shared_local_model_lease(monkeypatch):
    import cohelper_core
    import threading

    calls = []

    class FakeProvider:
        def complete(self, system, user, **kwargs):
            calls.append(kwargs["model"])
            return "done"

    monkeypatch.setattr(cohelper_core, "make_provider", lambda _name: FakeProvider())
    config = Config(
        {
            "features": {"translation": True},
            "translation": {"base_url": "http://127.0.0.1:19999"},
        }
    )
    held = DEFAULT_MODEL_SCHEDULER.acquire("http://127.0.0.1:19999", "translategemma:4b")
    result = []
    worker = threading.Thread(
        target=lambda: result.append(ModelService(config, "translation").run("text", "system"))
    )
    worker.start()
    __import__("time").sleep(0.05)
    assert calls == []
    held.release()
    worker.join(1)

    assert result[0].text == "done"
    assert calls == ["translategemma:4b"]
