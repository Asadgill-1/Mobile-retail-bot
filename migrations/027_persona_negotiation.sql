-- 027: make the customer chat sound like a person, and let it bargain within limits.
--
-- Persona (Part A): the assistant answers as a named person at the shop. `assistant_gender` is
--   not decoration — Hindi, Urdu and Arabic conjugate verbs by the SPEAKER's gender, and most of
--   this customer base writes in romanized Hindi/Urdu. A "Sara" writing masculine verb forms is
--   an instant tell. `assistant_style` is a short free-text vibe note the shop writes.
--   All three are nullable: a shop that sets nothing keeps today's behaviour (speak as the shop).
--
-- Bargaining (Part C): today EVERY haggle pings a shopkeeper for approval. At peak that floods
--   the shop and leaves the customer in dead air. `haggle_ask_every_time` defaults to TRUE, so
--   this migration changes no shop's behaviour until it is switched off deliberately. When off,
--   the AI may settle at or above a floor computed server-side as
--       max(products.min_price, selling_price * (1 - ai_max_discount_pct/100), cost_price)
--   The cost_price term is an absolute clamp: no combination of settings can sell at a loss.
--   `min_price` is optional per product (set in /addproduct or the dashboard, editable any time).
--
-- Offers (Part D): shops bargain with freebies, not only with price ("no more discount, but I'll
--   put a cover in the box"). `reveal='on_haggle'` holds an offer back until the customer pushes
--   on price; 'always' is the existing advertise-on-sight behaviour. The one-active-per-product
--   unique index is relaxed to one per (product, reveal) so a product can carry one advertised
--   offer AND one bargaining chip at the same time.

-- 1. persona + bargaining authority, per shop
alter table public.shops
    add column if not exists assistant_name         text,
    add column if not exists assistant_gender       text,
    add column if not exists assistant_style        text,
    add column if not exists haggle_ask_every_time  boolean not null default true,
    add column if not exists ai_max_discount_pct    numeric(5,2) not null default 0;

alter table public.shops drop constraint if exists shops_assistant_gender_check;
alter table public.shops add constraint shops_assistant_gender_check
    check (assistant_gender is null or assistant_gender in ('female','male'));

-- A percentage outside 0-100 is a data-entry slip that would compute a nonsense floor.
alter table public.shops drop constraint if exists shops_ai_max_discount_pct_check;
alter table public.shops add constraint shops_ai_max_discount_pct_check
    check (ai_max_discount_pct >= 0 and ai_max_discount_pct <= 100);

-- 2. optional per-product price floor.
--    History: migration 004 line 25 DROPPED products.min_price ("floor replaced by explicit
--    approval", ADR-010 rev.). This is not an accidental revert of that decision — explicit
--    approval REMAINS the default (haggle_ask_every_time defaults true). The floor only has any
--    effect for a shop that deliberately turns asking off, and it is clamped by cost_price.
--    When set, it is THE floor for that product (an explicit per-product number beats the
--    shop-wide percentage); when null, the percentage applies.
alter table public.products
    add column if not exists min_price numeric(12,2);

alter table public.products drop constraint if exists products_min_price_check;
alter table public.products add constraint products_min_price_check
    check (min_price is null or min_price > 0);

-- 3. offers: advertise on sight, or hold back as a bargaining chip
alter table public.offers
    add column if not exists reveal text not null default 'always';

alter table public.offers drop constraint if exists offers_reveal_check;
alter table public.offers add constraint offers_reveal_check
    check (reveal in ('always','on_haggle'));

-- one ACTIVE offer per product PER REVEAL MODE (was: one active per product, migration 023)
drop index if exists public.offers_one_active_per_product;
create unique index if not exists offers_one_active_per_product_reveal
    on public.offers(shop_id, product_id, reveal) where active;
