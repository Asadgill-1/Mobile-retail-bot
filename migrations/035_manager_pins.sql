-- 035: a manager PIN, and a log of everything it was asked to approve.
--
-- The role matrix built in phase 1d (mobile-shop-and-shop-owner-dashboard/ROLE-MATRIX.md) measured
-- this live under both logins: role is consulted in exactly five files, and EVERY destructive action
-- is identical for a keeper and the shop owner. A keeper can hard-delete a product, edit cost_price,
-- void any sale, cancel any order, sell a 4,000 AED phone for 1 AED, and edit the shop's TRN — which
-- prints on every tax invoice and which migration 033's guard now requires for checkout to work at
-- all. There is no ceiling on any of it and nothing that records that it happened without an owner
-- reading the activity feed line by line.
--
-- The answer is NOT owner-only locks. A keeper who cannot discount cannot serve a haggling customer,
-- and a till that needs the owner on the phone stops being a till. What a retail POS actually does is
-- let the ordinary case through and put a manager's PIN in front of the loosening direction only —
-- deeper discount, lower price, bigger void — never in front of charging list price.
--
-- Two tables:
--
--   manager_pins       one row per SHOP, not per login. The PIN is the shop's, the same way the safe
--                      combination is. It records WHICH LOGIN used it, not which human — that is the
--                      honest ceiling, and the upgrade path is a composite (shop_id, user_id) key
--                      with the same helper. Also carries the lockout counter, because the dashboards
--                      have no Redis and an in-memory counter resets on every Vercel cold start.
--   override_approvals every over-threshold attempt, whatever the outcome. This is the separate
--                      approvals log the owner asked for; it surfaces as a 4th view on the existing
--                      owner-gated /logs, so it costs zero new routes and zero new auth surface.
--
-- Hashing is deliberately NOT pgcrypto. crypt('123456', ...) travels inside the SQL body, so the PIN
-- lands in the Postgres query log, in Supabase's request log, and in any pooler in between. It is
-- hashed in Node with crypto.scrypt (lib/override.ts) and only the digest is ever sent here. The
-- per-row salt lives in pin_salt; the pepper lives in the OVERRIDE_PEPPER env var and never touches
-- this database — 6 digits is 10^6, which a leaked table alone would fall to in seconds, and the
-- pepper is the whole reason a DB-only leak does not.
--
-- FAILS OPEN, LOUDLY. A shop with no manager_pins row is not blocked from anything: the till keeps
-- working exactly as it does today. But every action that WOULD have needed approval still writes an
-- override_approvals row with outcome='unset', so the owner's first look at the new view is a list of
-- what has been happening unsupervised, priced. That is a far better argument for setting a PIN than
-- a locked screen, and it means this migration cannot break a live shop on the day it lands.
--
-- ponytail: thresholds are constants in lib/override.ts, not rows here. manager_pins.limits holds
-- only per-shop DELTAS from those defaults, so a shop that sells 30k phones can raise its discount
-- ceiling without a deploy while every other shop keeps the sane default. No UI writes limits yet —
-- it is set by SQL until a shop asks for it.

create table if not exists public.manager_pins (
    shop_id      uuid primary key references public.shops(id) on delete cascade,
    -- scrypt(pin + pepper, pin_salt) as hex. Never the PIN, never anything reversible.
    pin_hash     text not null,
    pin_salt     text not null,
    -- Per-shop overrides of the defaults in lib/override.ts, e.g. {"discount_aed": 500}.
    limits       jsonb not null default '{}'::jsonb,
    -- Lockout state. Checked BEFORE the 64ms scrypt call, so a wrong-PIN storm cannot be used to
    -- pin the Node event loop.
    fail_count   integer not null default 0,
    locked_until timestamptz,
    set_by       text,
    updated_at   timestamptz not null default now()
);

create table if not exists public.override_approvals (
    id         bigserial primary key,
    shop_id    uuid not null references public.shops(id) on delete cascade,
    -- What was being attempted: discount · unit_price · void · cancel · stock_adjust ·
    -- product_delete · cost_edit · price_cut · trn. Free text on purpose — a CHECK here would mean a
    -- migration every time a new action grows a ceiling, and the app is the only writer.
    kind       text not null,
    -- approved: a correct PIN was given.  refused: a wrong one.  locked: too many wrong ones.
    -- unset:   the shop has no PIN, so the action went through unapproved and this row is the record.
    outcome    text not null check (outcome in ('approved', 'refused', 'locked', 'unset')),
    -- The number that tripped the threshold and the threshold it crossed. Units follow `kind`: AED
    -- for money, whole units for stock_adjust, percent for price_cut. Both nullable because
    -- always-PIN kinds (product_delete, cost_edit, trn) have no number to compare.
    amount     numeric(12, 2),
    threshold  numeric(12, 2),
    -- One human line, already formatted: "Apple iPhone 16 at 1.00 — below cost 3800.00".
    detail     text,
    actor      text not null,
    ref_table  text,
    ref_id     uuid,
    created_at timestamptz not null default now()
);

-- The view is always "this shop, newest first, inside a period".
create index if not exists override_approvals_shop_time_idx
    on public.override_approvals (shop_id, created_at desc);

alter table public.manager_pins enable row level security;
alter table public.override_approvals enable row level security;
revoke all on public.manager_pins from anon, authenticated;
revoke all on public.override_approvals from anon, authenticated;
revoke all on sequence public.override_approvals_id_seq from anon, authenticated;
