# Progress Log

## 2026-08-22 — Voice and overlay phase 1

### Approved design

- The agreed cross-session design is
  `docs/specs/voice-overlay-actions.md`: explicit push-to-talk, local
  whisper.cpp, grounded streaming answers, interruptible system TTS,
  deterministic commands, guarded direct native actions, fresh “点它” target
  context, overlay masking, durable emergency stop, and five delivery phases.
- Normal startup may make explicitly configured direct voice actions available,
  but a durable emergency-stop latch overrides startup and requires manual
  recovery.

### Implemented in phase 1

- Added strict schema-version-1 `OutputEvent` serialization with bounded IDs,
  renderable Unicode text, JSON metadata, typed enum values, exact integer
  versions, total wire size, and rejection of unknown fields or invalid data.
  Answer generations and emergency-latch revisions are mandatory where order
  matters.
- Added a platform-independent overlay model with partial-transcript
  replacement, per-source answer-generation high-water marks, late-event
  rejection, event deduplication, a bounded timeline, 12-second idle hiding,
  20-second action-error retention, and revision-ordered emergency state that
  cannot disappear when timeline entries are evicted.
- Added a current-user output-only Unix socket protocol, real client/server
  transport, bounded concurrent clients, total read deadlines, active-socket
  protection, matching event-ID acknowledgements, and `0700` directory /
  `0600` socket permissions. The transport cannot submit action commands.
- Added an AppKit left-side `NSPanel` with a `NSVisualEffectView` background,
  no activation, mouse passthrough, all-Spaces visibility, main-display layout,
  display-change repositioning, bounded rendering, UTF-16-safe styling, old-entry
  fading, and explicit emergency/error colors.
- Added `features.overlay`, including validated boolean configuration, an
  advanced-config switch, and immediate runtime start/stop of the panel, timer,
  display observer, and socket.
- Wired existing clipboard input, translation, knowledge sources, answers, and
  task errors into the overlay with generation-aware UI callbacks so cancelled
  or reconfigured work cannot render late. The automation process does not
  publish output events yet; that remains phase 5.
- Updated setuptools and PyInstaller package discovery for `ai_drive.output`
  and `apps.overlay`.

### Verified

- Full test suite in the required local Unix-socket context: 161 passed.
- `python3 -m compileall -q src apps cohelper_core.py cohelper_app.py
  cohelper_setup.py` and `git diff --check` passed.
- Real Unix-socket delivery, concurrent slow-client isolation, total client and
  server deadlines, prompt shutdown, and `0700` / `0600` permissions passed.
- Live AppKit component acceptance confirmed a readable left-side blurred
  panel, fading old entries, orange action errors, Terminal remaining
  frontmost, mouse passthrough, and the non-activating panel style.
- An isolated PyInstaller build under `/private/tmp` completed from the final
  source. Deep strict code-signature verification passed, and its archive
  contains `ai_drive.output`, `apps.overlay`, and `cohelper_app`.
- Standards and specification reviews both completed with no remaining P1/P2
  finding. The review drove fixes for callback generations, socket concurrency
  and deadlines, answer-stream high-water marks, Unicode/AppKit range safety,
  emergency ordering, and strict acknowledgements.

### Phase boundary

- Phase 1 is locally verified. This is not a release claim: the isolated app was
  not notarized or accepted on a clean Mac.
- At the phase 1 boundary, Phase 2 had not started. TTS, voice command router,
  direct voice click, overlay masking, and automation output publication remain
  outside the current implementation.

## 2026-08-24 — Voice input phase 2 in progress

### Implemented so far

- Added an explicit voice-session state machine with bounded push-to-talk and
  long-input deadlines. Partial transcripts remain display-only; only final
  transcripts complete a session.
- Added bounded in-memory PCM buffering, energy-based VAD boundary events, and
  a supervised local STT worker protocol with invalid-output and child-failure
  handling. The worker does not persist audio or transcripts.
- Added a feature-gated input coordinator and a macOS AVAudioEngine capture
  adapter. The adapter fails closed unless the microphone is one-channel 16 kHz
  PCM; it does not silently send a different format to the worker.
- Added `features.voice_input: false` and validated voice configuration. The
  existing advanced configuration can opt into the feature.
- Bound final Whisper transcripts to the existing `TaskCoordinator`; only
  finalized speech enters QMD/answer processing, while partial speech remains
  overlay-only. The answer model remains the local `qwen3:8b` configuration.

### Verified so far

- Focused voice and configuration tests: 25 passed.
- Python compilation and diff checks pass for the new voice modules.
- Full repository regression after whisper.cpp/Ollama integration: 178 passed.
- Real macOS test audio was transcribed by the local `whisper-server` as
  “打开Safari的刷新按钮。”; local `qwen3:8b` also returned a successful
  response through Ollama.

### Remaining Phase 2 work

- The menu controls, global/local `Option-Space` press/release handling, and
  microphone permission request entry are now implemented.
