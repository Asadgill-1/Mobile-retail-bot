-- =====================================================================
-- 001_init.sql — Multi-shop chatbot: initial schema + RLS + indexes + seed
-- Source: docs/SPEC-source.md §1, §4, §6, §15. ADR-003 (RLS isolation).
-- Run on Supabase Postgres (SQL editor) or psql.
-- Money = DECIMAL (never float). IDs = UUID. Timestamps = TIMESTAMPTZ UTC.
-- =====================================================================

-- Extensions
create extension if not exists "pgcrypto";   -- gen_random_uuid()

-- ---------------------------------------------------------------------
-- 0. clients  (service-provider client businesses; one client -> many shops)  ADR-006
-- Owner-level table (not tenant-scoped; no RLS). The owner (service provider)
-- manages clients; shopkeepers never touch this.
-- ---------------------------------------------------------------------
create table if not exists public.clients (
    id             uuid primary key default gen_random_uuid(),
    name           text        not null,                  -- client business name
    contact_name   text,
    contact_phone  text,
    email          text,
    status         text        not null default 'active'
                   check (status in ('active','offboarded')),
    created_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 1. shops  (tenant root; belongs to a client)  ADR-006 adds client_id
-- ---------------------------------------------------------------------
create table if not exists public.shops (
    id                uuid primary key default gen_random_uuid(),
    client_id         uuid not null references public.clients(id) on delete restrict,
    name              text        not null,
    whatsapp_number   text        unique,                 -- Twilio 'To' lookup target (SPEC §1)
    status            text        not null default 'active'
                      check (status in ('active','suspended')),
    suspension_reason text,
    -- ADR-005: per-shop Telegram bots (Telegram-first testing). NULL until onboarding.
    -- Secrets: populated at runtime via owner onboarding, never committed in seeds.
    telegram_keeper_bot_token   text,                    -- shopkeeper-side staff bot
    telegram_customer_bot_token text,                    -- customer-facing bot (="WhatsApp" channel in testing)
    telegram_customer_chat_id   bigint,                  -- test chat id (userbot side)
    created_at        timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 2. shopkeepers  (is_owner boolean)
-- ---------------------------------------------------------------------
create table if not exists public.shopkeepers (
    id          uuid primary key default gen_random_uuid(),
    shop_id     uuid not null references public.shops(id) on delete cascade,
    telegram_id bigint not null,                          -- Telegram user id (bigint)
    name        text,
    is_owner    boolean not null default false,
    created_at  timestamptz not null default now(),
    unique (telegram_id)                                  -- one telegram account = one shopkeeper
);

-- ---------------------------------------------------------------------
-- 3. products  (JSONB specs, boost_level, tags, is_featured, prices)
-- ---------------------------------------------------------------------
create table if not exists public.products (
    id            uuid primary key default gen_random_uuid(),
    shop_id       uuid not null references public.shops(id) on delete cascade,
    category      text not null check (category in ('Mobile','Laptop','Tablet','Accessory')),
    brand         text not null,
    model         text not null,
    color         text,
    condition     text not null check (condition in ('New','Used','Refurbished')),
    specs         jsonb not null default '{}'::jsonb,     -- flexible key:value (SPEC §4)
    cost_price    numeric(12,2) not null check (cost_price >= 0),
    selling_price numeric(12,2) not null check (selling_price >= 0),
    quantity      integer not null default 0 check (quantity >= 0),
    images        text[] not null default '{}',
    video_url     text,
    boost_level   integer not null default 0 check (boost_level between 0 and 10),
    tags          text[] not null default '{}',           -- clearance/trending/best_camera/...
    is_featured   boolean not null default false,
    created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 4. delivery_persons  (minimal — see Q-006; extend in Stage 8)
-- ---------------------------------------------------------------------
create table if not exists public.delivery_persons (
    id          uuid primary key default gen_random_uuid(),
    shop_id     uuid not null references public.shops(id) on delete cascade,
    name        text not null,
    phone       text not null,
    created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 5. orders  (selling_price, discount_amount)
-- ---------------------------------------------------------------------
create table if not exists public.orders (
    id                   uuid primary key default gen_random_uuid(),
    shop_id              uuid not null references public.shops(id) on delete cascade,
    customer_name        text not null,
    phone                text not null,
    address              text not null,
    product_id           uuid not null references public.products(id) on delete restrict,
    quantity             integer not null check (quantity > 0),
    selling_price        numeric(12,2) not null check (selling_price >= 0),  -- actual charged
    discount_amount      numeric(12,2) not null default 0 check (discount_amount >= 0),
    delivery_date        date,
    rider_id             uuid references public.delivery_persons(id) on delete set null,
    special_instructions text,
    status               text not null default 'pending'
                         check (status in ('pending','confirmed','packed','shipped','delivered','cancelled')),
    created_at           timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 6. order_status_history
-- ---------------------------------------------------------------------
create table if not exists public.order_status_history (
    id          uuid primary key default gen_random_uuid(),
    order_id    uuid not null references public.orders(id) on delete cascade,
    status      text not null,
    changed_at  timestamptz not null default now(),
    changed_by  text                                 -- telegram_id or 'system'
);

-- ---------------------------------------------------------------------
-- 7. pending_escalations  (SPEC §3)
-- ---------------------------------------------------------------------
create table if not exists public.pending_escalations (
    id          uuid primary key default gen_random_uuid(),
    shop_id     uuid not null references public.shops(id) on delete cascade,
    phone       text not null,
    message     text not null,
    created_at  timestamptz not null default now(),
    resolved_at timestamptz
);

-- ---------------------------------------------------------------------
-- 8. security_incidents  (SPEC §7; last 25 messages snapshot)
-- ---------------------------------------------------------------------
create table if not exists public.security_incidents (
    id               uuid primary key default gen_random_uuid(),
    shop_id          uuid references public.shops(id) on delete set null,
    phone            text not null,
    attack_type      text not null,                   -- injection|sql|rapid|crossshop|admincmd|credprobe
    message_snapshot jsonb not null,                 -- last 25 messages
    status           text not null default 'open'
                     check (status in ('open','extended','lifted','blacklisted')),
    created_at       timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 9. blacklisted_phones  (SPEC §9 step 3)
-- ---------------------------------------------------------------------
create table if not exists public.blacklisted_phones (
    phone       text primary key,
    shop_id     uuid references public.shops(id) on delete set null,
    reason      text,
    created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 10. audit_logs  (SPEC §16)
-- ---------------------------------------------------------------------
create table if not exists public.audit_logs (
    id          uuid primary key default gen_random_uuid(),
    shop_id     uuid references public.shops(id) on delete set null,
    actor       text not null,                        -- telegram_id / 'system'
    action      text not null,
    detail      jsonb not null default '{}'::jsonb,
    created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 11. usage_daily  (per-client/shop daily aggregates for billing)  ADR-006
-- Fed from Redis counters by a daily Celery beat job (Stage 10). Hot path
-- touches Redis only (no per-message DB write). RLS by shop_id.
-- ---------------------------------------------------------------------
create table if not exists public.usage_daily (
    id          uuid primary key default gen_random_uuid(),
    client_id   uuid not null references public.clients(id) on delete cascade,
    shop_id     uuid not null references public.shops(id) on delete cascade,
    day         date not null,
    metric      text not null,                        -- customer_msg_in|msg_out|escalation|ai_call|telegram_command
    count       bigint not null default 0,
    unique (client_id, shop_id, day, metric)
);

-- =====================================================================
-- Indexes
-- =====================================================================
create index if not exists idx_shopkeepers_shop     on public.shopkeepers(shop_id);
create index if not exists idx_shopkeepers_telegram on public.shopkeepers(telegram_id);

create index if not exists idx_products_shop        on public.products(shop_id);
create index if not exists idx_products_shop_boost  on public.products(shop_id, boost_level desc);
create index if not exists idx_products_featured    on public.products(shop_id, is_featured) where is_featured;
create index if not exists idx_products_specs_gin   on public.products using gin (specs);    -- JSONB ILIKE search
create index if not exists idx_products_tags_gin    on public.products using gin (tags);

create index if not exists idx_orders_shop          on public.orders(shop_id);
create index if not exists idx_orders_created       on public.orders(shop_id, created_at desc);
create index if not exists idx_orders_product       on public.orders(product_id);
create index if not exists idx_orders_rider         on public.orders(rider_id);

create index if not exists idx_osh_order            on public.order_status_history(order_id);

create index if not exists idx_escalations_shop     on public.pending_escalations(shop_id);
create index if not exists idx_escalations_phone    on public.pending_escalations(phone);

create index if not exists idx_incidents_phone      on public.security_incidents(phone);
create index if not exists idx_incidents_shop       on public.security_incidents(shop_id);

create index if not exists idx_audit_shop           on public.audit_logs(shop_id);
create index if not exists idx_audit_created        on public.audit_logs(created_at desc);

create index if not exists idx_shops_client         on public.shops(client_id);
create index if not exists idx_clients_status       on public.clients(status);

create index if not exists idx_usage_client_day     on public.usage_daily(client_id, day desc);
create index if not exists idx_usage_shop_day       on public.usage_daily(shop_id, day desc);
create index if not exists idx_usage_metric         on public.usage_daily(metric);

-- =====================================================================
-- Row-Level Security  (ADR-003; defense-in-depth by shop_id)
-- The backend uses the SERVICE-ROLE key which bypasses RLS for trusted
-- cross-shop operations (owner reports, health). Per-request tenant context
-- is ALSO enforced in the application layer. The two layers are complementary.
-- =====================================================================
alter table public.shopkeepers          enable row level security;
alter table public.products             enable row level security;
alter table public.delivery_persons     enable row level security;
alter table public.orders               enable row level security;
alter table public.order_status_history enable row level security;
alter table public.pending_escalations  enable row level security;
alter table public.security_incidents   enable row level security;
alter table public.audit_logs           enable row level security;
alter table public.usage_daily          enable row level security;
-- blacklisted_phones is global (owner-managed); RLS optional. Left open.
-- clients is owner-level (no shop_id); no RLS (service-role access only).

-- Policy generator: tenant tables are readable/writable only within their shop_id.
-- (Supabase exposes the request tenant via a custom claim; for the service-role
--  path these policies are bypassed. For anon-key clients we additionally scope
--  by a session variable set per request: set local role 'shopkeeper', shop_id = X.)
create policy "tenant read own shop"  on public.shopkeepers          for select using (true);
create policy "tenant write own shop" on public.shopkeepers          for all    using (true) with check (true);
create policy "tenant read own shop"  on public.products             for select using (true);
create policy "tenant write own shop" on public.products             for all    using (true) with check (true);
create policy "tenant read own shop"  on public.delivery_persons     for select using (true);
create policy "tenant write own shop" on public.delivery_persons     for all    using (true) with check (true);
create policy "tenant read own shop"  on public.orders               for select using (true);
create policy "tenant write own shop" on public.orders               for all    using (true) with check (true);
create policy "tenant read own shop"  on public.order_status_history for select using (true);
create policy "tenant write own shop" on public.order_status_history for all    using (true) with check (true);
create policy "tenant read own shop"  on public.pending_escalations  for select using (true);
create policy "tenant write own shop" on public.pending_escalations  for all    using (true) with check (true);
create policy "tenant read own shop"  on public.security_incidents   for select using (true);
create policy "tenant write own shop" on public.security_incidents   for all    using (true) with check (true);
create policy "tenant read own shop"  on public.audit_logs           for select using (true);
create policy "tenant write own shop" on public.audit_logs           for all    using (true) with check (true);
create policy "tenant read own shop"  on public.usage_daily           for select using (true);
create policy "tenant write own shop" on public.usage_daily           for all    using (true) with check (true);

-- NOTE: The permissive policies above are a starting scaffold. Stage 1 will
-- tighten them to enforce `shop_id = current_setting('app.shop_id')::uuid`
-- once the request-tenant context wiring is in place. The application layer
-- already enforces shop_id scoping on every query (ADR-003, CONVENTIONS).
-- ponytail: RLS policies permissive (using(true)). ceiling: no per-request tenant context at DB layer. upgrade: Stage 1->2 tighten with current_setting('app.shop_id') once wiring lands.

-- =====================================================================
-- Seed data (dev only)
-- =====================================================================
insert into public.clients (name, contact_name, contact_phone, email, status)
values ('Client A — TechStore Group', 'Ahmed', '+971500000001', 'ahmed@techstore.example', 'active')
on conflict do nothing;

-- three shops, the first two belonging to Client A (multi-shop client), the third to Client B
insert into public.shops (client_id, name, whatsapp_number, status)
select id, 'Shop 01 — Dubai Marina', '+10000000001', 'active' from public.clients where name = 'Client A — TechStore Group'
on conflict (whatsapp_number) do nothing;
insert into public.shops (client_id, name, whatsapp_number, status)
select id, 'Shop 02 — Abu Dhabi',    '+10000000002', 'active' from public.clients where name = 'Client A — TechStore Group'
on conflict (whatsapp_number) do nothing;

insert into public.clients (name, contact_name, contact_phone, email, status)
values ('Client B — Sharjah Mobiles', 'Sara', '+971500000002', 'sara@sharjahmobiles.example', 'active')
on conflict do nothing;
insert into public.shops (client_id, name, whatsapp_number, status)
select id, 'Shop 03 — Sharjah', '+10000000003', 'suspended' from public.clients where name = 'Client B — Sharjah Mobiles'
on conflict (whatsapp_number) do nothing;

-- owner shopkeeper on the first shop (is_owner=true; the service provider's account)
insert into public.shopkeepers (shop_id, telegram_id, name, is_owner)
select id, 100000001, 'Owner', true from public.shops where whatsapp_number = '+10000000001'
on conflict (telegram_id) do nothing;

-- sample product with flexible specs + tags + boost
insert into public.products
  (shop_id, category, brand, model, color, condition, specs, cost_price, selling_price, quantity, tags, boost_level, is_featured)
select
  s.id, 'Mobile', 'Samsung', 'Galaxy S25 Ultra 512GB', 'Titanium Black', 'New',
  '{"processor":"Snapdragon 8 Gen 3","battery":"5000mAh","camera":"200MP","storage":"512GB","ram":"12GB"}'::jsonb,
  2800.00, 3400.00, 5,
  array['trending','best_camera','premium','high_margin'], 8, true
from public.shops s where s.whatsapp_number = '+10000000001'
on conflict do nothing;

-- =====================================================================
-- End of 001_init.sql
-- =====================================================================
