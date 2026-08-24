# Voice, Overlay, and Guarded Direct Actions

Status: approved design; phased implementation started on 2026-08-22.

This document is the cross-session source of truth for CoHelper's local voice
input, knowledge answer speech, left-side overlay, and guarded voice actions.
Implementation status belongs in [progress.md](../progress.md); a design item in
this document is not evidence that the corresponding phase is implemented.

## Goals

- Capture explicit, local microphone sessions from the macOS menu-bar app.
- Show partial speech transcripts and grounded knowledge answers in a
  non-activating left-side overlay.
- Speak grounded answers with interruptible local system TTS.
- Let explicitly registered low-risk native capabilities execute from finalized
  voice commands without a second confirmation.
- Let “点它，执行” reference one fresh, unique, revalidated detection target.
- Feed automation detections and action outcomes through one typed output
  contract without granting output publishers control authority.

## Non-goals

- Always-on listening or wake-word activation.
- Cloud STT, cloud TTS, or silent cloud fallback.
- Persisting raw audio, transcripts, questions, or answers.
- Arbitrary LLM-planned actions, shell, Python, AppleScript, or unregistered
  visual-coordinate clicks.
- Direct voice actions in web content, sensitive controls, or multiple displays.
- A voice-only confirmation mechanism for actions outside the direct-action
  allowlist.

## Process boundaries

```text
CoHelper menu-bar app
  |-- global push-to-talk and menu session controls
  |-- AVAudioEngine microphone capture
  |-- left-side NSPanel / NSVisualEffectView overlay
  |-- AVSpeechSynthesizer
  |-- VoiceCommandRouter
  |-- OutputEvent Unix-socket listener
  `-- PCM stream ----------------------------.
                                                v
                                          STT Worker
                                          whisper.cpp
                                          VAD + partial/final events

Screen automation process -- OutputEvent --> menu-bar app
VoiceCommandRouter -------- guarded call --> capability/action service
```

The `.app` owns microphone permission and audio capture. Whisper inference runs
in a supervised worker so a native inference crash cannot take down AppKit. The
worker must terminate with the app and cannot retain audio after a session.

The automation service remains a separate process. It publishes display-only
events through a current-user Unix socket. Display events and action commands
use separate protocols: forging an `OutputEvent` can never cause a click.

## Voice session contract

- Primary control: hold `Option-Space` to record; release to finalize and
  submit.
- Long-input fallback: choose “开始语音” from the menu, then explicitly choose
  “结束并提交” or “取消”.
- `Escape` cancels the current input without QMD, model, history, or action.
- VAD segments speech but does not submit a query by itself.
- Push-to-talk is limited to 60 seconds. Long input is limited to 10 minutes.
- Default recognition language is Chinese, with English technical terms
  allowed. Automatic language detection is disabled for short commands.
- Temporary transcript changes are display-only. Only finalized segments may
  enter command routing or knowledge retrieval.

The local STT backend is `whisper.cpp` with `ggml-large-v3-turbo-q5_0` on Apple
Silicon Metal. Ollama is not the audio runtime. The microphone format at the
worker boundary is 16 kHz mono PCM.

Performance acceptance on the current M4 Pro development Mac:

- listening state visible within 100 ms of push-to-talk;
- first partial transcript within 800 ms of speech onset;
- partial updates every 300-500 ms, capped at 10 UI updates per second;
- final transcript within 1.5 seconds P95 after release for inputs up to 30
  seconds;
- an unresponsive worker becomes a visible error within two seconds.

## Knowledge and speech output

Finalized non-command text uses the existing QMD collection. A missing result
produces “知识库中没有足够依据” and does not invoke the answer model. Grounded
answers continue to use local Ollama `qwen3:8b`.

The answer provider must stream real model output. Every delta carries a
generation. New user speech cancels the HTTP stream; late deltas from old
generations are discarded. The overlay shows only the final-answer channel,
never model reasoning. Sources remain visible but paths, URLs, tables, and code
are not read aloud by default.

First-version TTS uses a locally installed Chinese macOS system voice through
`AVSpeechSynthesizer`. It reads complete answer sentences, not the user's
transcript. Starting a new recording immediately stops current speech and
clears queued sentences. Recording and TTS never run simultaneously. Detection
alerts use a separate short system sound.

## Command routing and direct actions

The deterministic `VoiceCommandRouter` runs before knowledge retrieval:

```text
final transcript
  |-- exact registered phrase ending in “执行” --> command
  `-- other text ------------------------------> knowledge query
```

A command may execute while the overall voice session remains open only after
the sentence-ending “执行” marker and a 500-800 ms VAD pause. Partial text must
never execute. Each finalized sentence has a unique `utterance_id` and may
produce at most one action. A sentence is either one command or one knowledge
query; mixed multi-step utterances are rejected.

