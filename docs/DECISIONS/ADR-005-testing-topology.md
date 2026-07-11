# ADR-005 — Testing topology: 5 bots (owner + per-shop keeper + customer), 2 Telegram user accounts

- **Status:** Accepted (revised 2026-07-07 — supersedes the original 3-bot plan)
- **Date:** 2026-07-07
- **Deciders:** owner
- **Stage when decided:** 0 (revised at Stage 2→3, when the owner provisioned real bots)

## Context

ADR-002 mandates Telegram-first testing with WhatsApp mocked. SPEC §1 specifies, for production:
- **WhatsApp:** one Twilio number per shop → single webhook → shop resolved from `To` (no "which shop?" prompt).
- **Telegram:** shopkeepers authenticated by `telegram_id → shop_id`.

Two hard constraints shape the test design:
1. **Telegram bots cannot message other bots.** Only *user accounts* can message a bot. Therefore customers and shopkeepers must be *user accounts*, not bots.
2. The owner has **2 Telegram user accounts** available for testing.

The owner (service provider) provisioned **5 real bots** and directed that each shop is an independent client unit that may, during onboarding, be declared as either a single shop or a parent with N branches (each branch onboarded as its own shop row under the same `client_id`). Each shop/branch therefore gets its own bot pair.

## Options considered

### Option A — 3 bots (1 staff + 2 customer-facing) + 2 user accounts  *(original, now superseded)*
- Staff bot = owner + all shopkeepers (matches a literal reading of spec §1 "one bot token").
- One customer-facing bot per shop.
- Rejected by the owner: the owner wants explicit per-shop staff bots so each shop's shopkeeper channel is independent (matches the "each shop is independent" business framing).

### Option B — 5 bots (owner control + per-shop shopkeeper + per-shop customer)  *(chosen)*
- Owner control bot: admin commands (/pauseshop, /shopstatus, onboarding, reports).
- Per shop: a **shopkeeper bot** (staff side: /addproduct, /profit, /exportorders, …) + a **customer bot** (customer-facing channel = the "WhatsApp" channel in Telegram-first testing).
- Branches = additional shops under the same client; each branch gets its own bot pair.
- Pros: explicit per-shop separation; shopkeeper and customer channels never share a bot; onboarding new shops/branches just adds another bot pair.
- Cons: more bots to provision (acceptable — the owner provisions them at onboarding); shopkeeper side now uses per-shop bots rather than one shared staff bot (a deliberate, owner-approved deviation from a literal spec §1 reading).

### Option C — 2 bots (staff + 1 customer-sim)
- Pros: fewest bots.
- Cons: single customer bot can't mirror per-shop WhatsApp numbers without a "which shop?" prompt (violates spec §1). Rejected.

## Decision

Adopt **Option B**: 5 bots now (owner + 2 shops × {keeper, customer}), scaling by adding a bot pair per onboarded shop/branch.

### Bots (tokens) — current 5
| # | Bot username | Purpose | Spec role |
|---|--------------|---------|-----------|
| 1 | `@Retail_owner_bot` | Owner control: admin commands, onboarding, reports | Owner channel |
| 2 | `@Shop_no_1_bot` | Shop 1 shopkeeper bot (staff side) | Production Telegram staff channel, scoped to shop 1 |
| 3 | `@Retailcustomer_bot` | Shop 1 customer bot (customer-facing) | Stands in for shop 1's Twilio `To` number |
| 4 | `@Shop_no2_bot` | Shop 2 shopkeeper bot (staff side) | Production Telegram staff channel, scoped to shop 2 |
| 5 | `@Customer_no2_bot` | Shop 2 customer bot (customer-facing) | Stands in for shop 2's Twilio `To` number |

### Telegram user accounts — 2
| Account (telegram_id) | Roles | Bots it messages |
|------------------------|-------|------------------|
| **A** = `5215780245` | Owner; Customer of shop 1 | owner bot (as owner), shop 1 customer bot (as customer 1) |
| **B** = `6157301262` | Customer of shop 2 | shop 2 customer bot (as customer 2) |

Routing is by *which bot received the message*, so one account playing multiple roles across different bots does not conflict. Customer identity in tests = the account's `telegram_id` (stands in for the WhatsApp phone number; Redis session key `lock:session:{shop_id}:{phone}` keeps per-shop sessions distinct).

### Branches
A client may own multiple shops. During onboarding the owner declares "single" or "N branches"; each branch is a row in `shops` sharing the parent's `client_id`, and gets its own shopkeeper + customer bot pair. No schema change needed — `shops.client_id` already models this (ADR-006).

### Why only 2 user accounts
Cross-shop isolation is provable with 2: Account A (as shop 1 customer) must be served by shop 1's bot and never reach shop 2 data; Account B conversely. A shopkeeper-side isolation test uses the owner account messaging a shopkeeper bot. More accounts are optional, not required.

### Customer automation
Customers are scripted via **Telethon** user-sessions (real phone-number logins for Accounts A and B, stored once as `.session` files). The harness lives in `tests/customer_simulator/`. Bots alone cannot drive customer traffic (bots-can't-message-bots rule).

### Where tokens live
- Owner control bot token: `TELEGRAM_BOT_TOKEN` (env).
- Per-shop tokens: `TELEGRAM_SHOP_BOTS_JSON` (env, JSON list) for the testing phase — attached to the in-memory `Shop` model at seed time.
- **Upgrade path (ponytail):** at the onboarding stage, move per-shop tokens into `shops.telegram_keeper_bot_token` / `shops.telegram_customer_bot_token` columns (already added to `migrations/001_init.sql`) so newly onboarded shops/branches carry their own bots without an env edit.

## Rationale

Owner-directed: each shop is an independent business unit with its own staff + customer channels. Per-shop bots make that separation explicit and let onboarding scale linearly. Stage 13 WhatsApp cutover remains clean: each shop's *customer bot* is replaced by a real Twilio number pointing at the same webhook; the shopkeeper bots stay on Telegram in production (shopkeepers keep using Telegram).

## Consequences

- Positive: explicit per-shop separation; shopkeeper/customer channels never collide; onboarding = add a bot pair; customer channel mirrors production WhatsApp; deterministic scripted tests.
- Negative: 5 bots to provision now (more per shop added); Telethon dev dependency; one-time phone login per account to create `.session` files; customer identity differs test (telegram_id) vs prod (phone) — abstracted behind the customer-identity field so Stage 13 only changes the resolver; per-shop tokens in env JSON for now (DB upgrade noted above).
- Follow-ups: `telethon` + `fakeredis` already in `requirements-dev.txt`; `tests/customer_simulator/` skeleton exists; `.session` files gitignored; create the 2 real `.session` files (Q-004) to drive customer-side round-trip tests.

## Related

- ADR-002 (Telegram-first), ADR-004 (LLM provider), ADR-006 (clients/branches), SPEC §1.
- `docs/11-API-CONTRACTS.md` (testing topology section), `tests/customer_simulator/README.md`.
