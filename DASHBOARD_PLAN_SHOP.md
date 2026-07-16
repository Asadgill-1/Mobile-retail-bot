# Shop & Shop-Owner Dashboard — Full Plan

Repo: `https://github.com/Asadgill-1/mobile-shop-and-shop-owner-dashboard.git` → deployed on Vercel.
One web app, two roles: **Keeper** (staff of one shop) and **Shop Owner** (client owning 1+ shops, full remote oversight and anti-corruption visibility).
Every feature below is mapped to a verified function/table in the existing Python backend (`new retail v2` project). Items with no backend support are flagged **NEW** with their exact migration.

---

## 1. System context (what already exists — verified in code)

- Multi-tenant: `clients` (1 owner → N shops) → `shops` → `shopkeepers`, `delivery_persons` (riders), `products`, `orders`. Supabase Postgres + private Storage buckets `shop-media` (product images/video) and `shop-reports` (Excel exports).
- 7 Telegram bots live: owner control, per-shop keeper, per-shop customer AI bot (Moonshot kimi-k2.6), rider, shop-owner. The dashboard mirrors every keeper command and every shop-owner bot button (parity checklists in §10).
- WhatsApp: Twilio webhook exists (`src/app/whatsapp/webhook.py`), inbound dormant until Stage 13; outbound not built. Chats flow through the same `messages` table — when WhatsApp activates, the dashboard Chats tab needs **zero changes**.
- RLS is off (migration `006_rls_lockdown.sql`); tenant isolation is app-layer `shop_id`/`client_id` scoping. The dashboard must enforce the same scoping server-side.
- Redis + Celery + FastAPI run on the platform owner's PC — reached via the Bridge API (§3.3).

## 2. Design system

Style: **flat, touch-first, zero elevation** — color blocking, solid icon containers, bottom tabs solid fill, press feedback scale 0.97 (150ms).

| Token | Value |
|---|---|
| `--color-primary` | `#334155` (industrial slate) |
| `--color-on-primary` | `#FFFFFF` |
| `--color-secondary` | `#475569` |
| `--color-accent` | `#059669` (stock green — CTAs, success, in-stock) |
| `--color-background` | `#F8FAFC` |
| `--color-foreground` | `#0F172A` |
| `--color-muted` | `#F2F3F4` |
| `--color-border` | `#E6E8EA` |
| `--color-destructive` | `#DC2626` |
| `--color-ring` | `#334155` |

- Type: **Rubik** (display/headings) / **Nunito Sans** (body, 16px base, line-height 1.5). Tabular figures for all money/qty columns.
- Icons: **Lucide SVG only** — never emoji. One stroke width (2px) throughout.
- Dark mode: full pair designed together (`data-theme` + `prefers-color-scheme`), contrast re-checked ≥4.5:1 in both.
- Spacing: 8pt system, dense dashboard scale (8/16/24/32). Breakpoints 375 / 768 / 1024 / 1440, mobile-first.
- Touch targets ≥44×44px, 8px+ gaps. `touch-action: manipulation`. Skeleton screens for loads >300ms. `prefers-reduced-motion` respected.
- Signature element: the **stock-green pulse** — every successful save flashes the affected row/card border green for 400ms; the whole app's "it worked" language.

## 3. Architecture

### 3.1 Stack
Next.js App Router + Tailwind + shadcn/ui + recharts on Vercel.
- **Reads** = React Server Components querying Supabase directly (`@supabase/supabase-js`, service-role key in server-only module: `import "server-only"` — never shipped to browser).
- **Writes** = server actions with `useActionState` → every button gets loading state + success flash + inline error (`{ok} | {error: string}` shape). No silent failures.
- Media upload: browser → `createSignedUploadUrl` (issued by server action) → direct PUT to Storage (avoids Vercel body limits for videos).

### 3.2 Auth + permissions
Supabase Auth **email/password**. No self-signup — accounts created only by the platform owner from the Owner Console (Dashboard 2).

