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
- Added Quartz capture/pointer adapters and in-memory preview annotation.
- Added Telegram command handler and polling runtime without arbitrary execution.
- Added advanced configuration controls for vision, actions, and Telegram.

### Verified so far

- Focused TDD tests: 15 passed.
- Full suite after integration and concurrency repair: 54 passed.
- Editable package dependencies installed; `ApplicationServices` is available.
- Current process reports Screen Recording and Accessibility permissions absent.
- QMD status verified 194 documents and 5137 vectors; a real `qwen3:8b`
  grounded answer for "什么是 Flink？" completed from three local sources.
- PyInstaller app build, DMG checksum verification, deep ad-hoc signature
  verification, and local process launch passed.

### Pending

- Finish `qwen2.5vl:7b` download and run a real structured vision request.
- Configure Telegram User ID and Keychain Token without reading Dofi secrets.
- Grant `.app` Screen Recording and Accessibility permissions.
- Run real preview-confirm-click-result acceptance.
- Rebuild and verify `.app` and DMG.
- Run two-axis code review, commit, and merge local `main` only after all live
  acceptance criteria pass.