- Bind the worker to a real whisper.cpp command/model contract, implement VAD
  latency instrumentation, and add true incremental partial transcription;
  final transcription and the local whisper.cpp server contract are now
  verified with real audio.
- Run live microphone permission, worker crash, partial/final latency, and
  60-second stop acceptance on the development Mac. Phase 2 is not complete
  until those checks pass.

## 2026-08-21

### Completed

- Live Telegram acceptance completed: `/click Safari 的刷新按钮` produced a
  preview, `/confirm` refreshed Safari, and the Bridge returned the result
  screenshot.
- Native Safari capability discovery now uses only the focused window, requires
  the control centre to be inside the captured main display, and deduplicates
  Accessibility traversal.
- A Telegram security-configuration change or failed reload revokes all
  pending actions before the Bridge stops. The final authorization generation
  is checked immediately before Quartz pointer delivery.
- Clipboard submissions now capture a configuration snapshot; saving a new
  configuration cancels the active task before installing it.
- Resolved the P1 findings from the two review axes; the native-intent matcher
  also avoids treating a generic Chinese "刷新" request as a toolbar reload.

### Verified

- Full unit suite: 93 passed.
- `python3 -m compileall -q src apps cohelper_core.py cohelper_app.py
  cohelper_setup.py` and `git diff --check` passed.
- Rebuilt `dist/cohelper.app` and `dist/cohelper-0.1.0.dmg`; deep ad-hoc
  signature verification, `hdiutil verify`, version `0.1.0`, bundle identifier
  `com.charleschen68.cohelper`, and a local application launch passed.
- DMG SHA-256:
  `60d0640b4eab1ca389f89a372e45ff85442df2c89eb0b6e9b9c8c19ccf11f012`.

### Remaining release boundary

- This remains a locally verified, ad-hoc-signed artifact. Developer ID
  signing, notarization, clean-Mac installation, and an explicit merge to
  `main` are intentionally not claimed or performed.

## 2026-08-15

### Confirmed decisions

- Evolve CoHelper into a local AI Drive foundation; do not modify Dofi.
- Implement main-display vision with `qwen2.5vl:7b` and grounded clipboard
  answers with `qwen3:8b`.
- Restrict version one to confirmed single clicks in Safari and TextEdit.
- Use Quartz and Accessibility rather than PyAutoGUI as the core backend.
- Run Telegram locally with explicit, one-use action commands and Keychain
  credentials.

### Implemented

- Added repository specification and confirmed TDD seams.
- Added legacy feature/model configuration migration and independent answer
  gating.
- Added clipboard question/term/paragraph classification and term query rewrite.
- Added screenshot metadata, Retina coordinate conversion, strict visual output
  parsing, and local-only Ollama client.
- Added action lifecycle, application allowlist, sensitive-target rejection,
  Accessibility revalidation, expiry, and one-use confirmation.
- Added exact safe-label allowlisting, confirmation-time screen digest and age
  checks, collision-safe IDs, atomic token consumption, and complete-gesture
  Quartz event creation.
- Replaced the label-only rule with native capability matching (owner bundle,
  role, title, hierarchy, optional identifier) and unconditional web-content
  rejection.
- Added post-inference recapture and request generations so cold model loading
  cannot stale the actionable capture and concurrent requests cannot reactivate
  an older action.
- Added Quartz capture/pointer adapters and in-memory preview annotation.
- Added Telegram command handler and polling runtime without arbitrary execution.
- Kept Telegram as a standalone manual process and added shutdown-on-config-change.
- Added explicit watcher cancellation on normal Telegram shutdown so polling
  cannot hang while the configuration remains unchanged.
- Added advanced configuration controls for vision, actions, and Telegram.
- Wired missing-source responses and paragraph-specific summaries into the
  production menu-bar coordinator; fixed answers to local `qwen3:8b`.

### Verified so far

- Focused TDD tests: 15 passed.
- Full suite after final runtime-lifecycle repair: 80 passed.
- Editable package dependencies installed; `ApplicationServices` is available.
- Current development runtime reports Screen Recording and Accessibility
  permissions available; the rebuilt `.app` identity still needs to exercise
  them in the live Telegram click loop.
- QMD status verified 194 documents and 5137 vectors; a real `qwen3:8b`
  grounded answer for "什么是 Flink？" completed from three local sources.
- `qwen2.5vl:7b` (6.0 GB) installed and a real local multimodal request returned
  a schema-valid normalized target coordinate.
- Rebuilt PyInstaller app and DMG after review repairs; DMG checksum/structure,
  deep ad-hoc signature, version `0.1.0`, and local process launch passed. DMG
  SHA-256: `8388188d1dd54703873d3cb9c5c49af2ebfbf8bd3b146a79866f4080de430aad`.

### Pending

- Configure Telegram User ID and Keychain Token without reading Dofi secrets.
- Grant `.app` Screen Recording and Accessibility permissions.
- Run real preview-confirm-click-result acceptance.
- Re-run the two-axis code review after those repairs.
- Commit the reviewed repairs; merge local `main` only after all live acceptance
  criteria pass.
