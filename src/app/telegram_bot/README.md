# Module: telegram_bot

## Responsibility
All Telegram surfaces: command routing, auth (owner / shopkeeper / rider), handler bodies, and
outbound sends. Uses python-telegram-bot v20+. Testing mode = long-polling (ADR-002); this is the
live customer/staff/rider channel today, not a stand-in for something else.

## Boundaries
- **Owns:** Telegram update handling, command dispatch, reply text, the 6-bot runner.
- **Exposes:** `build_application`, `build_shopkeeper_application`, `build_customer_application`,
  `build_rider_application`, `run_polling`, `run_all_polling`; `notify.py`: `send_to_owner`,
  `send_to_shopkeepers`, `send_to_customer`, `send_to_rider`.
- **Does NOT touch:** business logic — delegates to `tenants/`, `products/`, `orders/`, `riders/`,
  `reports/`, `security/`, `escalations/`, `audit/`. Handlers parse `update.message.text` directly
  (not PTB's `context.args`) so they're unit-testable without the PTB dispatcher.

## Bot topology (ADR-005 revised) — 6 bots today
| Bot | Built by | Scope |
|-----|----------|-------|
| Owner control | `build_application` | admin commands, cross-shop dashboards, security ops |
| Shopkeeper (×1 per shop) | `build_shopkeeper_application` | staff commands, scoped to one shop, gated by `_keeper_auth_gate` |
| Customer (×1 per shop) | `build_customer_application` | routes to `messaging.pipeline.process_message` |
| Rider (global, 1 total) | `build_rider_application` | link Telegram, work assignments — only if `TELEGRAM_RIDER_BOT_TOKEN` is set |

`run_all_polling` builds owner + per-shop bots + (if configured) the rider bot, and runs them
concurrently under one event loop. The rider bot is appended last and carries no `shop` in
`bot_data` (it's global, not tenant-scoped) — a test asserting bot count/order should account for
that (`tests/telegram_bot/test_bot.py::test_build_all_applications_appends_rider_bot_when_configured`).

## Commands (current, not historical)
- **Owner:** `/pauseshop` `/resumeshop` `/shopstatus`; riders `/addrider <shop> <phone> <name>`
  `/riders <shop>`; `/owner dashboard|health|profit|escalations|security|audit`; security
  `/investigate` `/quarantine_lift` `/quarantine_extend` `/blacklist` `/forward_to_shop`
  `/bypass_ai` `/bypass_remove`.
- **Shopkeeper (keeper bot, staff-only — see Auth):** products `/addproduct` `/boost` `/unboost`
  `/tag` `/untag` `/cleartags` `/feature` `/productstats`; orders `/orders` `/confirmorder`
  `/rejectorder` `/deliveryupdate <#> packed|shipped|delivered`; riders `/riders`
  `/assigndelivery <#> <rider_id>` `/reconcilecod <rider|name> <amount>`; pricing `/negotiation`
  `/approveprice` `/custom` `/denyprice`; `/profit`; export `/exportorders` `/exportrider`;
  escalation `/reply` `/handover`.
- **Rider bot (global):** `/mydeliveries`, `/accept <#>` / `/notreceived <#>` (custody handshake),
  `/deliver <#>` (asks cash on reply), `/canceldelivery <#> <remarks>`, `/myreport [period | from to]`.
- **Customer:** no commands — free text routes through `messaging.pipeline`.

## Auth
- **Owner:** `owner_only` decorator checks `is_owner(effective_user.id)`; wraps typed errors into
  safe replies and audits on success.
- **Shopkeeper:** `_keeper_auth_gate` (a `TypeHandler` in group `-10`, runs before every other
  handler including the `/addproduct` `ConversationHandler`) allows only registered shopkeepers of
  *that* shop or the global owner — fails closed. `keeper_command` wraps typed errors (including
  `RiderNotFound`, `OrderNotFound`, `InvalidTransition`) into safe replies.
- **Rider:** `rider_command` looks up `riders_by_telegram(user.id)` — a linked rider may have more
  than one row (rides for >1 shop); commands act across all of them. Not linked → told to `/start`
  and share their phone.

## Run
```bash
./scripts/run_bot.sh   # long-polling, live Supabase (all configured bots)
MSC_USE_INMEMORY=1 ./scripts/run_bot.sh   # offline, InMemoryTenantRepo
```

## Status
🟢 Done through Stage 12b. 6 bots polling live. See `docs/07-CURRENT-STATE.md` for the full stage
history — this file only describes current shape, not the build order.
Spec ref: §2, §3, §5, §6, §7, §8, §10, §12, §16.
