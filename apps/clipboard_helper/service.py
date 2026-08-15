"""Public clipboard processing seam for the menu-bar application."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class ClipboardKind(Enum):
    QUESTION = "question"
    TERM = "term"
    PARAGRAPH = "paragraph"


class Translator(Protocol):
    def translate(self, text: str) -> str: ...


class KnowledgeSearcher(Protocol):
    def search(self, query: str) -> list[str]: ...


class KnowledgeAnswerer(Protocol):
    def answer(self, query: str, sources: list[str], kind: ClipboardKind) -> str: ...


@dataclass(frozen=True)
class ClipboardResult:
    kind: ClipboardKind
    query: str
    translation: str | None = None
    sources: list[str] = field(default_factory=list)
    answer: str | None = None


@dataclass(frozen=True)
class ClipboardFeatures:
    translation: bool = True
    search: bool = True
    answer: bool = True

    def __post_init__(self) -> None:
        if self.answer and not self.search:
            raise ValueError("answer requires search")


class ClipboardAssistant:
    def __init__(
        self,
        translator: Translator,
        searcher: KnowledgeSearcher,
        answerer: KnowledgeAnswerer,
        features: ClipboardFeatures | None = None,
    ):
        self._translator = translator
        self._searcher = searcher
        self._answerer = answerer
        self._features = features or ClipboardFeatures()

    def process(self, text: str) -> ClipboardResult:
        kind = classify_clipboard_text(text)
        stripped = text.strip()
        query = f"什么是 {stripped}？" if kind is ClipboardKind.TERM else stripped
        translation = self._translator.translate(text) if self._features.translation else None
        sources = self._searcher.search(query) if self._features.search else []
        if self._features.answer:
            answer = self._answerer.answer(query, sources, kind) if sources else "知识库中没有足够依据。"
        else:
            answer = None
        return ClipboardResult(kind, query, translation, sources, answer)


def classify_clipboard_text(text: str) -> ClipboardKind:
    stripped = text.strip()
    question_markers = ("为什么", "为何", "怎么", "如何", "什么", "谁", "哪里", "哪一个", "是否", "能否", "请问")
    if stripped.endswith(("?", "？")) or stripped.startswith(question_markers):
        return ClipboardKind.QUESTION
    if "\n" not in stripped and len(stripped) <= 32 and not any(mark in stripped for mark in "。.!！；;"):
        return ClipboardKind.TERM
    return ClipboardKind.PARAGRAPH
