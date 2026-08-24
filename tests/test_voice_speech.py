from __future__ import annotations

from ai_drive.voice import AnswerSentenceBuffer


def test_sentence_buffer_emits_complete_chinese_and_english_sentences_only():
    buffer = AnswerSentenceBuffer(max_pending=2)

    assert buffer.feed(1, "第一句。第二") == [(1, "第一句。")]
    assert buffer.feed(1, "句！") == [(1, "第二句！")]
    assert buffer.finish(1) == []


def test_new_answer_generation_discards_old_sentences_and_only_keeps_one_pending():
    buffer = AnswerSentenceBuffer(max_pending=1)

    buffer.feed(1, "旧句一。旧句二。")
    assert buffer.feed(2, "新句。") == [(2, "新句。")]
    assert buffer.finish(2) == []


def test_finish_emits_short_tail_but_empty_or_partial_generation_is_not_spoken():
    buffer = AnswerSentenceBuffer(max_pending=2)

    assert buffer.feed(3, "这是一个未完") == []
    assert buffer.finish(3) == [(3, "这是一个未完")]
    assert buffer.finish(3) == []
