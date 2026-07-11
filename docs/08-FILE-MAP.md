# 08 — File Map

> Index of the codebase. Update whenever files are added/removed/moved.

## Layout

```
new retail v2/
├── README.md
├── AGENTS.md
├── PONYTAIL-DEBT.md           # ponytail shortcut ledger (ethos §D)
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .context/
│   └── HANDOFF.md
├── config/
│   ├── settings.py             # pydantic-settings, env-driven
│   └── logging.conf            # (Stage 12)
├── migrations/
│   ├── 001_init.sql            # full schema + RLS + indexes + seed (incl. ADR-006 clients + usage_daily) [APPLIED to live project uwlczgwlkqlflpveeykj 2026-07-07]
│   ├── 002_storage_buckets.sql # private `shop-media` bucket for product images/video (SPEC §4) [APPLIED 2026-07-08]
│   ├── 003_order_drafts.sql    # hybrid booking (Q-017/ADR-010): draft status, order_number, decrement_stock() RPC [APPLIED 2026-07-10]
│   ├── 004_negotiation.sql     # human-in-loop pricing (ADR-010 rev.1): shops.negotiation_enabled, price_requests, drops products.min_price [APPLIED 2026-07-10]
│   ├── 005_reports_bucket.sql  # private `shop-reports` storage bucket for Excel exports (SPEC §10) [APPLIED 2026-07-10]
│   └── 006_rls_lockdown.sql    # security audit: RLS on all tables + drop scaffold policies + revoke anon (data API = service-role only) [APPLIED 2026-07-11]
├── mcp_servers/
│   ├── README.md               # how to run/wire the Supabase MCP server (ADR-007)
│   └── supabase_server.py      # FastMCP server: list_tables / execute_sql / apply_migration / get_project_info
├── pices and Video/            # owner-supplied test media (gitignored binaries): 6 photos + 2 videos
│                               #   = 2 real handsets (S23 Ultra green+black, iPhone 16 green)
├── scripts/
│   ├── seed_dev.py             # (Stage 1)
│   ├── run_bot.sh              # run all 5 Telegram bots (live Supabase DB; ADR-005)
│   ├── apply_migration.py      # apply a migration to live Supabase via the MCP tooling
│   ├── seed_shop_bots.py       # write per-shop bot tokens onto live shops rows (ADR-005)
│   └── seed_test_catalog.py    # seed the 8-product scenario catalogue + media (--clean to remove)
├── src/app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI: real /health (reports.health, §13, Stage 10 ✅) + /webhook/whatsapp mounted
│   ├── core/
│   │   ├── README.md
│   │   ├── config.py           # re-export settings
│   │   ├── logging.py          # setup_logging() — structured logs (§16, Stage 12 ✅)
│   │   └── security.py         # internal API key, headers (Stage 1+)
│   ├── db/
│   │   ├── README.md
│   │   ├── base.py             # TenantRepo abstract interface (Stage 1 ✅)
│   │   ├── in_memory.py        # InMemoryTenantRepo (tests/dev) (Stage 1 ✅)
│   │   ├── supabase_client.py  # get_supabase() + SupabaseTenantRepo (Stage 1 ✅)
│   │   ├── redis_client.py     # async Redis factory (Stage 1 ✅)
│   │   └── factory.py          # get_tenant_repo() — backend selection by env (Stage 3 ✅)
│   ├── llm/
│   │   ├── README.md
│   │   ├── llm_client.py       # provider-agnostic chat + tool-calling, retry-once (Stage 4 ✅)
│   │   ├── functions.py        # search_products + escalate_to_human tool schemas (Stage 4 ✅, ADR-008)
│   │   └── prompts.py          # anti-hallucination + promotion prompt, fallback/escalation replies (Stage 4 ✅)
│   ├── tenants/                # models/service/auth (Stage 1 ✅)
│   ├── telegram_bot/           # bot.py: handlers+auth+build_application (2 ✅); notify.py: owner/shopkeeper/customer sends (6 ✅)
│   ├── whatsapp/               # webhook.py: Twilio sig-verify + /webhook/whatsapp (Stage 3 ✅, mocked till Stage 13)
│   ├── messaging/              # pipeline.py: SPEC §9 pipeline (3 ✅) + §11 per-session lock + MessageSid dedup (11 ✅)
│   ├── ai/                     # orchestrator.py: chat loop + tool exec + escalation (Stage 4 ✅, ADR-008)
│   ├── products/               # models.py, search.py (4 ✅), service.py (guard+CRUD), addproduct_flow.py (11-step ConversationHandler), media.py (Storage upload) — Stage 5 ✅
│   ├── orders/                 # models.py (line_profit + ProfitSummary) + service.py (create_order, profit_summary, hybrid booking: draft/confirm/reject, negotiation, Excel export queries) — Stage 8/9 ✅ (Q-017/ADR-010)
│   ├── escalations/            # service.py (escalate/freeze/reply/handover/alert_owner) + context.py (Redis conversation memory) — Stage 6 ✅
│   ├── security/               # detectors.py (6 pure attack patterns) + service.py (quarantine/blacklist/bypass/incident snapshot) — Stage 7 ✅
│   ├── reports/                # service.py (parse_period + profit formatting, 8 ✅) + health.py (check_health §13, one checker for /health + beat, 10 ✅)
│   ├── audit/                   # service.py: record/recent → audit_logs (§16), wired at the command wrappers — Stage 12 ✅
│   ├── tasks/                  # celery_app.py + tasks.py: process_whatsapp_message (Stage 3 skeleton ✅; beat Stage 10)
│   └── utils/                  # excel.py (pure openpyxl pick-&-pack builder, §10) + storage.py (shop-reports upload + 24h signed URL) — Stage 9 ✅
├── tests/
│   ├── conftest.py             # fixtures: in-memory repo, TenantService (Stage 1 ✅)
│   ├── customer_simulator/     # Telethon userbot harness (ADR-005) — Stage 2 skeleton, Stage 3 build-out
│   │   ├── README.md
│   │   ├── userbot.py          # Userbot wrapper (skeleton)
│   │   └── sessions/           # .session files (gitignored)
│   ├── db/                     # test_in_memory_repo.py (Stage 1 ✅)
│   ├── tenants/                # test_service/test_auth/test_clients_usage (Stage 1 ✅)
│   ├── telegram_bot/           # test_bot.py (Stage 2 ✅)
│   ├── messaging/              # test_pipeline.py (Stage 3 ✅)
│   ├── whatsapp/               # test_webhook.py (Stage 3 ✅)
│   ├── tasks/                  # test_tasks.py (Stage 3 ✅)
│   ├── escalations/            # test_service.py + test_context.py (Stage 6 ✅)
│   ├── security/               # test_detectors.py + test_service.py (Stage 7 ✅)
│   ├── orders/                 # test_service.py — profit agg + create_order tenant guard (Stage 8 ✅)
│   ├── reports/                # test_service.py (period+format) + test_health.py — checker up/down/metrics + owner-alert (Stage 8/10 ✅)
│   ├── audit/                  # test_service.py — record writes/defaults/swallows-failure + recent (Stage 12 ✅)
│   ├── utils/                  # test_excel.py — pure workbook builder: headers/style/mapping (Stage 9 ✅)
│   ├── fixtures/               # catalog.py — 8-product scenario catalogue mapped onto the owner's media
│   ├── products/               # test_search.py — ranking (Stage 4 ✅); test_service.py — tenant guard + validation (Stage 5 ✅)
│   ├── ai/                     # test_orchestrator.py — tool loop + boost-leak guard (Stage 4 ✅)
│   └── <module>/test_*.py
└── docs/
    ├── 00-HOW-TO-USE.md
    ├── 01-VISION.md … 12-OPEN-QUESTIONS.md
    ├── 13-ENGINEERING-ETHOS.md  # caveman + karpathy + ponytail (MANDATORY)
    ├── SPEC-source.md          # immutable source spec
    ├── DECISIONS/              # ADR-001..004
    ├── TEMPLATES/              # MODULE-DOC.md, ADR-TEMPLATE.md
    └── modules/                # cross-module notes (per-module READMEs live in src/app/<module>/)
```

