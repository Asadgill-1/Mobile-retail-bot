# Platform Owner Console — Full Plan

Repo: `https://github.com/Asadgill-1/owner-dashboard-mobile.git` → deployed on Vercel.
Single-admin web console for the **platform owner**: onboarding/offboarding of clients, shops, keepers, riders; demo-grade analytics; system health; security operations; escalations; message archive control; runtime AI model switching.
Every feature is mapped to a verified function/table in the existing Python backend. Items with no backend support are flagged **NEW** with their exact migration.

---

## 1. System context (what already exists — verified in code)

- Multi-tenant Supabase schema: `clients` → `shops` → `shopkeepers` / `delivery_persons` / `products` / `orders`, plus `pending_escalations`, `security_incidents`, `blacklisted_phones`, `audit_logs`, `usage_daily`, `price_requests`, `cod_ledger`, `messages` (permanent chat archive), `order_status_history`.
- Owner Telegram bot already exposes: pause/resume shop, shop status, add rider, dashboard/health/profit/escalations/security/audit, investigate incident, quarantine lift/extend, blacklist, bypass AI, forward-to-shop, delete messages (all/shop/range). The console mirrors **all of it** (parity checklist §10).
- Tenant repo already has `create_client` / `create_shop` / `create_shopkeeper` (`src/app/db/supabase_client.py`) — **no bot UI exists for them: this console IS the missing onboarding UI**.
- AI brain: Moonshot **kimi-k2.6** via OpenAI-compatible client (`src/app/llm/llm_client.py`), provider set `moonshot | openai | openrouter` — today env-only, no runtime switch.
- Redis-bound operations (quarantine, bypass, blacklist hot-key, escalation freeze, health, LLM test) run on the owner's PC → Bridge API (§3.3).
- RLS off; scoping is app-layer. Console uses service-role key server-side only.

## 2. Design system

Style: **data-dense dashboard** — KPI cards, tight grids, maximum data visibility, row hover highlighting, interactive chart legends.

| Token | Value |
|---|---|
| `--color-primary` | `#1E40AF` (data blue) |
| `--color-on-primary` | `#FFFFFF` |
| `--color-secondary` | `#3B82F6` |
| `--color-accent` | `#D97706` (amber highlights — WCAG-adjusted) |
| `--color-background` | `#F8FAFC` |
| `--color-foreground` | `#1E3A8A` |
| `--color-muted` | `#E9EEF6` |
| `--color-border` | `#DBEAFE` |
| `--color-destructive` | `#DC2626` |
| `--color-ring` | `#1E40AF` |

- Type: **Calistoga** (display) / **Inter** (body, 16px) / **JetBrains Mono** (all numbers, IDs, phones — tabular).
- Icons: Lucide SVG only. Dark mode: full designed pair (this style supports it natively).
- Dense spacing scale 8/16/24/32; breakpoints 375 / 768 / 1024 / 1440; sidebar ≥1024px, bottom nav below.
- Charts: recharts — legends always visible + clickable to toggle series, tooltips with exact values, subtle gridlines, empty/error/loading states on every chart, locale AED formatting.
- Signature element: the **health strip** — a persistent thin bar under the header on every page, green/amber/red from the live health check; the whole system's status is always one glance away.

## 3. Architecture

### 3.1 Stack
Identical pattern to the shop dashboard (shared conventions, §3.4): Next.js App Router + Tailwind + shadcn/ui + recharts on Vercel. Reads = RSC → Supabase direct (service-role, server-only). Writes = server actions + `useActionState` (every button: loading → success flash / inline error).

### 3.2 Auth
Same Supabase Auth email/password code as the shop dashboard, but access limited by middleware to email(s) in `OWNER_EMAILS` env. No roles table needed. This console also **provisions** the shop dashboard's users (§5.2).

