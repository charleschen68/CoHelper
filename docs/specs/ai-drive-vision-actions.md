# AI Drive Vision and Actions Specification

## Scope

Turn CoHelper into a local-only AI Drive foundation that exposes reusable
clipboard, vision, and pointer-action capabilities. Dofi is reference material
only and is not modified.

## Repository layout

- `src/ai_drive/vision`: main-display capture, coordinate mapping, and local
  Qwen2.5-VL analysis.
- `src/ai_drive/actions`: application allowlisting, Accessibility validation,
  pending-action lifecycle, and Quartz pointer output.
- `apps/clipboard_helper`: clipboard classification, translation, QMD retrieval,
  and grounded answer or summary orchestration.
- `apps/telegram_bridge`: local Telegram polling and explicit action commands.

## Models

- Vision: local Ollama `qwen2.5vl:7b` with no external fallback.
- Clipboard answers and summaries: local Ollama `qwen3:8b`.
- Translation: the configured local translation provider.

## Clipboard behavior

Questions, short terms, and ordinary paragraphs are all translated when
translation is enabled. Questions are answered from QMD sources, terms are
rewritten as "what is X" questions, and paragraphs are summarized in relation
to retrieved sources. Translation, retrieval, and answer generation have
independent feature flags. Answer generation requires retrieval. When sources
are absent, the application must say that the knowledge base is insufficient.

## Visual action behavior

Version one supports the main display and a single mouse click only. An
instruction that names an explicitly allowlisted native Accessibility capability
is resolved directly through Accessibility in the application's focused window;
the control must also be within the captured main-display bounds. The safe default enables Safari
toolbar refresh and no TextEdit action. Instructions that cannot be resolved
natively, and other instructions, use Qwen, which
returns a strictly validated target candidate. Retina coordinate mapping,
frontmost-application identity, screenshot age, display identity, and
Accessibility target semantics must be validated before confirmation and again
before execution. Safari and TextEdit initially pass the application gate, but
an action is enabled only when an explicit native Accessibility capability also
matches.
Password,
Keychain, authorization, security settings, destructive, purchase, and system
permission targets are rejected. There is no blind-click fallback.

## Telegram protocol

- `/click <target>` prepares an action and returns a compressed annotated
  preview.
- `/confirm <action-id>` executes a matching, unexpired, one-use action.
- `/cancel <action-id>` cancels it.
- A new click cancels the user's previous pending click.
- Pending actions expire after 30 seconds and bind user, chat, target-region
  screenshot digest, display, frontmost application, and coordinate. The exact
  native Accessibility target is revalidated before the click.
- A security-configuration change or failed configuration read revokes pending
  actions before the Bridge stops.
- Ordinary chat never produces a pointer action.

The Telegram token is stored in macOS Keychain. Screenshots are memory-only
except for SDK-required temporary files, which are removed immediately after
sending. Logs exclude screenshots, clipboard text, secrets, and full prompts.

## Completion criteria

Unit tests cover the confirmed public seams. A live Telegram preview-confirm-
click-result loop, the three clipboard routes, local model selection, a rebuilt
app and DMG, and a local application launch must be verified. If GUI permissions
or live confirmation block acceptance, the work remains on the feature branch
and is reported as partial rather than merged to `main`.
