# ADR-006 — Client layer above shops + per-client usage tracking

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** owner
- **Stage when decided:** 1

## Context

Clarified business model (see `01-VISION.md`): the owner runs an **automation service company**; shops are **clients** that pay for the service. Two needs arise from this:
1. A single client business may operate **multiple shops** (e.g., a brand with several locations) and should be groupable under one client account for consolidated owner reporting.
2. As a service company, the owner wants **per-client usage insight** (message volume, escalations, AI calls) for billing/SLA — without building a full billing/payment system.

The original spec (§15) has only `shops` (1:1 with client) and no usage tables.

## Options considered

### Option A — Keep `shop` = `client` (1:1); no usage tracking
- Pros: simplest; matches spec verbatim.
- Cons: can't group multi-location clients; no usage insight for billing. Rejected by owner.

### Option B — `clients` table above `shops` + `usage_daily` aggregate (chosen)
- `clients` (one → many `shops`); `shops.client_id` FK.
- `usage_daily` (client_id, shop_id, date, metric, count) fed from **Redis counters** on the hot path, flushed to Postgres by a daily Celery beat job.
- Pros: groups multi-shop clients; gives billing-grade aggregates; **no DB write per message** (hot path touches Redis only).
- Cons: extra table + a beat job; client-level suspension not wired into the pipeline yet (deferred).

### Option C — Append-only `usage_events` (one row per message)
- Pros: full detail.
- Cons: high write volume on the hot path (300+ concurrent); overkill for billing aggregates. Rejected for the hot path (kept conceptually possible for non-aggregatable events later).

## Decision

Adopt **Option B**.

### Schema additions (`migrations/001_init.sql`)
- `clients` (id, name, contact_name, contact_phone, email, status, created_at) — owner-level, not tenant-scoped.
- `shops.client_id` FK → `clients(id)` (nullable only during migration; not nullable after backfill).
- `usage_daily` (id, client_id, shop_id, date, metric, count; unique (client_id, shop_id, date, metric)) — RLS by `shop_id`.

### Hot path (Stage 3) + flush (Stage 10)
- Hot path increments Redis: `INCR usage:{client_id}:{shop_id}:{YYYY-MM-DD}:{metric}` (metrics: `customer_msg_in`, `msg_out`, `escalation`, `ai_call`, `telegram_command`).
- Daily Celery beat job (Stage 10) reads these keys, upserts into `usage_daily`, expires the keys.
- `active_conversations` is **not** stored — it's a realtime Redis metric surfaced in `/health` (§13).

### Reporting (Stage 8)
- Owner reports gain a client grouping: `/owner profit all` can roll up by client across that client's shops; `/owner profit compare` can compare clients (in addition to shops).

## Rationale

Owner directive. Keeps the hot path fast (Redis-only), gives billing-grade aggregates, and supports multi-shop clients without a full billing system. Schema is additive and fits the existing RLS model (`usage_daily` scoped by `shop_id`; `clients` is owner-level).

## Consequences

- Positive: multi-shop client grouping; usage insight for billing; clean hot path.
- Negative: extra migration + beat job; client-level suspension (cascade to all shops) is **not** wired yet — shop-level suspension (spec §2) remains the pipeline gate. Client onboarding/offboarding flows are owner-side and added later.
- Follow-ups: Stage 3 wires Redis usage counters; Stage 8 adds client-grouped owner reports; Stage 10 adds the daily flush beat job.

## Related

- `01-VISION.md` (service-provider framing), `10-DATA-MODEL.md`, `migrations/001_init.sql`.
- ADR-003 (RLS), ADR-001 (stack).
