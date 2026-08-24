# Region Screenshot Translation

Status: approved design; implementation started on 2026-08-24.

This document is the cross-session source of truth for CoHelper's explicit
screen-region translation feature. Implementation status belongs in
[`progress.md`](../progress.md); a requirement in this document is not evidence
that it has been implemented or accepted on macOS.

## Goals

- Let the user explicitly select one region on the display under the pointer.
- Extract readable text with local `qwen2.5vl:7b`, then translate it with local
  `translategemma:4b`.
- Keep the frozen source image and the translated result aligned to the
  original selection rectangle.
- Make OCR errors distinguishable from translation errors through three views:
  original image, recognized text, and translated text.
- Make cancellation, privacy, feature gating, and stale-result rejection
  observable and testable.

## Non-goals

- Continuous screen observation, automatic capture, or background OCR.
- Cross-display selections or preserving the source document's visual layout.
- Per-word, per-line, speech-bubble, or coordinate-preserving replacement.
- Cloud OCR, cloud translation, silent provider fallback, history, notifications,
  Telegram delivery, speech output, QMD retrieval, or action execution.
- Pure-keyboard region selection in the first version.

## User interaction

`features.region_translation` is independent and disabled by default. When it
is false, CoHelper does not register its shortcut, request Screen Recording,
load its modules, diagnose its models, or call them. Enabling it registers a
configurable global shortcut whose default is `Option-Shift-T`, and adds a
menu-bar item named `翻译屏幕区域`.

Triggering either entry point starts one explicit selection session on the
display containing the pointer. A selection cannot cross displays and must be
at least 120 by 80 logical points. `Escape` cancels without capturing or calling
a model. A shortcut registration conflict leaves the menu item available,
reports the conflict, and never overrides another binding.

When selection finishes, CoHelper hides all of its own overlays, waits for the
next composited frame, captures the selected region, and restores only the
windows that had been visible. The captured image is frozen. The underlying
application may continue to operate, but the translation panel remains fixed
to the selection's display-local screen coordinates and cannot be moved or
resized.

The panel does not steal focus when capture or inference completes. It accepts
focus only after the user interacts with it, and returns focus to the
previously active application when closed. It belongs only to the Space where
the selection occurred. Clicking outside does not close it. It closes on its
close button, `Escape`, a new selection, feature disablement, lock, sleep, user
switch, or display reconfiguration; it is not restored automatically.

The result panel uses a high-contrast system material and reflows text within
the original rectangle. Overflow scrolls. A compact toolbar provides:

- `原图 | 识别文本 | 译文`, defaulting to `译文` when ready;
- detected and target language, with Chinese/English target switching;
- explicit copy, retry, and close actions.

The toolbar is placed above the selection when possible, then below it, and
inside its top edge only when neither outside position fits. Source and
translated text are selectable. Text reaches the clipboard only after an
explicit copy button or normal selected-text `Command-C` operation.

## Processing contract

The processing sequence is:

```text
IDLE -> SELECTING -> CAPTURED -> WAITING_OCR -> OCR_READY
     -> WAITING_TRANSLATION -> READY
```

Every active state may transition to a classified `FAILED` state or to
`CANCELLED`. One monotonically increasing session generation owns the capture,
OCR text, target language, translation, and window. A callback whose generation
is not current cannot update state or UI. Coordinator notifications are ordered
but never execute external UI code under a coordinator lock; the AppKit adapter
must retain the newest observed generation and reject older snapshots at the
main-thread rendering boundary.

Only one session may exist. Starting a new selection cancels and closes the old
session before entering selection. Retry reuses exactly the same frozen image;
obtaining newer pixels requires a new selection. Target-language changes reuse
recognized text, cancel only translation, and start a new translation
generation. Results are committed atomically rather than rendered as model
streaming deltas.

### Text extraction

`ScreenshotTextExtractor` is separate from the clickable-target
`VisionAnalyzer`. Both may share local transport code, but their prompts,
response types, and validation cannot be combined. The extractor fixes the
model to `qwen2.5vl:7b` and accepts exactly this logical response:

```json
{
  "found_text": true,
  "text": "recognized text in reading order",
  "detected_language": "zh"
}
```

Extra or missing fields, Markdown fences, empty text, an unsupported language,
or `found_text=false` with non-null result fields are rejected. No-text results
do not call translation. Recognized content longer than 20,000 characters is
rejected without truncation and tells the user to select a smaller region.