```sql
-- migration 020_dashboard_users.sql
create table dashboard_users (
  user_id   uuid primary key references auth.users(id) on delete cascade,
  role      text not null check (role in ('keeper','owner')),
  shop_id   uuid references shops(id),     -- set when role='keeper'
  client_id uuid references clients(id),   -- set when role='owner'
  created_at timestamptz not null default now()
);
```

Session cookie via `@supabase/ssr` middleware. Every RSC/action starts:
`getScope()` → `{role, shopIds[], clientId?}` — keeper: `shopIds=[shop_id]`; owner: `shopIds =` all shops where `client_id` matches.
`assertShop(scope, shopId)` mirrors the bot's `_own_shop` guard (`src/app/telegram_bot/bot.py:2040`): unknown shop and foreign shop return the **identical 404** — never confirm another tenant's resource exists.

### 3.3 Bridge API (features that live on the local PC)
Escalation reply/handover (Redis freeze keys), Excel export (openpyxl), live health — these run in Python next to Redis. A ~12-endpoint internal API is added to the existing FastAPI app (`src/app/main.py`), bearer-token secured (`INTERNAL_API_TOKEN`, constant-time compare), exposed via **Cloudflare Tunnel** (free, stable hostname).

Used by this dashboard:
```
POST /internal/escalations/reply     {shop_id, phone, text}
POST /internal/escalations/handover  {shop_id, phone}
POST /internal/export/orders         {shop_id, filter, detailed} → {url}   (24h signed URL)
POST /internal/export/rider          {shop_id, rider_id, period} → {url}
```
Degradation: bridge down → only those buttons show "backend offline — try again later"; everything else keeps working.

### 3.4 Shared conventions with the Owner Console repo
`shared/` folder of byte-identical files in both repos, each headed `// SHARED with owner-dashboard-mobile — edit both`:
`db.ts` (service-role client) · `scope.ts` (tenant guard) · `period.ts` (Dubai TZ) · `money.ts` (AED) · `telegram.ts` (Bot API sends) · `bridge.ts` (bridge client) · `database.types.ts` (**generated**: `supabase gen types typescript` — regenerate after every migration; divergence = compile error). Plus one `CONVENTIONS.md` in both repos.

### 3.5 Telegram notifications from dashboard writes
Server actions call `https://api.telegram.org/bot{token}/sendMessage` directly (per-shop keeper/customer tokens from the `shops` row; rider bot token as env var). Semantics mirror `src/app/telegram_bot/notify.py`: **best-effort, never throws, never fails the DB write** — notify failure surfaces as a non-blocking toast ("Saved. Rider not reached — they may not have pressed /start yet").

### 3.6 Time, money, audit
- `period.ts` ports `parse_period` (`src/app/reports/service.py:20`): today | yesterday | weekly | monthly | custom date, **Dubai UTC+4 fixed offset** (UAE has no DST).
- `money.ts` = `Intl.NumberFormat("en-AE", {style:"currency", currency:"AED"})`.
- Every mutation inserts an `audit_logs` row (`actor = "dashboard:{email}"`) — same trail the bots write via `_audit`. Owner reads of chat transcripts are audited too (parity with the shop-owner bot).

## 4. Navigation

**Desktop sidebar — Keeper:**
`Home · Orders · Inventory · POS · Chats · Riders & COD · Reports · Settings`

**Desktop sidebar — Shop Owner:** all of the above **plus** `Shops` and `Oversight`, with a persistent **shop switcher** in the header (each shop + "All shops").

**Mobile bottom nav (5 slots max, labels + icons):**
- Keeper: `Home · Orders · POS · Inventory · More` (More sheet: Chats, Riders & COD, Reports, Settings)
- Owner: `Home · Orders · Chats · Oversight · More` (More: Shops, Inventory, POS, Riders & COD, Reports, Settings)

Active tab highlighted; badges (drafts, price requests, escalations) cleared after visit; back preserves scroll/filter state.

## 5. Per-tab specification

