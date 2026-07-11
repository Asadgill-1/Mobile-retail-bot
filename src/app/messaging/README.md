# Module: messaging

## Responsibility
The 9-step message processing pipeline (SPEC §9), executed in Celery after the webhook returns 200. Per-session Redis lock + MessageSid dedup.

## Boundaries
- **Owns:** pipeline orchestration, session locks, dedup, conversation state in Redis.
- **Exposes:** `process_whatsapp_message(shop_id, phone, body, media, message_sid)` (Celery task).
- **Does NOT touch:** attack-pattern definitions (in `security/`); LLM prompting (in `ai/`); it calls them.

## Pipeline (SPEC §9, exact order)
1. validate Twilio signature (in `whatsapp/`, before enqueue)
2. check `shops.status` → suspended = auto-reply, stop
3. check `blacklisted_phones` → silent ignore
4. check `quarantine:{phone}` → generic reply
5. check `bypass_ai:{phone}` → forward to shopkeeper
6. attack detection → quarantine + alert
7. normal AI processing

## Concurrency / reliability (§11 — Stage 11)
`process_message` wraps the §9 body (moved into `_dispatch`) in a **per-session lock**
(`lock:session:{shop_id}:{identity}`, `SET NX EX 30`, released in `finally`; contention →
`PipelineResult(None, "locked")`) and **MessageSid dedup** (`dedup:{sid}`, `SET NX EX 300` — a
re-delivered Twilio message is dropped; Telegram carries no sid → never deduped). **Lock first,
dedup second** so a `locked` retry (the Stage-13 Twilio path's `self.retry`) isn't deduped away.
Both are a few `SET NX EX` lines — no separate module (ponytail). `lock_key`/`_is_duplicate` own
the key formats. The live Telegram bots run without `concurrent_updates` (sequential per bot), so
`locked` never fires there today — it exists for the Stage-13 Celery/Twilio path.

## Key files
| Path | Role | Stage |
|------|------|-------|
| `pipeline.py` | §9 orchestration + §11 session lock + MessageSid dedup | 3 / 11 ✅ |

## Status
🟢 `pipeline.py` live (channel-agnostic `InboundMessage` → `process_message` → `PipelineResult`).

| Step | State |
|------|-------|
| 2 suspension | ✅ live |
| 3 blacklist | ✅ live (Stage 7) — `security.is_blacklisted`, silent ignore |
| 4 quarantine | ✅ live — `security.is_quarantined`, generic reply |
| **4b escalation freeze** | ✅ live (Stage 6, SPEC §3 step 4) |
| 5 bypass_ai | ✅ live — forwards to staff (Stage 6) |
| 6 attack detection | ✅ live (Stage 7) — 6 patterns, auto-quarantine + owner alert |
| 7 AI | ✅ live, multi-turn |

The `quarantine:` / `bypass_ai:` / `blacklist:` key strings live in `security/service.py`, not here — the pipeline imports the reader functions so setter and reader can never drift.

**Freeze and bypass do the same thing** — no AI, forward to the shop's staff — so both route through one `_to_humans()`. That message is also `remember()`ed, because after `/handover` the AI resumes the conversation and must see what the customer said while a human held it. A failing forward never raises: the customer's message was already accepted.

Spec ref: §9, §11.
> Note: SPEC §9 enumerates **7** ordered steps (the "pipeline" list above), not 9.