The model extracts content in reading order. It preserves line breaks, code,
commands, paths, URLs, version strings, numbers, and proper nouns. It does not
need to return layout boxes in version one. The inference image may be
downscaled to a bounded long edge, but the original frozen capture remains the
source for the `原图` view.

### Translation

`RegionTranslationService` is independent of clipboard translation and does
not use the clipboard translation system prompt. It fixes the model to
`translategemma:4b`, uses only a loopback Ollama endpoint, and accepts recognized
text plus an explicit target of Chinese or English. Chinese source defaults to
English; other source defaults to Chinese. The user can override the target.

Screenshot text is untrusted data. The translation prompt treats instructions
inside that data as content to translate. This pipeline has no QMD, action,
shell, Python, Accessibility, Telegram, notification, generic output-overlay,
or speech capability. It translates natural language while preserving code,
commands, paths, URLs, versions, numbers, and proper nouns.

Output validation compares exact multisets for machine-recognizable URLs,
POSIX and Windows paths, command flags, backtick-delimited code, version strings,
and numbers. Natural-language command names, identifiers, and proper nouns do
not have a reliable language-independent parser; their preservation remains a
prompt requirement and a mandatory benchmark/human-review acceptance item,
rather than a false deterministic guarantee.

## Scheduling and cancellation

OCR and translation each have a 60-second execution timeout. Waiting for the
shared local-model scheduler has a separate 30-second limit. The UI distinguishes
`等待本地模型` from model execution. The same local model runs one CoHelper
request at a time; explicit region translation is queued ahead of background
clipboard work but does not preempt an unrelated request already executing.

Logical result suppression is insufficient cancellation. Region-translation
HTTP clients must use a closeable response so cancellation closes the transport
and releases its model lease before a replacement request starts. If shutdown
is not prompt, the new session displays `正在停止上一任务` rather than running a
second inference concurrently. Internally streamed transport may be accumulated,
but the UI publishes only a complete validated result.

## Privacy and failures

The screenshot, recognized text, prompt, and translation remain in memory and
are released after session cleanup. CoHelper does not intentionally persist,
cache, log, upload, notify, speak, or remotely transmit their contents. Logs may
contain state transitions, generations, model names, timings, sanitized error
types, and character counts. This is not a claim that application code can
control macOS swap or crash-diagnostic behavior.

Screen Recording is requested only on the first real trigger. When it is
missing, selection does not start. The UI explains the problem and offers an
explicit button to open the relevant System Settings pane, then tells the user
to restart CoHelper. It does not poll indefinitely or claim that a changed
permission is already effective.

User-visible failures distinguish at least:

- Screen Recording unavailable;
- no readable text;
- vision model unavailable or timed out;
- translation model unavailable or timed out;
- local model busy;
- invalid model response;
- recognized text over the 20,000-character limit.

Failures retain the original image and expose retry where retry is meaningful.
Raw exceptions and content are never shown or logged.

## Public test seams

The approved TDD seams are:

1. `ScreenshotTextExtractor`: strict OCR response parsing and rejection.
2. `RegionTranslationService`: direction, prompt isolation, preservation rules,
   local fixed-model boundary, output validation, and cancellation.
3. `RegionTranslationCoordinator`: state transitions, single-session ownership,
   target switching without new OCR, retry with the same capture, timeout,
   cleanup, and stale-generation suppression.

AppKit selection, display coordinates, toolbar placement, focus, Space behavior,
permissions, global shortcuts, and lifecycle events require adapter tests plus
real macOS acceptance. Tests must observe public behavior rather than private
methods.

## Acceptance

- Automated tests cover the three public seams, timeout/cancellation races,
  feature disablement, multi-display coordinate conversion, selection limits,
  and classified failures.
- A private, sanitized benchmark contains at least 20 screenshots spanning
  Chinese, English, small text, Retina scaling, light/dark UI, code, URLs,
  mixed-language text, and low contrast. Clear-text mean OCR character error
  rate is at most 5%, and no sample may hallucinate a whole sentence.
- Human translation review verifies that numbers, code, URLs, and proper nouns
  remain intact.
- Real macOS acceptance covers shortcut, secondary-display selection, OCR,
  automatic direction, language change, all three views, scrolling, copying,
  retry, close, permission denial, unavailable models, lock/sleep cleanup, and
  display reconfiguration.
- Network observation confirms that the feature contacts only the configured
  loopback Ollama endpoint, and diagnostics confirm that no content is written
  to logs or application storage.