### 5.1 Home
| Widget | Backend reference | Status |
|---|---|---|
| Orders today by status (pill row) | `orders` grouped by status, Dubai day | exists |
| Revenue / profit today card | mirror `profit_summary` (`src/app/orders/service.py:123`) | exists |
| Draft orders inbox badge → Orders | mirror `list_drafts` (`orders/service.py:546`) | exists |
| Pending price requests badge | mirror `list_price_requests` (`orders/service.py:560`) | exists |
| Open escalations count | `pending_escalations where resolved_at is null` (mirror `escalations.count_open`) | exists |
| Low-stock list | `products` qty ≤ threshold; code as `min_qty ?? 2` (forward-compatible with pending migration 010) | exists |
| COD outstanding card | Σcollect−Σhandover from `cod_ledger` (mirror `cod_balance`, `riders/service.py:429`) | exists |

### 5.2 Orders
| View / Action | Backend reference | Status |
|---|---|---|
| Drafts inbox (list + detail) | `list_drafts` | exists |
| **Confirm draft** | mirror `confirm_order` (`orders/service.py:442`): atomic `decrement_stock` RPC (out-of-stock → clear error), status→confirmed, `order_status_history` row, audit row, Telegram notify customer via shop's customer bot | exists |
| **Reject draft** (reason) | mirror `reject_order` (`orders/service.py:536`) | exists |
| All-orders table: filter status/date/rider, search order # / customer / phone | `orders` + joins | exists |
| Order detail with status timeline | `order_status_history` | exists |
| Advance status one step (confirmed→packed→shipped→delivered) | mirror `advance_delivery` (`orders/service.py:464`) — single-step rule enforced server-side | exists |
| **Assign rider** (picker) | mirror `assign_delivery` (`orders/service.py:491`): rider_id + cod_amount + custody='offered' + custody_at; Telegram push to rider | exists |
| Cancel order (remarks **mandatory**) | status='cancelled' + `cancel_remarks` + history + audit | exists |
| Manual order form | mirror `create_order` (`orders/service.py:74`) | exists |
| Price requests inbox: **Approve / Counter / Deny** + customer notified | mirror `approve_price` / `deny_price` (`orders/service.py:295,316`); Counter = approve with custom price (bot `/custom`) | exists |
| Excel export (today/yesterday/date/pending/all × plain/detailed) | bridge `POST /internal/export/orders` → open signed URL | exists via bridge |
| Generate invoice on delivered order | §5.5 Invoices | **NEW** |

### 5.3 Inventory
| View / Action | Backend reference | Status |
|---|---|---|
| Product grid/table: image thumb, brand/model/color, condition, qty (low-stock highlighted), cost/sell price, boost, tags, featured; filters category/brand/condition/featured/low-stock; text search | `products` (mirror `list_inventory`, `products/service.py:262`); search = `ilike`, order `boost_level desc` | exists |
| Add product form: category (Mobile/Laptop/Tablet/Accessory), brand, model, color, condition (New/Used/Refurbished), specs (key:value editor → jsonb), cost, sell, qty, tags | mirror `create_product` (`products/service.py:147`) + validators (`parse_category/condition/price/quantity/spec_line`) — same fields as the bot's 11-step `/addproduct` flow | exists |
| Full edit of any field | `products` update + audit — no schema change | exists (new surface) |
| Upload images (max 5) + one video | signed upload URL → `shop-media/{shop_id}/{product_id}/{file}` (mirror `products/media.py`, `MAX_IMAGES=5`); update `images[]` / `video_url` | exists |
| Remove/replace media | array update + storage remove | exists (new surface) |
| Boost slider 0–10 | mirror `set_boost` (`products/service.py:230`) | exists |
| Tag chips (add from 11-tag whitelist / remove / clear) | mirror `add_tags`/`remove_tag`/`clear_tags` | exists |
| Featured toggle | mirror `toggle_featured` | exists |
| Quick stock ± buttons | `quantity` update + audit | exists (new surface) |
| Delete product | blocked if any `orders.product_id` references it (clear error), else delete + storage cleanup | **NEW** (rule only, no schema) |
| Product stats | honest empty state — "not tracked yet", same as bot `productstats_cmd` | exists (deliberately empty) |

### 5.4 POS — counter sales (**NEW tab**)
Full point-of-sale for walk-in counter sales. Table shape **aligned with the pending backlog's `counter_sales`** so the future photo-AI recording flow writes to the same table.

