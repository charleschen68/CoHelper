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

`ActionService.prepare_click` validates capture age, desktop identity,
allowlisted application, model confidence, Accessibility role/title, enabled
state, and sensitive terms. `confirm` binds user and chat, revalidates desktop
and Accessibility state, consumes the action before output, and emits one Quartz
click. A new action for the same user invalidates the old action.

## Clipboard contract

The menu-bar process keeps asynchronous cancellation semantics from CoHelper.
Questions use their text as the query, terms become `什么是 X？`, and paragraphs
use their original text. Translation, search, and answer generation are
independently gated. `knowledge_answer` depends on `knowledge_search`; the old
`knowledge_summary` key is migrated on load.

## Extension points

Future audio input/output, display output, text command inputs, and external
Agent interfaces should depend on the public protocols in `ai_drive`, use the
same explicit capability and confirmation model, and remain disabled when their
feature flag is false.
