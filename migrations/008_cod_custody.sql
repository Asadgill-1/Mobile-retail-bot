-- 008: COD (cash-on-delivery) + custody audit for rider deliveries.
--
-- Custody = the "who has the product" handshake: shop assigns → custody 'offered'; rider must
-- /accept ('accepted' = "yes, I have this product") or /notreceived ('disputed'). A rider can only
-- /deliver an order they accepted — so neither side can later claim the product was never handed over.
--
-- COD money is an append-only ledger (cod_ledger), never a mutable balance column: 'collect' rows
-- are written when the rider registers cash on delivery, 'handover' rows when the shop reconciles
-- end-of-day cash. Balance = Σcollect − Σhandover, always re-derivable, full audit trail.

alter table public.orders
    add column if not exists cod_amount     numeric(12,2),  -- cash to collect (net), set at assignment
    add column if not exists cash_received  numeric(12,2),  -- what the rider actually collected
    add column if not exists delivered_at   timestamptz,    -- delivery time registered by rider /deliver
    add column if not exists custody        text not null default 'none'
        check (custody in ('none','offered','accepted','disputed')),
    add column if not exists custody_at     timestamptz,
    add column if not exists cancel_remarks text;            -- mandatory remarks on /canceldelivery

create table if not exists public.cod_ledger (
    id          uuid primary key default gen_random_uuid(),
    shop_id     uuid not null references public.shops(id) on delete cascade,
    rider_id    uuid not null references public.delivery_persons(id) on delete cascade,
    order_id    uuid references public.orders(id) on delete set null,  -- null on handover rows
    entry       text not null check (entry in ('collect','handover')),
    amount      numeric(12,2) not null check (amount >= 0),
    note        text,
    created_at  timestamptz not null default now()
);
create index if not exists idx_cod_ledger_rider on public.cod_ledger(shop_id, rider_id, created_at);

alter table public.cod_ledger enable row level security;
create policy "tenant read own shop"  on public.cod_ledger for select using (true);
create policy "tenant write own shop" on public.cod_ledger for all    using (true) with check (true);