```sql
-- migration 022_counter_sales.sql
create table counter_sales (
  id uuid primary key default gen_random_uuid(),
  shop_id uuid not null references shops(id) on delete cascade,
  product_id uuid not null references products(id) on delete restrict,
  quantity integer not null check (quantity != 0),   -- negative = void reversal
  unit_price numeric(12,2) not null check (unit_price >= 0),
  discount_amount numeric(12,2) not null default 0 check (discount_amount >= 0),
  payment_method text not null check (payment_method in ('cash','card')),
  customer_name text, customer_phone text,
  sold_by text not null,                              -- dashboard:{email}
  invoice_id uuid,                                    -- fk added in 021
  created_at timestamptz not null default now()
);
create index idx_counter_sales_shop_day on counter_sales(shop_id, created_at desc);
```

| View / Action | Design |
|---|---|
| Product search + cart | reads `products` (in-stock only); big touch targets; barcode-style fast search by brand/model |
| Cart lines: qty, unit-price override, per-line discount | client state until checkout |
| Checkout (cash / card) | per line: existing atomic `decrement_stock` RPC → insert `counter_sales` rows → audit row → optional auto-invoice (§5.5) |
| Today's sales list | `counter_sales` Dubai-day |
| Void sale | insert reversing row (negative qty) + restock via `decrement_stock` with negative n — never delete |

### 5.5 Invoices — UAE tax invoices (**NEW**)
Covers both online orders and counter sales, per FTA requirements.

```sql
-- migration 021_invoices.sql
alter table shops add column trn text, add column invoice_name text, add column invoice_address text;
create table invoices (
  id uuid primary key default gen_random_uuid(),
  shop_id uuid not null references shops(id) on delete cascade,
  source text not null check (source in ('order','counter')),
  order_id uuid references orders(id),
  counter_sale_ids uuid[],
  invoice_number bigint generated always as identity,
  customer_name text, customer_phone text,
  items jsonb not null,                 -- [{desc, qty, unit_price, line_total}]
  subtotal numeric(12,2) not null,
  vat_rate numeric(5,2) not null default 5,
  vat_amount numeric(12,2) not null,
  total numeric(12,2) not null,
  issued_at timestamptz not null default now(),
  created_by text not null
);
create unique index invoices_number_key on invoices(invoice_number);
```

- Retail prices are **VAT-inclusive** → `vat_amount = total × 5/105` (one tested util in `money.ts`).
- Sequential numbering satisfied by the identity column; displayed `INV-000123`.
- Rendering: HTML page `/invoices/[id]/print` with print stylesheet — "TAX INVOICE" header, shop `invoice_name` / `invoice_address` / **TRN**, invoice number, date, customer, line items, subtotal, VAT 5% breakout, AED total. Browser print / save-as-PDF. (No server PDF lib — add only if emailed invoices are ever needed.)
- Views: invoice list (period filter), create-from-order (delivered orders), auto-create at POS checkout.

### 5.6 Chats (Telegram now, WhatsApp at Stage 13 — same table)
| View / Action | Backend reference | Status |
|---|---|---|
| Conversations list per shop, latest-first, escalation badge | `messages` grouped by identity (mirror `messaging/store.py: conversations`) + open `pending_escalations` join | exists |
| Transcript (customer / AI / shopkeeper roles, ~10s polling) | mirror `transcript` (`store.py:76`) | exists |
| Escalations inbox | mirror `escalations.list_open` | exists |
| **Reply to customer** (only while escalated/frozen) | bridge `POST /internal/escalations/reply` (Redis freeze respected, message sent, archived) | exists via bridge |
| **Handover back to AI** | bridge `POST /internal/escalations/handover` (resolve + unfreeze) | exists via bridge |
| Channel indicator | `shops.whatsapp_number` shown; "WhatsApp inbound: coming (Stage 13)" note | exists (dormant) |

