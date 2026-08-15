# Progress Log

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
- Added Quartz capture/pointer adapters and in-memory preview annotation.
- Added Telegram command handler and polling runtime without arbitrary execution.
- Kept Telegram as a standalone manual process and added shutdown-on-config-change.
- Added advanced configuration controls for vision, actions, and Telegram.
- Wired missing-source responses and paragraph-specific summaries into the
  production menu-bar coordinator; fixed answers to local `qwen3:8b`.

### Verified so far

- Focused TDD tests: 15 passed.
- Full suite after two-axis review repairs: 69 passed.
- Editable package dependencies installed; `ApplicationServices` is available.
- Current process reports Screen Recording and Accessibility permissions absent.
- QMD status verified 194 documents and 5137 vectors; a real `qwen3:8b`
  grounded answer for "什么是 Flink？" completed from three local sources.
- `qwen2.5vl:7b` (6.0 GB) installed and a real local multimodal request returned
  a schema-valid normalized target coordinate.
- Rebuilt PyInstaller app and DMG after review repairs; DMG checksum/structure,
  deep ad-hoc signature, version `0.1.0`, and local process launch passed. DMG
  SHA-256: `06a542ea12c5e052de5d73a147871860f8246bd7cd632a663d8f1e7e1067437c`.

### Pending

- Configure Telegram User ID and Keychain Token without reading Dofi secrets.
- Grant `.app` Screen Recording and Accessibility permissions.
- Run real preview-confirm-click-result acceptance.
- Rebuild and verify `.app` and DMG.
- Re-run the two-axis code review after the safety repairs.
- Commit the reviewed repairs; merge local `main` only after all live acceptance
  criteria pass.
