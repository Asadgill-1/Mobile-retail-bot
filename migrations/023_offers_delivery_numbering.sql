-- 023: date-based order/invoice refs + home-delivery charges + shop offers + online-order IMEI link.
--
-- Numbering (Part A): the owner wants ODR-DD-MM-NNN / INV-DD-MM-NNN (NNN = the Nth of that Dubai
--   day, per shop). We KEEP orders.order_number and invoices.invoice_number (the bigint keys humans
--   type into bot commands and FK against) untouched — the date ref is a DISPLAY layer computed from
--   created_at + a per-shop-per-day sequence. daily_counters + next_day_seq() serialize the sequence
--   via a row lock, exactly like next_invoice_number (022) / decrement_stock (003).
--
-- Delivery (Part B): orders.delivery_fee (keeper sets it at confirm; customer sees a breakdown).
--   shops.rider_keeps_delivery decides whether the rider hands delivery cash to the shop (default,
--   flows to shop revenue) or keeps it (COD 'collect' row records product-only; delivery is the
--   rider's earning, derived from delivery_fee at report time — no new ledger entry type).
--
-- Offers (Part C): one active offer per product. free_gift/bogo point at a gift product whose stock
--   decrements on sale; percent_off/amount_off feed discount_amount; free_delivery zeroes the fee.
--   orders.applied_offer snapshots what was applied so the invoice can render the gift as a 0.00 line
--   (orders are single-product — the gift is a snapshot + a stock decrement, never a 2nd order row).
--
-- Online IMEI (Part D): no schema needed — product_units.order_id (022) has waited for this. The
--   invoice-from-order flow flips units sold and sets order_id; the packing sheet gains an IMEI column
--   (code only).

-- 1. per-shop-per-day sequence for order/invoice display refs
alter table public.orders
    add column if not exists day_seq integer;
alter table public.invoices
    add column if not exists day_seq integer;

create table if not exists public.daily_counters (
    shop_id uuid  not null references public.shops(id) on delete cascade,
    kind    text  not null check (kind in ('order','invoice')),
    day     date  not null,                 -- Dubai date the sequence resets on
    last_no integer not null default 0,
    primary key (shop_id, kind, day)
);

-- Row-lock increment (same pattern as next_invoice_number). Returns the Nth ref of the day.
create or replace function public.next_day_seq(p_shop uuid, p_kind text, p_day date)
returns integer
language plpgsql
as $$
declare n integer;
begin
    insert into public.daily_counters (shop_id, kind, day, last_no)
    values (p_shop, p_kind, p_day, 1)
    on conflict (shop_id, kind, day)
        do update set last_no = daily_counters.last_no + 1
    returning last_no into n;
    return n;
end;
$$;

-- 2. home delivery charge + rider-keeps setting
alter table public.orders
    add column if not exists delivery_fee numeric(12,2) not null default 0
        check (delivery_fee >= 0);
alter table public.shops
    add column if not exists rider_keeps_delivery boolean not null default false;

-- 3. offers (one active per product). label = the line the AI/invoice shows the customer.
create table if not exists public.offers (
    id              uuid primary key default gen_random_uuid(),
    shop_id         uuid not null references public.shops(id) on delete cascade,
    product_id      uuid not null references public.products(id) on delete cascade,
    type            text not null check (type in
                        ('free_gift','percent_off','amount_off','free_delivery','bogo','bulk')),
    gift_product_id uuid references public.products(id) on delete set null,  -- free_gift target
    value           numeric(12,2),   -- percent_off:%, amount_off:AED, bulk:min-qty, bogo:buy-N
    label           text not null,
    active          boolean not null default true,
    created_at      timestamptz not null default now()
);
create index if not exists idx_offers_product on public.offers(shop_id, product_id);
-- at most one ACTIVE offer per product (inactive history is allowed)
create unique index if not exists offers_one_active_per_product
    on public.offers(shop_id, product_id) where active;

-- 4. offer snapshot on the order (free gift lands here so the invoice can print a 0.00 line)
alter table public.orders
    add column if not exists applied_offer jsonb;

-- 5. RLS scaffold (repo-wide pattern, 001/008/009/010/022: permissive; the app layer scopes by shop_id)
alter table public.daily_counters enable row level security;
create policy "tenant read own shop"  on public.daily_counters for select using (true);
create policy "tenant write own shop" on public.daily_counters for all    using (true) with check (true);

alter table public.offers enable row level security;
create policy "tenant read own shop"  on public.offers for select using (true);
create policy "tenant write own shop" on public.offers for all    using (true) with check (true);
