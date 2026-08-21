# AI Drive Architecture

## Boundaries

```text
Telegram -> apps/telegram_bridge -> VisualClickWorkflow
                                      |-> QuartzScreenCapture
                                      |-> VisionAnalyzer -> local Ollama
                                      |-> ActionService -> Accessibility -> Quartz click

Clipboard -> apps/clipboard_helper -> translation
                                    -> QMD -> qwen3:8b grounded answer
```

`apps/` contains human-facing processes. `src/ai_drive/` contains reusable
capabilities and typed contracts. Platform code never imports or executes Dofi
skills. Other Agents should call typed capability interfaces rather than submit
source code.

## Vision contract

`Screenshot` binds image bytes to capture time, pixel and logical dimensions,
display ID, origin, and frontmost bundle ID. The vision model returns exactly
`found`, `x`, `y`, `confidence`, and `description`; coordinates use a 0-1000
normalized space. Extra or missing fields are rejected, and `found=false`
cannot enter the action pipeline.

## Action contract

`ActionService` first resolves an instruction that names an explicitly
allowlisted native capability through Accessibility; the default is Safari's
toolbar refresh button. Native discovery walks only that application's focused
window and rejects a control outside the captured main-display bounds. This
avoids relying on a vision model to find a small native icon. Instructions that
native discovery cannot resolve, and unknown instructions, continue through the visual path. Both paths
validate capture age, desktop identity, allowlisted application, Accessibility
role/title, enabled state, native capability hierarchy, owner bundle, and
sensitive terms. Webpage content cannot impersonate an allowlisted browser-
toolbar title. Visual inference is followed by a fresh identical capture so
slow model startup does not consume the screenshot age budget. `confirm` binds user and chat,
captures a fresh main-display screenshot, requires the prepared target region
digest and desktop identity to match, revalidates Accessibility role and
semantics, consumes the action before output, and emits one complete Quartz
click gesture. A new action for the same user invalidates the old action.
Preparation generations ensure concurrent requests cannot finish out of order
and reactivate an older click.

The Telegram Bridge is a standalone manual process. It watches the
security-relevant configuration and stops when it changes so stale allowlists or
identities cannot remain active; it revokes all pending actions before stopping.

## Clipboard contract

The menu-bar process keeps asynchronous cancellation semantics from CoHelper.
Questions use their text as the query, terms become `什么是 X？`, and paragraphs
use their original text with an explicit source-grounded summary prompt.
Translation, search, and answer generation are
independently gated. `knowledge_answer` depends on `knowledge_search`; the old
`knowledge_summary` key is migrated on load. An empty QMD result emits an
explicit insufficient-knowledge response without invoking the answer model.

## Extension points

Future audio input/output, display output, text command inputs, and external
Agent interfaces should depend on the public protocols in `ai_drive`, use the
same explicit capability and confirmation model, and remain disabled when their
feature flag is false.
