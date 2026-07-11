# Module: escalations

## Responsibility
Human-escalation flow (SPEC §3; ADR-009): write `pending_escalations`, notify the shop's staff, freeze the AI for that customer, `/reply` + `/handover`. Also owns the Redis conversation memory every other module reads.

## Boundaries
- **Owns:** `pending_escalations` table, the freeze flag, the Redis session (`context.py`), owner alerts.
- **Exposes:** `escalate()`, `is_frozen()`, `freeze()`/`unfreeze()`, `forward_to_shopkeepers()`, `reply()`, `handover()`, `alert_owner()`; `context.remember()` / `context.history()`.
- **Does NOT touch:** LLM prompting (`ai/`), Telegram transport (`telegram_bot/notify.py`).

## Key files
| Path | Role | Stage |
|------|------|-------|
| `service.py` | escalate / freeze / reply / handover / alert_owner | 6 ✅ |
| `context.py` | Redis conversation memory (last 25 turns) | 6 ✅ |

## The flow (SPEC §3)
```
escalate()   → pending_escalations row · AI frozen · shopkeeper notified
             → (customer sees "Let me connect you with our specialist.")
is_frozen()  → pipeline step 4b routes the customer's next messages to humans, not the AI
reply()      → shopkeeper answers the customer, in the shop's voice
handover()   → AI unfrozen, escalation resolved
```

**Freeze happens before notify.** If the shopkeeper notification is slow, the customer's very next message must already miss the AI. Pinned by `test_escalate_freezes_before_notifying`.

## "AI resumes with full Redis context" — there is nothing to restore
`context.remember()` records **every** turn as it happens: customer, AI, and shopkeeper alike. So `/handover` is just an unfreeze — the history was never lost. A shopkeeper's turns replay as **assistant** turns, because the shopkeeper speaks *as the shop*: the AI picks up a conversation a human was holding without being told a human held it.

This also ended the AI's single-turn era — `ai/orchestrator` replays the session on every message.

## Tenant guard (do not weaken)
The customer id in `/reply <customer>` and `/handover <customer>` is **shopkeeper free text**. Freeze state is keyed `escalation:frozen:{shop_id}:{identity}` and both commands refuse when the customer is not frozen *for this shop*, so Shop B cannot answer or hijack Shop A's escalated customer. `_resolve_escalation` also filters `shop_id` in the DB. Pinned by `test_shop_b_cannot_reply_to_shop_a_escalation` and `test_shop_b_cannot_hand_over_shop_a_escalation`.

## Nothing here may cost the customer their reply (ADR-009)
`escalate()` and `alert_owner()` run on the reply path, often inside an `except` block. Notification and DB failures are logged, never raised. If **no shopkeeper was reachable**, the owner is paged — the customer has been promised a specialist and somebody must know nobody heard.

## Status
🟢 Stage 6 ✅. Spec ref: §3. See ADR-009 (failure handling & AI disclosure).
