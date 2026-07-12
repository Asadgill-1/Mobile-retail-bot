# 10 — Data Model

> Source: `docs/SPEC-source.md` §1, §4, §6, §15. ADR-006 adds `clients` + `usage_daily`. Full DDL in `migrations/001_init.sql`.

## Entities

### clients  (ADR-006)
- **Purpose:** Service-provider client businesses. One client may own multiple shops.
- **Key fields:** `id`, `name`, `contact_name`, `contact_phone`, `email`, `status` (active/offboarded), `created_at`.
- **Relations:** one → many `shops`.
- **Invariants:** owner-level (not tenant-scoped; no RLS). Client-level suspension cascade is NOT wired yet — shop-level suspension (§2) remains the pipeline gate.

### shops
- **Purpose:** Tenant root. One row per shop. Belongs to a client.
- **Key fields:** `id`, `client_id` FK→clients, `name`, `whatsapp_number`, `status` (active/suspended), `suspension_reason`, `created_at`.
- **Relations:** belongs to client; has many shopkeepers, products, orders, etc.
- **Invariants:** `status` gates the message pipeline (§9 step 2).

### shopkeepers
- **Key fields:** `id`, `shop_id` FK, `telegram_id`, `name`, `is_owner` (boolean), `created_at`.
- **Invariants:** owner = `is_owner=true`; owner-only commands validated by Telegram ID.

### products
- **Key fields:** `id`, `shop_id` FK, `category`, `brand`, `model`, `color`, `condition`, `cost_price` DECIMAL, `selling_price` DECIMAL, `quantity` INT, `images` TEXT[], `video_url` TEXT, `specs` JSONB, `boost_level` INT DEFAULT 0 (0–10), `tags` TEXT[], `is_featured` BOOLEAN DEFAULT false, `created_at`.
- **Relations:** belongs to shop; referenced by orders (line items) and productstats.
- **Invariants:** `boost_level` ∈ [0,10]; `specs` = flexible key:value; tags from a fixed vocabulary (§4).
- **Search:** `search_products` parses natural-language requirements → Postgres query on `specs` JSONB ILIKE + `tags`; sorts by boost_level DESC, relevance (spec matches + tag matches×2), is_featured DESC; relevance × (1 + boost_level/10) (§4).

### orders
- **Key fields:** `id`, `shop_id` FK, `customer_name`, `phone`, `address`, `product_id` FK, `quantity`, `selling_price` DECIMAL (actual charged), `discount_amount` DECIMAL DEFAULT 0, `delivery_date`, `status`, `order_number` (human-friendly serial, migration 003), `rider_id` FK?, `special_instructions`, `created_at`. **Migration 008 (Stage 12b):** `cod_amount` DECIMAL? (COD to collect, set at assignment = net charge), `cash_received` DECIMAL? (what the rider actually collected), `delivered_at` TIMESTAMPTZ? (registered by rider `/deliver`), `custody` TEXT DEFAULT `'none'` (`none|offered|accepted|disputed` — the pickup handshake), `custody_at` TIMESTAMPTZ?, `cancel_remarks` TEXT? (mandatory on rider `/canceldelivery`).
- **`status`** ∈ `draft|pending|confirmed|packed|shipped|delivered|cancelled` (migration 003 added `draft`). Fulfilment moves one step at a time `confirmed→packed→shipped→delivered` (`orders.service._is_next_step`, no skipping/backward).
- **`custody`** is the audit handshake for rider handoff: assignment sets `'offered'`; the rider answers once (`'accepted'` = has the product, `'disputed'` = does not) and cannot re-answer. `/deliver` is refused unless `custody='accepted'`.
- **Profit formula:** `profit = selling_price - discount_amount - product.cost_price × quantity`; `margin% = profit / cost × 100` (§6).
- **Relations:** has `order_status_history`, `cod_ledger` (via `order_id`, collect rows only); belongs to shop + product + (optional) delivery_person.

### order_status_history
- **Key fields:** `id`, `order_id` FK, `status`, `changed_at`, `changed_by` (`system|shopkeeper|rider`).
- **Purpose:** audit trail of order status transitions.

