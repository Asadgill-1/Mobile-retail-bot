-- 022: dashboard POS + UAE tax invoices + IMEI unit tracking.
--
-- POS (dashboard repo) sells over the counter into the EXISTING counter_sales table (010) so the
-- bots' reports (/profit merge_counter, counter sheet flow) see web sales with zero Python changes.
--
-- counter_sales changes:
--   quantity != 0 : a void is a REVERSING negative row (append-only, never delete/update a sale);
--                   merge_counter/counter_totals are plain sums, so reversals net out for free.
--   recorded_by default 0 : bot rows carry the owner's telegram_id; the dashboard has none.
--   sold_by       : dashboard actor "dashboard:{email}" (bot rows stay null).
--   payment_method: cash|card from the POS screen (bot photo-flow rows stay null — the paper
--                   sheet never said; enforced only where the dashboard writes).
--
-- product_units: light IMEI serialization for phones. products.quantity REMAINS the stock source
-- of truth (bots know nothing about units); units are a parallel ledger for warranty/receipts and
-- theft control. unique(shop_id, imei) — the same IMEI can't sit in a shop's stock twice.
--
-- invoices: UAE FTA tax invoices (retail prices VAT-inclusive, vat = total * 5/105).
--   Numbering is PER SHOP and sequential (FTA wants a per-business sequence; one global identity
--   would leak cross-tenant gaps). invoice_counters + next_invoice_number(p_shop) serialize via
--   the counter row lock — same precedent as decrement_stock (003).

-- 1. barcode lookup for POS scanning (box EAN/UPC)
alter table public.products
    add column if not exists barcode text;
create index if not exists idx_products_barcode on public.products(shop_id, barcode);

-- 2. counter_sales: void rows + dashboard writer fields
alter table public.counter_sales
    drop constraint if exists counter_sales_quantity_check;
alter table public.counter_sales
    add constraint counter_sales_quantity_check check (quantity != 0);
alter table public.counter_sales
    alter column recorded_by set default 0;
alter table public.counter_sales
    add column if not exists sold_by text,
    add column if not exists payment_method text
        check (payment_method is null or payment_method in ('cash','card'));

-- 3. IMEI unit ledger
create table if not exists public.product_units (
    id              uuid primary key default gen_random_uuid(),
    shop_id         uuid not null references public.shops(id) on delete cascade,
    product_id      uuid not null references public.products(id) on delete cascade,
    imei            text not null,
    status          text not null default 'in_stock' check (status in ('in_stock','sold')),
    counter_sale_id uuid references public.counter_sales(id),
    order_id        uuid references public.orders(id),
    added_at        timestamptz not null default now(),
    sold_at         timestamptz,
    unique (shop_id, imei)
);
create index if not exists idx_product_units_product on public.product_units(product_id, status);

-- 4. invoice identity of the shop (printed on every tax invoice)
alter table public.shops
    add column if not exists trn text,
    add column if not exists invoice_name text,
    add column if not exists invoice_address text;

-- 5. tax invoices + per-shop sequential numbering
create table if not exists public.invoices (
    id               uuid primary key default gen_random_uuid(),
    shop_id          uuid not null references public.shops(id) on delete cascade,
    invoice_number   bigint not null,
    source           text not null check (source in ('order','counter')),
    order_id         uuid references public.orders(id),
    counter_sale_ids uuid[],
    customer_name    text,
    customer_phone   text,
    customer_address text,          -- required in-app when total > 10,000 AED (full tax invoice)
    customer_trn     text,          -- B2B buyer TRN (input-VAT recovery needs it)
    items            jsonb not null, -- [{desc, qty, unit_price, line_total, imeis[]}] snapshot
    subtotal         numeric(12,2) not null,
    vat_rate         numeric(5,2) not null default 5,
    vat_amount       numeric(12,2) not null,
    total            numeric(12,2) not null,
    issued_at        timestamptz not null default now(),
    created_by       text not null,
    unique (shop_id, invoice_number)
);
create index if not exists idx_invoices_shop_issued on public.invoices(shop_id, issued_at desc);

create table if not exists public.invoice_counters (
    shop_id uuid primary key references public.shops(id) on delete cascade,
    last_no bigint not null default 0
);

create or replace function public.next_invoice_number(p_shop uuid)
returns bigint
language plpgsql
as $$
declare n bigint;
begin
    insert into public.invoice_counters (shop_id, last_no) values (p_shop, 1)
    on conflict (shop_id) do update set last_no = invoice_counters.last_no + 1
    returning last_no into n;
    return n;
end;
$$;

-- 6. RLS scaffold (repo-wide pattern, 001/008/009/010: permissive; the app layer scopes by shop_id)
alter table public.product_units    enable row level security;
create policy "tenant read own shop"  on public.product_units for select using (true);
create policy "tenant write own shop" on public.product_units for all    using (true) with check (true);

alter table public.invoices         enable row level security;
create policy "tenant read own shop"  on public.invoices for select using (true);
create policy "tenant write own shop" on public.invoices for all    using (true) with check (true);

alter table public.invoice_counters enable row level security;
create policy "tenant read own shop"  on public.invoice_counters for select using (true);
create policy "tenant write own shop" on public.invoice_counters for all    using (true) with check (true);
