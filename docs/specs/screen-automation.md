# Screen Automation

The screen automation service is a CoHelper-owned, separately launched local
process. It is not a Dofi skill and does not start at login by default.

## Runtime data

- Rules: `~/Library/Application Support/cohelper/automation/rules.yaml`.
- State and bounded notification queue: the same application-support directory.
- Templates: a user-private directory outside this repository.
- Telegram token and any `keychain_ref` input: macOS Keychain service
  `com.charleschen68.cohelper`.

Use [automation-rules.example.yaml](../examples/automation-rules.example.yaml)
only as a schema example. It contains no real screenshots, paths, identities,
or credentials.

## Invariants

- One main-display screenshot is shared by each scan; default cadence is five
  seconds and configuration may reduce it only to one second.
- A rule starts once per appearance, and re-arms only after two absent scans.
- The highest-priority match is the sole action candidate for that observation.
- Before every click, text input, or key press, the configured guard template
  is matched again. An action failure stops the rest of the sequence and is
  never retried automatically.
- State is written as `EXECUTING` before the first irreversible output. A
  restart changes it to `UNKNOWN`; the rule cannot replay until it is re-armed.
- Arbitrary Python and shell actions are rejected by configuration validation.

## Telegram control

Only the configured user and private chat may issue control commands. Start is
always per-group and requires a one-time, 30-second confirmation. Stop may use
`all`; it disarms scanning but leaves the control process available for a later
explicit start. The network transport must call `AutomationController`; it must
not execute actions directly.

## Acceptance

The original scripts remain a fallback until all three migrated groups are
validated live: detection, one action, success condition, re-arm, sound,
Telegram delivery, emergency stop, and partial failure handling.
