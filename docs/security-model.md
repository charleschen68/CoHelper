# Security Model

## Protected assets

- Desktop contents and clipboard text.
- Mouse and future keyboard control.
- macOS Keychain credentials.
- Local knowledge-base documents.

## Trust boundaries

Telegram is external infrastructure. A Telegram preview leaves the Mac. Ollama
and QMD are local; the visual endpoint refuses non-loopback hosts. Dofi's
unauthenticated `0.0.0.0:5001/execute` arbitrary-code endpoint is explicitly not
used.

## Controls

- Telegram user ID allowlist and Keychain-only Bot Token.
- Explicit `/click`, `/confirm <id>`, and `/cancel <id>` protocol.
- One-use, 30-second action IDs bound to user and chat.
- Main-display and frontmost-application identity binding.
- Safari/TextEdit bundle allowlist.
- Native Accessibility capability allowlist keyed by owning bundle, role,
  title, ancestor hierarchy, and optional identifier. Web-content ancestry is
  always rejected; defaults cover Safari toolbar refresh only.
- Strict vision schema and minimum confidence.
- Confirmation-time fresh screenshot, byte digest, display, application, and
  age validation. Any mismatch consumes the one-use token without clicking.
- Accessibility role, title, enabled-state, safe-label, and sensitive-target
  validation before preview and again immediately before execution.
- A new click request revokes the user's old token on arrival; preparation
  generations prevent a slower old request from becoming active afterward.
- No blind-click fallback and no arbitrary code, shell, keyboard, drag, or
  double-click interface.
- Memory-only screenshots; Telegram uses in-memory byte streams.
- Logs exclude clipboard contents, screenshots, secrets, and full prompts.

## Remaining risks

Vision and Accessibility labels can still be wrong. UI can change in the very
small interval between final validation and event delivery. Dynamic screen
content can conservatively invalidate a legitimate confirmation. Telegram
retains data according to its own policies. Ad-hoc signing is not a distribution
trust chain. These risks are why actions remain single-click, allowlisted,
confirmed, and local-only.