### 3.3 Bridge API (local-PC operations)
~12 endpoints on the existing FastAPI (`src/app/main.py`), bearer `INTERNAL_API_TOKEN`, via Cloudflare Tunnel. Each wraps an existing service function:

```
GET  /internal/health                      → check_health (db/redis/llm/twilio/celery + metrics)
POST /internal/escalations/reply           {shop_id, phone, text}
POST /internal/escalations/handover        {shop_id, phone}
POST /internal/security/quarantine/lift    {phone}
POST /internal/security/quarantine/extend  {phone}
GET  /internal/security/quarantined        → Redis scan list
POST /internal/security/blacklist          {shop_id, phone, reason}   (DB row + Redis key together)
POST /internal/security/bypass             {phone, enabled}
POST /internal/security/forward            {phone, shop_id}
POST /internal/export/orders|rider         → {url}
POST /internal/llm/test                    → {ok, model, latency_ms, sample}   [NEW endpoint]
```
Bridge down → those buttons show "backend offline"; the Health tab is the first to say so.

### 3.4 Shared conventions with the shop-dashboard repo
Byte-identical `shared/` files (`db.ts, scope.ts, period.ts, money.ts, telegram.ts, bridge.ts, database.types.ts` — generated via `supabase gen types typescript`), header `// SHARED with mobile-shop-and-shop-owner-dashboard — edit both`, plus `CONVENTIONS.md`.

### 3.5 Audit
Every console mutation writes `audit_logs` (`actor = "dashboard:{email}"`). Destructive operations (offboard, archive, delete messages) additionally require a **typed-YES confirmation modal** — same guard the owner bot uses for message deletion.

## 4. Navigation

**Desktop sidebar:**
`Overview · Clients & Shops · Analytics · Health · Security · Escalations · Messages · Audit · AI & Settings`

**Mobile bottom nav (5 + More):**
`Overview · Clients · Health · Security · More` (More sheet: Analytics, Escalations, Messages, Audit, AI & Settings)

## 5. Per-tab specification

### 5.1 Overview
| Widget | Backend reference | Status |
|---|---|---|
| Active clients / shops / suspended counts | `clients`, `shops` (mirror `list_clients`, `shop_status`) | exists |
| Messages + AI calls today/this week sparkline | `usage_daily` — label "as of last hourly flush" (Celery beat flushes at :15) | exists |
| Orders + revenue today, platform-wide | `orders` Dubai-day | exists |
| Open escalations / open incidents badges | `pending_escalations`, `security_incidents` | exists |
| Health strip (green/amber/red) | bridge `GET /internal/health` | exists via bridge |

