from ai_drive.region_capture import RegionSelection
from ai_drive.region_translation import (
    ExtractedText,
    RegionTranslationFailure,
    RegionTranslationSnapshot,
    RegionTranslationState,
    TranslationTarget,
)
from ai_drive.region_translation_panel import RegionTranslationPanelModel, RegionTranslationView
from ai_drive.vision import Screenshot


def screenshot() -> Screenshot:
    return Screenshot(b"frozen", 600, 400, 200, 120, 7, 10.0, "app", 120, 80)


def selection() -> RegionSelection:
    return RegionSelection(7, 120, 80, 200, 120)


def test_panel_defaults_to_original_then_translation_and_supports_explicit_copy():
    model = RegionTranslationPanelModel(selection())
    model.apply(RegionTranslationSnapshot(1, RegionTranslationState.WAITING_OCR, screenshot=screenshot()))
    assert model.snapshot().active_view is RegionTranslationView.ORIGINAL

    model.apply(
        RegionTranslationSnapshot(
            1,
            RegionTranslationState.READY,
            screenshot=screenshot(),
            source=ExtractedText("hello", "en"),
            target=TranslationTarget.CHINESE,
            translation="你好",
        )
    )
    assert model.snapshot().active_view is RegionTranslationView.TRANSLATED
    assert model.snapshot().detected_language == "en"
    assert model.snapshot().target is TranslationTarget.CHINESE
    model.select_view(RegionTranslationView.RECOGNIZED)
    assert model.copy_text() == "hello"
    model.select_view(RegionTranslationView.TRANSLATED)
    assert model.copy_text() == "你好"
    assert model.select_target(TranslationTarget.ENGLISH) is TranslationTarget.ENGLISH
    assert model.snapshot().target is TranslationTarget.ENGLISH
    assert model.snapshot().active_view is RegionTranslationView.RECOGNIZED


def test_panel_rejects_stale_results_and_exposes_retry_after_failure():
    model = RegionTranslationPanelModel(selection())
    current = RegionTranslationSnapshot(
        3,
        RegionTranslationState.FAILED,
        screenshot=screenshot(),
        failure=RegionTranslationFailure.VISION_UNAVAILABLE,
    )
    assert model.apply(current)
    assert model.snapshot().retry_available is True
    assert model.request_retry() is True
    assert model.request_retry() is False

    stale = RegionTranslationSnapshot(
        2,
        RegionTranslationState.FAILED,
        screenshot=screenshot(),
        failure=RegionTranslationFailure.INVALID_TEXT_RESPONSE,
    )
    assert model.apply(stale) is False
    assert model.snapshot().generation == 3


def test_new_generation_clears_previous_text_before_ocr_finishes():
    model = RegionTranslationPanelModel(selection())
    model.apply(
        RegionTranslationSnapshot(
            1,
            RegionTranslationState.READY,
            screenshot=screenshot(),
            source=ExtractedText("old", "en"),
            target=TranslationTarget.CHINESE,
            translation="旧",
        )
    )
    model.apply(RegionTranslationSnapshot(2, RegionTranslationState.WAITING_OCR, screenshot=screenshot()))

    current = model.snapshot()
    assert current.recognized_text is None
    assert current.translated_text is None
    assert current.active_view is RegionTranslationView.ORIGINAL


def test_original_image_is_not_copyable_and_unavailable_views_are_rejected():
    model = RegionTranslationPanelModel(selection())
    model.apply(RegionTranslationSnapshot(1, RegionTranslationState.WAITING_OCR, screenshot=screenshot()))
    try:
        model.copy_text()
    except ValueError as exc:
        assert "original" in str(exc)
    else:
        raise AssertionError("original image must not be copied as text")
    try:
        model.select_view(RegionTranslationView.TRANSLATED)
    except ValueError as exc:
        assert "available" in str(exc)
    else:
        raise AssertionError("unavailable translation view must be rejected")