## Module → doc mapping

| Module / path | Purpose | Stage | Doc |
|---------------|---------|-------|-----|
| `src/app/core/` | config, logging, security | 0/12 | `src/app/core/README.md` |
| `src/app/db/` | Supabase + Redis clients + TenantRepo | 1 ✅ | `src/app/db/README.md` |
| `src/app/llm/` | provider-agnostic LLM client + tool schemas + prompts | 4 ✅ | `src/app/llm/README.md` |
| `src/app/tenants/` | shop, shopkeeper, suspension, auth | 1 ✅ | `src/app/tenants/README.md` |
| `src/app/telegram_bot/` | command router, auth, owner cmds | 2 ✅ | `src/app/telegram_bot/README.md` |
| `src/app/whatsapp/` | Twilio webhook + sig-verify | 3 ✅ | `src/app/whatsapp/README.md` |
| `src/app/messaging/` | SPEC §9 pipeline + §11 session lock + MessageSid dedup | 3/11 ✅ | `src/app/messaging/README.md` |
| `src/app/ai/` | anti-hallucination chat loop, tool exec, escalation | 4 ✅ | `src/app/ai/README.md` |
| `src/app/products/` | ranking/search, inventory, `/addproduct`, boost/tags, media | 4/5 ✅ | `src/app/products/README.md` |
| `src/app/orders/` | create_order (tenant-guarded), profit math + aggregation | 8 ✅ | `src/app/orders/README.md` |
| `src/app/escalations/` | pending, reply, handover, Redis session, owner alerts | 6 ✅ | `src/app/escalations/README.md` |
| `src/app/security/` | attack detection, quarantine, blacklist, bypass, incidents | 7 ✅ | `src/app/security/README.md` |
| `src/app/reports/` | profit formatting (§6) + `health.check_health` (§13) + owner dashboards (§12) | 8/10 ✅ | `src/app/reports/README.md` |
| `src/app/audit/` | `audit_logs` write/read (§16), wired at command wrappers | 12 ✅ | `src/app/audit/README.md` |
| `src/app/tasks/` | celery app + tasks, beat | 3 ✅/10 | `src/app/tasks/README.md` |
| `src/app/utils/` | money, time, Excel builder (§10), Storage upload + signed URL | 9 ✅ | `src/app/utils/README.md` |
| `tests/customer_simulator/` | Telethon userbot test harness | 2/3 | `tests/customer_simulator/README.md` |

## Where things go

- New top-level module → `src/app/<module>/` + a doc copied from `docs/TEMPLATES/MODULE-DOC.md`.
- New decision → `docs/DECISIONS/ADR-XXX-<slug>.md` + row in `04-TECH-DECISIONS.md`.
- New term → `09-GLOSSARY.md`.
- New entity → `10-DATA-MODEL.md` + SQL in a new `migrations/NNN_*.sql`.
- New API endpoint/contract → `11-API-CONTRACTS.md`.
- **Any other required folder → create it, organized by purpose, then add a row to the Layout tree above.** Only `README.md`, `AGENTS.md`, and standard config/manifest files live at the repo root.

## Folder creation rule (mandatory)

> When extending the system, **create whatever folders the work needs** — don't pile loose files at the root. After creating a folder, **update the Layout tree above** and, if it holds a module, add a `MODULE-DOC` for it. This keeps the repo navigable for the next LLM.