### 5.2 Clients & Shops — onboarding CRUD (drill: client → shops → keepers/riders)
| Action | Backend reference | Status |
|---|---|---|
| List/detail clients, shops | `TenantService.list_clients / get_client / list_shops_by_client` | exists |
| **Create client** (name, contact, phone, email) | `repo.create_client` (`db/supabase_client.py:108`) | exists (repo fn; console is the first UI) |
| **Create shop** (name, whatsapp_number, keeper + customer bot tokens, negotiation flag) | `repo.create_shop` (`db/supabase_client.py:222`) | exists (repo fn) |
| **Create shopkeeper** (name, telegram_id, is_owner) | `repo.create_shopkeeper` | exists (repo fn) |
| **Create rider** (name, phone) | mirror `add_rider` (`riders/service.py:67`) | exists |
| Pause / resume shop (+ mandatory reason) — customers get the suspension auto-reply immediately | mirror `suspend_shop` / `resume_shop` | exists |
| **Pause client** = bulk-suspend all client's shops, reason "client paused" | loop of `suspend_shop` | **NEW** (action only, no schema) |
| **"Delete" client** = `status='offboarded'` (enum already exists; hard delete forbidden — FK cascade danger) | `clients.status` | **NEW** (action only) |
| **Delete shop** = new `'archived'` status; archived hidden everywhere (incl. bot builders' shop list) | `shops.status` check + `'archived'` | **NEW** (migration 025) |
| **Delete shopkeeper** = hard delete + audit (nothing references shopkeepers) | `shopkeepers` | **NEW** (action only) |
| **Pause / delete rider** = deactivate (`active=false`); orders reference riders so never hard-delete | `delivery_persons.active` | **NEW** (migration 025) |
| **Provision dashboard logins** (create shop-dashboard users: keeper→shop, owner→client; reset password; disable) | `supabase.auth.admin.createUser` + `dashboard_users` row (migration 020) | **NEW** |
| View/edit `clients.telegram_id` (shop-owner bot link) | mirror `link_client_telegram` | exists |
| Per-shop usage panel | mirror `get_usage` over `usage_daily` | exists |

```sql
-- migration 025_status_columns.sql
alter table delivery_persons add column active boolean not null default true;
alter table shops drop constraint shops_status_check;
alter table shops add constraint shops_status_check check (status in ('active','suspended','archived'));
```

### 5.3 Analytics — the customer-demo tab
Everything derivable from existing tables; charts designed to impress a prospect with real numbers.

| View | Backend reference | Status |
|---|---|---|
| Growth: orders + revenue per week/month, per client and platform-wide (line charts, Dubai TZ) | `orders` time-bucketed | exists |
| Per-shop / per-client profit table + period compare | same math as `/owner profit all\|compare` (mirror `profit_summary`) | exists |
| Usage stacked chart: customer_msg_in / msg_out / ai_call / escalation / telegram_command per day | `usage_daily` | exists |
| **AI containment rate** — % of conversations AI handled without human escalation (the value-add number) | derived: `usage_daily` ai_call vs escalation | exists (derived) |
| **AI response latency** — median customer→assistant gap | derived from `messages` timestamps | exists (derived) |
| Top products platform-wide | profit logic | exists |
| COD outstanding by shop | `cod_ledger` | exists |
| CSV export of any view | client-side CSV from loaded data | trivial NEW |

### 5.4 Health
| Widget | Backend reference | Status |
|---|---|---|
| Subsystem cards: db / redis / llm / twilio / celery — ok/down strings verbatim from backend | bridge → `check_health` (`src/app/reports/health.py:47`) | exists via bridge |
| Active conversations + quarantined counts | `HealthReport.metrics` | exists |
| Auto-refresh 60s; bridge unreachable = the red state itself | — | — |
| Health history graph | skipped — YAGNI; the 60s Celery beat already pages the owner on Telegram when unhealthy | skipped |

### 5.5 Security
| View / Action | Backend reference | Status |
|---|---|---|
| Incidents list, filters by status (open/extended/lifted/blacklisted) and attack_type (injection/sql/rapid/crossshop/admincmd/credprobe) | `security_incidents` (mirror `recent_incidents`) | exists |
| Incident detail incl. `message_snapshot` jsonb viewer (last-25 messages) | mirror `get_incident` / bot `/investigate` | exists |
| Lift / extend quarantine | bridge (mirror `lift_quarantine` / `extend_quarantine`) | exists via bridge |
| Currently-quarantined list | bridge Redis scan | exists via bridge |
| Blacklist phone (+reason); blacklist table | bridge (mirror `blacklist` — DB row + Redis key); table reads `blacklisted_phones` direct | exists via bridge |
| Bypass AI set / remove | bridge (mirror `set_bypass` / `remove_bypass`) | exists via bridge |
| Forward customer to shop | bridge (mirror `forward_to_shop`) | exists via bridge |
| **Ignored-messages log** ("no message silently lost"): every non-AI pipeline outcome, filterable by action/shop/date with daily counts | today only `attack` persists (as incidents); `blacklisted / quarantined / frozen / bypass / too_long / rate_capped / locked / duplicate / suspended` are log-only — verified in `src/app/messaging/pipeline.py` | **NEW** (migration 024 + 8-line insert) |

```sql
-- migration 024_pipeline_events.sql
create table pipeline_events (
  id uuid primary key default gen_random_uuid(),
  shop_id uuid references shops(id) on delete set null,
  identity text not null,
  action text not null,
  created_at timestamptz not null default now()
);
create index idx_pipeline_events on pipeline_events(action, created_at desc);
```
Python: fire-and-forget insert in `pipeline.py::_dispatch` when `action != "ai"` (never raises, never blocks the reply path).

### 5.6 Escalations
Open list (`pending_escalations`, mirror `list_open`) → click through to full conversation context (`messages`) → **Reply** (bridge) → **Handover/Resolve** (bridge, unfreezes AI) → resolved history (`resolved_at not null`). All exists (+bridge).

### 5.7 Messages
- Cross-shop conversation browser + transcript viewer (mirror `messaging/store.py: conversations / transcript`).
- **Delete messages**: all / per-shop / date-range — typed-YES confirmation modal (mirror `delete_messages`, DB-only so direct from console) + audit row. Mirrors the owner bot's 🧹 menu exactly.

### 5.8 Audit
`audit_logs` explorer: filters shop / actor / action / date, jsonb `detail` viewer, newest-first (mirror `audit.recent`). Includes both bot actors and `dashboard:*` actors — one unified trail.

### 5.9 AI & Settings (**NEW tab**)
Runtime AI model switching (today env-only: `ai_provider=moonshot`, `ai_model=kimi-k2.6`).

```sql
-- migration 023_platform_settings.sql
create table platform_settings (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now()
);
```
- Keys: `ai_provider` (`moonshot|openai|openrouter`), `ai_base_url`, `ai_model`. **API keys stay in env** — one per provider; console never stores or displays secrets.
- UI: provider select → base_url auto-fill → model input (optionally populated from OpenRouter's public `/models` list, client-side fetch) → **Save** → **Test** button hits bridge `/internal/llm/test` (one cheap completion; shows model, latency, sample reply).
- Python change (the one meaningful backend edit): `LLMClient` overlays `platform_settings` values over env with env fallback, cached ~60s (`src/app/llm/llm_client.py`, ~15 lines). Bots pick up a model switch within a minute, no restart.
- Also on this tab: bridge URL + token status (reachable? last check), `whatsapp_mocked` flag (read-only), Supabase project link.

## 6. Route / action structure

```
app/
  (auth)/login/page.tsx
  (app)/layout.tsx                    ← health strip, sidebar/bottom nav
  (app)/page.tsx                      ← Overview
  (app)/clients/{page,[id]/page}.tsx
  (app)/shops/[id]/page.tsx
  (app)/analytics/page.tsx
  (app)/health/page.tsx
  (app)/security/{page,[id]/page}.tsx
  (app)/escalations/page.tsx
  (app)/messages/page.tsx
  (app)/audit/page.tsx
  (app)/settings/page.tsx             ← AI & Settings
lib/   ← same shared/ set as shop dashboard
actions/
  tenants.ts      createClient offboardClient pauseClient createShop suspendShop resumeShop archiveShop
                  createShopkeeper deleteShopkeeper createRider setRiderActive linkClientTelegram
  users.ts        provisionDashboardUser resetPassword disableDashboardUser
  security.ts     liftQuarantine extendQuarantine blacklist setBypass forwardToShop        [bridge]
  escalations.ts  reply handover                                                           [bridge]
  messages.ts     deleteMessages(scope)
  settings.ts     saveAiSettings testLlm                                                   [bridge]
```
Every action: auth check (OWNER_EMAILS) → validate → write → `audit()` → `{ok} | {error}`.

## 7. New DB migrations (this console's share)
| Migration | Contents |
|---|---|
| `020_dashboard_users.sql` | shop-dashboard auth mapping (provisioned from here) |
| `023_platform_settings.sql` | runtime AI config (§5.9) |
| `024_pipeline_events.sql` | ignored-message log (§5.5) |
| `025_status_columns.sql` | `delivery_persons.active`; shops `'archived'` status (§5.2) |

(021 invoices / 022 counter_sales belong to the shop dashboard plan; full set is 020–025.)

## 8. Python backend changes (complete minimal list; bots untouched)
1. **Bridge endpoints** in `src/app/main.py` (~100 lines total incl. shop-dashboard's): each a thin wrapper over `escalations.service`, `security.service`, `check_health`, `export_orders/export_rider`, plus the new `llm/test`. `INTERNAL_API_TOKEN` added to `config/settings.py`, constant-time bearer check.
2. **`src/app/llm/llm_client.py`**: `platform_settings` overlay with env fallback, 60s cache (~15 lines).
3. **`src/app/messaging/pipeline.py`**: `pipeline_events` fire-and-forget insert for `action != "ai"` (~8 lines).
4. **Ops**: `cloudflared tunnel` → local FastAPI; Task Scheduler entry; README section.
5. Explicitly NOT changed: bot handlers, Celery tasks, `profit_summary`, WhatsApp webhook.

## 9. Build phases
- **P0 (backend)**: migrations 020 + 023–025, bridge endpoints, tunnel, LLM overlay, pipeline-events insert.
- **P1**: auth + Overview + Clients/Shops read views + Health tab.
- **P2**: tenant CRUD (create/pause/offboard/archive), keeper + rider management, **dashboard-user provisioning** (unblocks shop-dashboard logins — sequenced early on purpose).
- **P3**: Analytics (charts + derived stats + CSV export).
- **P4**: Security tools, Escalations, Messages (incl. delete), Audit explorer.
- **P5**: AI & Settings + pipeline-events view + mobile polish + dark mode audit.

Cross-repo sequence: P0 → this console P1–P2 → shop dashboard P1–P5 → this console P3–P5.

## 10. Owner-bot parity checklist (tick each during verification)
/pauseshop, /resumeshop → Clients & Shops pause/resume · /shopstatus → shop detail · /addrider, /riders → rider drill · /owner dashboard → Overview · /owner profit [all|compare|shop] → Analytics · /owner health → Health · /owner escalations → Escalations · /owner security → Security incidents · /owner audit → Audit · /investigate → incident detail · /quarantine_lift, /quarantine_extend → Security buttons · /blacklist → Security blacklist · /forward_to_shop → Security forward · /bypass_ai, /bypass_remove → Security bypass · 🧹 delete messages (all/shop/range) → Messages tab · owner menu buttons (o-prefix callbacks) → all covered by the above. **Complete.**

## 11. Verification
1. **Access control**: non-allowlisted email → login rejected; every route behind middleware.
2. **Parity checklist** (§10): perform each action in the console; confirm identical DB/Redis effects as the bot command (incident status flips, Redis key set/cleared via bridge, audit row written).
3. **Onboarding E2E**: create client → shop (with bot tokens) → shopkeeper → rider → provision a keeper login → that login works on the shop dashboard scoped to exactly that shop; pause shop → customer bot answers with suspension message; archive shop → disappears from all views and bot builders.
4. **AI switch E2E**: change model in AI & Settings → Test button returns new model name → within 60s a live customer-bot message is answered by the new model (check `pipeline_events`/logs).
5. **Ignored-messages**: send a blacklisted-phone message on staging → row appears in pipeline events with `action='blacklisted'`.
6. **Bridge degradation**: tunnel off → Health strip red, bridge buttons disabled with "backend offline", direct-DB tabs still work.
7. **Analytics correctness**: profit table cross-checked against Python `profit_summary` on same staging data; containment rate hand-checked against `usage_daily` rows.
8. **UI quality gate**: 375px + landscape, dark mode contrast, keyboard nav, ≥44px targets, no emoji icons, chart empty/error/loading states, typed-YES on all destructive actions.