Direct commands are limited to registered low-risk native capabilities. A
registration binds the phrase and aliases to bundle ID, Accessibility role,
title, native ancestor hierarchy, optional identifier, and one action. Runtime
checks require the expected frontmost application, enabled native control,
owner, hierarchy, and a fresh point. `AXWebArea`, sensitive targets, ambiguous
matches, LLM-selected coordinates, and arbitrary visual targets cannot execute
directly.

“点它，执行” is valid only when exactly one rule marked `voice_direct: true`
produced a target during the previous three seconds. Before clicking, CoHelper
must capture again, match the same template or native capability, validate the
current screen and target, and consume `target_id + utterance_id` once. Missing,
moved, ambiguous, or expired targets are rejected. A complete Quartz
mouse-down/mouse-up gesture is atomic.

When `voice_direct_actions` is configured true and all health checks pass,
normal application startup makes the feature available. Push-to-talk remains
the only input window and the overlay must show that action mode is available.
Lock, sleep, user switching, permission/config changes, and health failures
suspend actions. The emergency-stop latch is durable across restarts and only a
manual menu action may resume it.

## Output events and overlay

`ai_drive.output.OutputEvent` is schema version 1. It has a bounded wire size and
strict fields: event ID, kind, source, timestamp, title, message, severity,
optional generation, and JSON metadata. Generation is mandatory for answer
stream events and emergency events; emergency publishers use it as a monotonic
latch revision so concurrent delivery cannot reorder stop and clear. Unknown
fields, enum values, versions, unsafe IDs, invalid numbers, and oversized
messages are rejected.

The event listener is output-only at:

```text
~/Library/Application Support/cohelper/output/events.sock
```

Its parent directory is `0700`; the socket is `0600`. Existing non-socket paths
are never replaced, and an active listener prevents a second listener from
taking over. Publishers receive an event-ID acknowledgement. Automation action
control continues to use its separate guarded socket. Client reads have a hard
deadline, concurrent clients are bounded, and acknowledgements must match the
submitted event ID.

The overlay is a borderless, non-activating, click-through `NSPanel` backed by
`NSVisualEffectView`. It occupies about 28% of the main display width and 45% of
its height. Final entries roll upward; the active partial transcript stays at
the bottom in a distinct state, and streaming answers update separately. Old
entries are bounded and fade from view rather than looping horizontally.

The overlay appears during input, retrieval, answer, speech, detection, action,
and error events. It hides after 12 idle seconds, except action errors remain at
least 20 seconds and emergency-stop state remains until acknowledged. A future
pin control may keep it visible. Version one supports only the main display and
must recalculate AppKit logical coordinates after display changes.

Raw screenshots may contain the overlay. Before OpenCV matching, vision-model
inference, action confirmation digests, or Telegram preview, capture adapters
must mask the exact overlay rectangle with a stable fill. A target hidden by the
overlay is rejected instead of clicked.

## Scheduling and failure behavior

Priority is fixed:

```text
emergency/safety > real-time STT > user knowledge answer
                 > visual-model inference > clipboard automation
```

OpenCV and native Accessibility validation do not require the heavy-model
scheduler. Whisper finalization blocks new visual-model inference. New speech
cancels an answer stream and stale output generations. Queues are bounded; TTS
keeps at most one pending answer. Notification failure must never replay an
action.

Voice input, voice output, voice commands, direct actions, and overlay have
independent feature flags with validated dependencies. A false flag prevents
permission checks, diagnostics, worker startup, model loading, and runtime
calls. Microphone/STT failure disables voice input without disabling clipboard
or automation. TTS failure leaves visible text. Missing Accessibility disables
external control. Missing Screen Recording disables detection-dependent voice
actions. Missing visible feedback disables direct actions.

## Privacy and logging

- Raw PCM is held only in a bounded in-memory ring buffer and released on
  submit or cancellation.
- No audio, transcript, question, answer, screenshot, secret, or full prompt is
  written to logs or progress files.
- Logs may contain event IDs, model names, timings, state transitions, rule IDs,
  and sanitized error types.
- There is no silent cloud fallback.
- A future history feature requires an explicit opt-in, retention limit, and
  clear operation; it is not part of this plan.

## Delivery phases

1. Versioned `OutputEvent`, bounded overlay state, current-user event transport,
   AppKit overlay skeleton, packaging, and documentation. Existing clipboard
   output supplies initial real events; automation publication is not connected
   yet.
2. App-owned microphone capture, supervised whisper.cpp worker, VAD, partial and
   final transcripts, push-to-talk/menu controls, and latency instrumentation.
3. QMD submission, true Ollama answer streaming, cancellation generations,
   sentence buffering, interruptible system TTS, and missing-source behavior.
4. Deterministic CoHelper-local voice commands and their conflict validation.
5. Registered native direct actions, fresh target context, “点它，执行”,
   overlay masking, durable emergency latch, audit metadata, and automation
   `OutputEvent` publication.

Each phase has its own feature gate and must pass focused tests, the full suite,
Python compilation, `git diff --check`, and relevant live macOS acceptance. A
later phase does not start until the preceding phase is verified. Commits and
pushes require explicit user direction.
