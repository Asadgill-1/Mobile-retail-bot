# Module: audit

## Responsibility
Append-only audit trail (SPEC §16): who ran which privileged command, when. Backs `/owner audit`.

## Boundaries
- **Owns:** `audit_logs` writes + reads.
- **Exposes:** `record(actor, action, *, shop_id=None, detail=None)`, `recent(limit=15, shop_id=None)`.
- **Does NOT touch:** the commands themselves — it's called *from* the two Telegram command wrappers.

## Where it's wired
`record()` fires from **`owner_only`** and **`keeper_command`** in `telegram_bot/bot.py`, in the `else`
branch (only after the handler succeeds). Those decorators wrap *exactly* the privileged owner/keeper
commands (plain `/start`/`/help` are undecorated), so auditing there covers every state-changing action
with its actor — one place, no per-command edits (the root-cause placement). The wrapper's `_audit`
helper supplies `actor` = Telegram user id, `action` = handler name, `shop_id` = the keeper bot's shop
(None on the owner control bot), `detail` = the message text.

## Best-effort, always
`record()` **never raises** — a failed audit write (even a broken client) is logged and swallowed, so
an audit outage can never break the action it was recording. Mirrors `escalations`/`security` notifies.

## Key files
| Path | Role | Stage |
|------|------|-------|
| `service.py` | `record` + `recent` (audit_logs) | 12 ✅ |

## Status
🟢 live (Stage 12). Live-verified: write → `recent` read-back → cleanup. `audit_logs` schema in
`migrations/001_init.sql` (id, shop_id, actor, action, detail jsonb, created_at). Spec ref: §16.