### 5.7 Riders & COD
| View / Action | Backend reference | Status |
|---|---|---|
| Riders list + Telegram-linked badge | `delivery_persons` (mirror `list_riders`) | exists |
| Add rider (name, phone) — rider links himself by pressing /start on rider bot | mirror `add_rider` (`riders/service.py:67`) | exists |
| Per-rider deliveries + custody chain (offered/accepted/disputed) | `orders where rider_id` (mirror `my_deliveries`) | exists |
| COD ledger table per rider | `cod_ledger` (mirror `cod_rows`) | exists |
| COD balance cards | mirror `cod_balance` | exists |
| **Reconcile COD** (amount + note → handover row, trail shown, rider notified) | mirror `reconcile_cod` (`riders/service.py:439`) | exists |
| Delivered report (period) | mirror `delivered_report` | exists |
| Rider route Excel export | bridge `/internal/export/rider` | exists via bridge |

### 5.8 Reports
| View | Backend reference | Status |
|---|---|---|
| Profit dashboard (today/yesterday/weekly/monthly/custom): revenue, discounts, cost, gross profit, margin %, top-5 products, clearance candidates — with charts | mirror `profit_summary` + `format_profit` math | exists |
| Include-counter-sales toggle | adds `counter_sales` to revenue/cost query (dashboard-side only; Python untouched) | **NEW** (query only) |
| Top products | from profit logic | exists |
| COD outstanding | as §5.7 | exists |
| Exports hub | all bridge export buttons + links | exists via bridge |

