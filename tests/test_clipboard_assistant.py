from apps.clipboard_helper.service import (
    ClipboardAssistant,
    ClipboardFeatures,
    ClipboardKind,
    route_clipboard_text,
)


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
