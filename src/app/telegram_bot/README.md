# Module: telegram_bot

## Responsibility
Telegram bot for owner + shopkeepers: command router, auth (owner vs shopkeeper), handlers. Uses python-telegram-bot v21+. Testing mode = long-polling (ADR-002).

## Boundaries
- **Owns:** Telegram update handling, command dispatch, reply text.
- **Exposes:** `build_application(service)`, `run_polling(service)`, individual handler functions.
- **Does NOT touch:** business logic — delegates to `tenants/` (now) and `products/`/`orders/`/`reports/`/`security/`/`escalations/` (later stages).

## Key files
| Path | Role | Stage |
|------|------|-------|
| `bot.py` | auth gate (`owner_only`), owner cmds (`/pauseshop` `/resumeshop` `/shopstatus`), shopkeeper stubs, `build_application`, `run_polling` | 2 ✅ |

## Commands
- **Owner (wired, §2):** `/pauseshop <id|number> <reason>`, `/resumeshop <id|number>`, `/shopstatus <id|number>`, `/help`.
- **Shopkeeper (stubs → Stages 5–9):** `/addproduct /boost /unboost /tag /untag /cleartags /feature /productstats` (5), `/profit /report` (8), `/exportorders /exportrider` (9), `/reply /handover` (6).

## Auth
`owner_only` decorator checks `is_owner(effective_user.id)` (settings.owner_telegram_id). Non-owners get "⛔ Owner only." Shopkeeper auth (per-shop scoping) lands when real shopkeeper commands ship (Stage 5+) via `tenant_service.resolve_shopkeeper`.

## Run
```bash
./scripts/run_bot.sh   # long-polling, InMemoryTenantRepo (swap to Supabase at Q-003)
```

## Status
🟢 Stage 2 complete (owner commands + auth + stubs). 10 bot tests; 33 total green.
Spec ref: §2, §5, §6, §12.