### 5.9 Owner-only: Shops + Oversight (anti-corruption) + Settings
| View / Action | Backend reference | Status |
|---|---|---|
| Shops overview cards (status, today's numbers) | `shops by client_id` (mirror `list_shops_by_client`) | exists |
| Compare shops profit side-by-side (period) | mirror `/owner profit compare` math per owned shop | exists |
| **Cancelled orders + remarks** (all shops, filter shop/period) — remarks front-and-center | mirror `cancelled_orders` (`orders/service.py:579`) | exists |
| **Discounted orders** | mirror `discounted_orders` (`orders/service.py:598`) | exists |
| **Activity log** (filter shop/actor/action/date) — what every keeper did | `audit_logs` (mirror `audit.recent`) | exists |
| Cross-shop chat transcripts (each view audited) | `messages` | exists |
| COD balances across all shops | `cod_ledger` | exists |
| Settings: negotiation on/off per shop | mirror `set_negotiation` (`orders/service.py:355`) | exists |
| Settings: invoice TRN / legal name / address per shop | new `shops` columns (021) | **NEW** |
| Settings: staff list per shop (read-only; provisioning in Owner Console) | `shopkeepers` | exists |

## 6. Route / action structure

```
app/
  (auth)/login/page.tsx
  (app)/layout.tsx                 ← scope loader, shop switcher (owner), sidebar/bottom nav
  (app)/page.tsx                   ← Home
  (app)/orders/{page,drafts/page,price-requests/page,[id]/page}.tsx
  (app)/inventory/{page,new/page,[id]/page}.tsx
  (app)/pos/page.tsx
  (app)/chats/{page,[identity]/page}.tsx
  (app)/riders/{page,[id]/page}.tsx
  (app)/reports/page.tsx
  (app)/invoices/{page,[id]/print/page}.tsx
  (app)/oversight/{cancels,discounts,activity,transcripts}/page.tsx   [owner-gated]
  (app)/settings/page.tsx
  api/export/route.ts              ← proxies bridge, returns {url}
lib/   db.ts · scope.ts · period.ts · money.ts · telegram.ts · bridge.ts · audit.ts   [shared/]
actions/
  orders.ts    confirmOrder rejectOrder advanceDelivery assignDelivery cancelOrder createOrder
               approvePrice counterPrice denyPrice
  products.ts  createProduct updateProduct setBoost addTag removeTag clearTags toggleFeatured
               adjustStock deleteProduct
  media.ts     getSignedUploadUrl attachMedia removeMedia
  pos.ts       recordCounterSale voidCounterSale
  invoices.ts  createInvoice
  riders.ts    addRider reconcileCod
  chats.ts     replyEscalation handoverEscalation      [bridge]
  settings.ts  setNegotiation saveInvoiceSettings
```
Every action: `getScope()` → `assertShop()` → validate → write → `audit()` → best-effort Telegram notify → `{ok} | {error}`.

## 7. New DB migrations (this dashboard's share)
Numbering starts at **020** (pending backlog owns 010).

| Migration | Contents |
|---|---|
| `020_dashboard_users.sql` | auth mapping table (§3.2) |
| `021_invoices.sql` | shops TRN fields + invoices table (§5.5) |
| `022_counter_sales.sql` | POS table (§5.4) — becomes no-op if the paused backlog's counter_sales lands first |

## 8. Python backend changes (minimal; bots untouched)
1. Bridge endpoints in `src/app/main.py`: escalations reply/handover, export orders/rider (~40 lines, each a thin wrapper over existing service functions) + `INTERNAL_API_TOKEN` in `config/settings.py`.
2. Ops: `cloudflared tunnel` pointing at local FastAPI (documented; Task Scheduler entry).

## 9. Build phases
- **P0 (backend)**: migrations 020–022 + bridge endpoints + tunnel.
- **P1**: scaffold, auth, scope guard, read-only Home / Orders / Inventory / Chats / Reports (proves scoping + Dubai period math).
- **P2**: all mutations — order lifecycle, price requests, product CRUD + media upload, riders/COD, negotiation. Audit + Telegram notifies wired.
- **P3**: POS + Invoices.
- **P4**: owner role — shop switcher, Oversight views, compare, exports (bridge), escalation reply/handover (bridge).
- **P5**: mobile nav polish, empty/error/loading states everywhere, AED + Dubai-TZ sweep, dark mode audit.

(Sequenced after Owner Console P1–P2 so logins can be provisioned.)

## 10. Bot-parity checklists (tick each during verification)

**Keeper commands (26) → dashboard location:**
/boost, /unboost → Inventory boost slider · /tag, /untag, /cleartags → tag chips · /feature → featured toggle · /profit → Reports · /orders → Orders drafts · /confirmorder → Confirm button · /rejectorder → Reject · /deliveryupdate → Advance step · /riders → Riders list · /assigndelivery → Assign picker · /reconcilecod → Reconcile · /negotiation → Settings toggle · /approveprice → Approve · /custom → Counter · /denyprice → Deny · /pricerequests → Price inbox · /exportorders → Export hub · /exportrider → Rider export · /productstats → Inventory stats (empty state) · /reply → Chats reply · /handover → Chats handover · /addproduct → Add product form · /start /help /menu → login + app shell.

**Shop-owner bot callbacks → dashboard location:**
sshops → Shops cards · sprof → Reports profit (per shop periods) · sord → Orders filtered · sinv → Inventory · scod → COD balances · sexp/sexpd → Export hub · smsg/smsgc → Chats transcripts · scmp → Compare shops · stop → Top products · scan → Oversight cancels+discounts · scodall → COD across shops.

## 11. Verification
1. **Tenant isolation probe**: log in as keeper-A, request shop-B's order/product/conversation by URL → identical 404s (mirror `_own_shop` semantics). Run before every deploy.
2. **Parity checklists** (§10): perform each action in the dashboard; confirm the same DB effects the bot produces (status row + history row + audit row + Telegram message on test account).
3. **Money tests** (vitest): `period.ts` Dubai day boundaries (weekly/monthly edges); profit query vs Python `profit_summary` output on same staging data; VAT `total × 5/105` rounding.
4. **E2E happy path** on staging shop: dashboard draft → confirm (stock actually decremented) → assign rider (rider bot receives push) → rider delivers via bot → COD ledger row appears in dashboard → reconcile from dashboard → Excel export opens.
5. **Bridge degradation**: stop tunnel → reply/handover/export buttons show "backend offline"; all other tabs work.
6. **Audit completeness**: after E2E, `audit_logs` has one row per dashboard mutation with `actor=dashboard:*`.
7. **UI quality gate**: 375px + landscape pass, dark mode contrast pass, keyboard nav + focus rings, all touch targets ≥44px, no emoji icons, skeletons on every async view, every destructive action confirmed.
