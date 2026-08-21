# Progress Log

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
