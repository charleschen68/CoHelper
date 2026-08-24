"""AppKit adapter for the platform-independent overlay state model."""

from __future__ import annotations

import time

from AppKit import (
    NSAppearance,
    NSAppearanceNameVibrantDark,
    NSBackingStoreBuffered,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSScrollView,
    NSTextView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowAnimationBehaviorUtilityWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)

from ai_drive.output import (
    OutputEvent,
    OutputKind,
    OutputSeverity,
    OverlayModel,
    OverlaySnapshot,
)
from ai_drive.vision import OverlayMask


def _utf16_length(value: str) -> int:
    """Return the index length Cocoa expects for NSRange values."""
    return len(value.encode("utf-16-le")) // 2


class OutputOverlayController:
    """Own the click-through AppKit panel while delegating behavior to OverlayModel."""

    def __init__(self, *, model: OverlayModel | None = None, now=time.monotonic):
        self._model = model or OverlayModel()
        self._now = now
        self._panel = None
        self._text_view = None

    def publish(self, event: OutputEvent) -> None:
        self._apply(self._model.apply(event, now=self._now()))

    def tick(self) -> None:
        self._apply(self._model.tick(now=self._now()))

    def reposition(self) -> None:
        if self._panel is not None:
            self._panel.setFrame_display_(self._panel_frame(), True)

    def close(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel.close()
        self._panel = None
        self._text_view = None

    def current_mask(self) -> OverlayMask | None:
        """Return the visible panel frame for screenshot masking."""
        if self._panel is None or not self._model.snapshot().visible:
            return None
        return self._mask_from_frame(self._panel.frame())

    @staticmethod
    def _mask_from_frame(frame) -> OverlayMask:
        return OverlayMask(
            float(frame.origin.x),
            float(frame.origin.y),
            float(frame.size.width),
            float(frame.size.height),
        )

    def _apply(self, snapshot: OverlaySnapshot) -> None:
        if not snapshot.visible:
            if self._panel is not None:
                self._panel.orderOut_(None)
            return
        self._ensure_panel()
        assert self._panel is not None and self._text_view is not None
        segments = self._render_segments(snapshot)
        rendered = "\n\n".join(text for text, _event in segments)
        self._text_view.setString_(rendered)
        event_count = sum(event is not None for _text, event in segments)
        event_index = 0
        offset = 0
        for text, event in segments:
            if event is None:
                color = NSColor.whiteColor()
            elif event.kind is OutputKind.EMERGENCY_STOP:
                color = NSColor.systemRedColor()
                event_index += 1
            elif event.kind is OutputKind.ACTION and event.severity in {
                OutputSeverity.ERROR,
                OutputSeverity.CRITICAL,
            }:
                color = NSColor.systemOrangeColor()
                event_index += 1
            else:
                alpha = 0.4 + 0.6 * ((event_index + 1) / max(1, event_count))
                color = NSColor.whiteColor().colorWithAlphaComponent_(alpha)
                event_index += 1
            self._text_view.textStorage().addAttribute_value_range_(
                NSForegroundColorAttributeName,
                color,
                (offset, _utf16_length(text)),
            )
            offset += _utf16_length(text) + 2
        if rendered:
            self._text_view.scrollRangeToVisible_((_utf16_length(rendered), 0))
        self._panel.orderFrontRegardless()

    def _ensure_panel(self) -> None:
        if self._panel is not None:
            return
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            self._panel_frame(), style, NSBackingStoreBuffered, False
        )
        panel.setLevel_(NSFloatingWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setIgnoresMouseEvents_(True)
        panel.setAnimationBehavior_(NSWindowAnimationBehaviorUtilityWindow)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )

        bounds = panel.contentView().bounds()
        effect = NSVisualEffectView.alloc().initWithFrame_(bounds)
        effect.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        effect.setMaterial_(NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(NSVisualEffectStateActive)
        effect.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameVibrantDark))
        effect.setWantsLayer_(True)
        effect.layer().setCornerRadius_(18)
        effect.layer().setMasksToBounds_(True)
        panel.setContentView_(effect)

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(18, 18, bounds.size.width - 36, bounds.size.height - 36))
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        scroll.setHasVerticalScroller_(False)
        scroll.setDrawsBackground_(False)

        text = NSTextView.alloc().initWithFrame_(scroll.bounds())
        text.setAutoresizingMask_(NSViewWidthSizable)
        text.setEditable_(False)
        text.setSelectable_(False)
        text.setDrawsBackground_(False)
        text.setTextColor_(NSColor.whiteColor())
        text.setFont_(NSFont.systemFontOfSize_weight_(16, 0.18))
        text.textContainer().setWidthTracksTextView_(True)
        text.textContainer().setContainerSize_((scroll.contentSize().width, 100_000))
        scroll.setDocumentView_(text)
        effect.addSubview_(scroll)

        self._panel = panel
        self._text_view = text

    @staticmethod
    def _render(snapshot: OverlaySnapshot) -> str:
        return "\n\n".join(text for text, _event in OutputOverlayController._render_segments(snapshot))

    @staticmethod
    def _render_segments(snapshot: OverlaySnapshot) -> list[tuple[str, OutputEvent | None]]:
        lines: list[tuple[str, OutputEvent | None]] = []
        events = list(snapshot.entries)
        if snapshot.emergency_event is not None and all(
            event.event_id != snapshot.emergency_event.event_id for event in events
        ):
            events.insert(0, snapshot.emergency_event)
        for event in events:
            marker = {
                OutputKind.ACTION: "操作",
                OutputKind.ANSWER_FINAL: "知识回答",
                OutputKind.DETECTION: "目标检测",
                OutputKind.EMERGENCY_CLEARED: "安全状态",
                OutputKind.EMERGENCY_STOP: "紧急停止",
                OutputKind.ERROR: "错误",
                OutputKind.KNOWLEDGE_SOURCES: "知识来源",
                OutputKind.TEXT_INPUT: "输入",
                OutputKind.TRANSCRIPT_FINAL: "你",
                OutputKind.TRANSLATION: "翻译",
            }.get(event.kind, event.title)
            lines.append((f"{marker}\n{event.message}", event))
        if snapshot.active_transcript:
            lines.append((f"正在聆听…\n{snapshot.active_transcript}", None))
        if snapshot.active_answer:
            lines.append((f"正在回答…\n{snapshot.active_answer}", None))
        return lines

    @staticmethod
    def _panel_frame():
        screens = list(NSScreen.screens())
        screen = screens[0] if screens else None
        if screen is None:
            return NSMakeRect(16, 180, 420, 420)
        visible = screen.visibleFrame()
        width = min(520.0, max(360.0, visible.size.width * 0.28))
        height = min(640.0, max(320.0, visible.size.height * 0.45))
        x = visible.origin.x + 16
        y = visible.origin.y + (visible.size.height - height) / 2
        return NSMakeRect(x, y, width, height)