### delivery_persons  ✅ Q-006 resolved (Stage 12b)
- **Key fields:** `id`, `shop_id` FK, `name`, `phone`, `created_at`, `telegram_id` BIGINT? (migration 007 — set once the rider links via the rider bot's Share-contact flow; **not unique**, one person riding for several shops gets one row per shop, all sharing the same `telegram_id`).
- **Model:** minimal, owner-onboarded (`/addrider`), no zones/shift/vehicle — matches the "minimal now, extend later" option from Q-006. A shop may have more than one rider.

### cod_ledger  (migration 008, Stage 12b)
- **Key fields:** `id`, `shop_id` FK, `rider_id` FK→delivery_persons, `order_id` FK→orders? (null on `handover` rows — not tied to one delivery), `entry` (`collect|handover`), `amount` DECIMAL, `note`, `created_at`.
- **Purpose:** append-only cash-on-delivery audit. `'collect'` rows are written by `riders.deliver_order` (one per delivery); `'handover'` rows by the keeper's `/reconcilecod` (end-of-day cash-in).
- **Invariant:** **balance is always `Σcollect − Σhandover` for a (shop, rider)**, re-derived from the row set — there is no mutable counter column that can drift out of sync with reality.

### pending_escalations
- **Key fields:** `id`, `shop_id` FK, `phone`, `message`, `created_at`, `resolved_at`.
- **Purpose:** Out-of-domain messages awaiting shopkeeper; freezes AI for that customer.

### security_incidents
- **Key fields:** `id`, `shop_id` FK?, `phone`, `attack_type`, `message_snapshot` (last 25 messages JSONB), `created_at`, `status`.
- **Purpose:** Attack forensics + owner investigation (§7).

### blacklisted_phones
- **Key fields:** `phone` (PK), `shop_id` FK?, `reason`, `created_at`.
- **Purpose:** Silent ignore in pipeline step 3 (§9).

### audit_logs
- **Key fields:** `id`, `shop_id` FK?, `actor`, `action`, `detail` JSONB, `created_at`.
- **Purpose:** Owner/sensitive action audit (§16).

### usage_daily  (ADR-006)
- **Key fields:** `id`, `client_id` FK, `shop_id` FK, `day` DATE, `metric` TEXT, `count` BIGINT; unique `(client_id, shop_id, day, metric)`.
- **Purpose:** Per-client/shop daily aggregates for billing insight.
- **Source:** Fed from Redis counters (`usage:{client_id}:{shop_id}:{day}:{metric}`) by a daily Celery beat job (Stage 10). Hot path touches Redis only — no per-message DB write.
- **Metrics:** `customer_msg_in`, `msg_out`, `escalation`, `ai_call`, `telegram_command`. (`active_conversations` is a realtime Redis metric in `/health`, not stored.)

## Relationships (overview)

```
clients 1───* shops 1───* shopkeepers
              shops 1───* products
              shops 1───* orders ──* order_status_history
                       orders *──1 products
                       orders *──?1 delivery_persons
                       orders 1───* cod_ledger  (collect rows; handover rows have order_id=null)
              shops 1───* pending_escalations
              shops 1───* security_incidents
              shops 1───* delivery_persons ──* cod_ledger
              shops 1───* audit_logs
              shops 1───* usage_daily *──1 clients
blacklisted_phones *──?1 shops
```

## Storage notes

- DB: **Supabase Postgres** (ADR-001).
- Migration tool: raw SQL in `migrations/` (Supabase SQL editor / `psql`). A real migration tool (e.g. alembic) may be adopted later (open).
- Naming: tables `snake_case`, columns `snake_case`.
- **RLS enabled** on all tenant tables (`shopkeepers`, `products`, `orders`, `order_status_history`, `pending_escalations`, `security_incidents`, `audit_logs`, `usage_daily`, `cod_ledger`), policies scoped by `shop_id`; backend uses service-role key (ADR-003). `clients` is owner-level (no RLS).

## Identity & types

- IDs: **UUID** (`gen_random_uuid()`).
- Money: **DECIMAL(12,2)** in DB; `decimal.Decimal` in code — never float (CONVENTIONS).
- Timestamps: **TIMESTAMPTZ**, UTC, ISO-8601.
- JSONB: `specs`, `message_snapshot`, `detail`.
- Arrays: `images` TEXT[], `tags` TEXT[].
