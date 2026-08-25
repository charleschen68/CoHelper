"""Small AppKit renderer for the explicit region-translation comparison panel."""

from __future__ import annotations

from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSImage,
    NSImageView,
    NSMakeRect,
    NSPanel,
    NSPasteboard,
    NSPasteboardTypeString,
    NSPopUpButton,
    NSScrollView,
    NSTextView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSFloatingWindowLevel,
)

from ai_drive.region_translation import TranslationTarget
from ai_drive.region_translation_panel import (
    RegionTranslationPanelSnapshot,
    RegionTranslationView,
)


class RegionTranslationPanelController:
    """Render one frozen screenshot and its OCR/translation comparison views."""

    def __init__(self, runtime, *, on_error=None):
        self._runtime = runtime
        self._on_error = on_error or (lambda _error: None)
        self._panel = None
        self._image_view = None
        self._text_view = None
        self._target_popup = None
        self._copy_button = None
        self._retry_button = None
        self._snapshot = None

    def show(self, snapshot: RegionTranslationPanelSnapshot) -> None:
        self._snapshot = snapshot
        if self._panel is None:
            self._build(snapshot)
        self._update(snapshot)
        self._present_panel(self._panel)

    def close(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel.close()
            self._panel = None

    def windowWillClose_(self, _notification) -> None:
        self._panel = None
        try:
            self._runtime.close()
        except Exception as exc:
            self._on_error(exc)

    @staticmethod
    def _present_panel(panel) -> None:
        """Show results without activating the accessory application."""
        panel.setHidesOnDeactivate_(False)
        panel.orderFrontRegardless()

    def _build(self, snapshot: RegionTranslationPanelSnapshot) -> None:
        width = max(360.0, float(snapshot.selection.width))
        height = max(220.0, float(snapshot.selection.height))
        frame = NSMakeRect(snapshot.selection.x, snapshot.selection.y, width, height)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, NSBackingStoreBuffered, False
        )
        self._panel.setTitle_("区域翻译")
        self._panel.setLevel_(NSFloatingWindowLevel)
        self._panel.setReleasedWhenClosed_(False)
        self._panel.setDelegate_(self)

        content = self._panel.contentView()
        bounds = content.bounds()
        toolbar_height = 36.0
        footer_height = 34.0
        button_width = 72.0
        for index, (title, view) in enumerate(
            (("原图", RegionTranslationView.ORIGINAL),
             ("识别文本", RegionTranslationView.RECOGNIZED),
             ("译文", RegionTranslationView.TRANSLATED))
        ):
            button = NSButton.alloc().initWithFrame_(
                NSMakeRect(8 + index * (button_width + 6), bounds.size.height - toolbar_height + 5,
                           button_width, 26)
            )
            button.setTitle_(title)
            button.setTag_(index)
            button.setTarget_(self)
            button.setAction_("selectView:")
            button.setAutoresizingMask_(NSViewHeightSizable)
            content.addSubview_(button)

        self._target_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(bounds.size.width - 150, bounds.size.height - toolbar_height + 5, 142, 26), False
        )
        self._target_popup.addItemWithTitle_("译为中文")
        self._target_popup.addItemWithTitle_("译为 English")
        self._target_popup.setTarget_(self)
        self._target_popup.setAction_("changeTarget:")
        self._target_popup.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        content.addSubview_(self._target_popup)

        body = NSMakeRect(8, footer_height, bounds.size.width - 16, bounds.size.height - toolbar_height - footer_height)
        self._image_view = NSImageView.alloc().initWithFrame_(body)
        self._image_view.setImageScaling_(1)  # NSImageScaleProportionallyUpOrDown
        self._image_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        content.addSubview_(self._image_view)

        scroll = NSScrollView.alloc().initWithFrame_(body)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self._text_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, body.size.width, body.size.height))
        self._text_view.setEditable_(False)
        self._text_view.setSelectable_(True)
        self._text_view.setFont_(self._text_view.font())
        scroll.setDocumentView_(self._text_view)
        content.addSubview_(scroll)

        self._copy_button = NSButton.alloc().initWithFrame_(NSMakeRect(8, 5, 80, 24))
        self._copy_button.setTitle_("复制")
        self._copy_button.setTarget_(self)
        self._copy_button.setAction_("copyText:")
        content.addSubview_(self._copy_button)

        self._retry_button = NSButton.alloc().initWithFrame_(NSMakeRect(94, 5, 80, 24))
        self._retry_button.setTitle_("重试")
        self._retry_button.setTarget_(self)
        self._retry_button.setAction_("retry:")
        content.addSubview_(self._retry_button)

    def _update(self, snapshot: RegionTranslationPanelSnapshot) -> None:
        if self._panel is None:
            return
        self._snapshot = snapshot
        image = None
        if snapshot.source_image:
            image = NSImage.alloc().initWithData_(snapshot.source_image)
        self._image_view.setImage_(image)
        is_original = snapshot.active_view is RegionTranslationView.ORIGINAL
        self._image_view.setHidden_(not is_original)
        self._text_view.superview().setHidden_(is_original)
        if snapshot.active_view is RegionTranslationView.RECOGNIZED:
            self._text_view.setString_(snapshot.recognized_text or "正在识别…")
        elif snapshot.active_view is RegionTranslationView.TRANSLATED:
            self._text_view.setString_(snapshot.translated_text or "正在翻译…")
        else:
            self._text_view.setString_("")
        self._copy_button.setEnabled_(snapshot.active_view is not RegionTranslationView.ORIGINAL)
        self._retry_button.setEnabled_(snapshot.retry_available)
        if snapshot.target is not None:
            self._target_popup.selectItemAtIndex_(0 if snapshot.target is TranslationTarget.CHINESE else 1)

    def selectView_(self, sender) -> None:
        views = (RegionTranslationView.ORIGINAL, RegionTranslationView.RECOGNIZED, RegionTranslationView.TRANSLATED)
        try:
            self._runtime.select_view(views[int(sender.tag())])
        except Exception as exc:
            self._on_error(exc)

    def changeTarget_(self, sender) -> None:
        target = TranslationTarget.CHINESE if sender.indexOfSelectedItem() == 0 else TranslationTarget.ENGLISH
        try:
            self._runtime.change_target(target)
        except Exception as exc:
            self._on_error(exc)

    def copyText_(self, _sender) -> None:
        try:
            text = self._runtime.copy_text()
            pasteboard = NSPasteboard.generalPasteboard()
            pasteboard.clearContents()
            pasteboard.setString_forType_(text, NSPasteboardTypeString)
        except Exception as exc:
            self._on_error(exc)

    def retry_(self, _sender) -> None:
        try:
            self._runtime.retry()
        except Exception as exc:
            self._on_error(exc)


__all__ = ["RegionTranslationPanelController"]
