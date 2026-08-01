-- 036: what the shop BUYS, plus the two VAT facts a return needs from the shop itself.
--
-- Everything before this migration is the OUTPUT side: what the shop sold and the 5% it collected.
-- A VAT return has two sides. Without the input side the shop pays over its entire output VAT and
-- recovers nothing — on a phone retailer's margins that is not a rounding error, it is the margin.
--
-- Header only, on purpose. A purchase invoice's LINES are only needed to move stock, and that is
-- migration 037 (phase 6). Everything the VAT201 needs — supplier, their TRN, their invoice number,
-- the date, the net, the input VAT, whether it is recoverable — is on the header. Booking the header
-- takes a shopkeeper fifteen seconds per bill; making them key every line first means the bills do
-- not get booked at all, and an unbooked bill is unrecovered VAT.
--
-- THE ONE CONTROL THAT MATTERS: unique (shop_id, supplier_id, supplier_invoice_no).
--
-- Claiming the same supplier invoice twice is the single most common input-VAT error and the easiest
-- for the FTA to find — they hold the supplier's copy of the same document. It is almost never
-- fraud; it is the same paper bill reaching the shop twice (once by WhatsApp, once in the box) and
-- being booked by two different people. The index makes the second booking impossible rather than
-- detectable, which is the difference between a control and a report. It is case- and
-- whitespace-insensitive: "INV-0091", "inv-0091 " and "INV-0091" are one document, and a UNIQUE over
-- the raw text would happily store all three.
--
-- NOT CHECKED HERE: that vat_amount ≈ subtotal × 5%.
--
-- Real supplier invoices round per line and then total, so they land a fil or two off the whole-bill
-- computation; some are partly exempt or partly zero-rated and are legitimately far off; a reverse-
-- charge import invoice carries no VAT at all. A CHECK constraint here would reject true documents
-- and force whoever is booking them to lie to the form to get the bill in. The dashboard warns at a
-- ±0.02 variance and books it anyway (lib/vat.ts::vatVarianceWarning) — the shop reads the warning
-- and looks at the paper, which is what a human is for.
--
-- Also here, because a return cannot be produced without them:
--   shops.emirate      standard-rated supplies are reported PER EMIRATE (VAT201 boxes 1a–1g). The
--                      figure is the shop's own place of supply, so it is a property of the shop and
--                      nothing can derive it. Left NULL until someone says which one — a default of
--                      'Dubai' would silently file another emirate's numbers under Dubai.
--   shops.vat_period   monthly or quarterly. Sets the default range of phase 5's report, so a shop
--                      is not re-picking dates every time it files.
--   invoices.vat_treatment  default 'standard', which is true of every invoice this system has ever
--                      issued (UAE retail at 5%). The column exists so phase 5's report can split
--                      standard / zero-rated / exempt / reverse-charge instead of assuming.

-- ---------------------------------------------------------------------
-- 1. suppliers — one row per shop per supplier
-- ---------------------------------------------------------------------
create table if not exists public.suppliers (
    id         uuid primary key default gen_random_uuid(),
    shop_id    uuid not null references public.shops(id) on delete cascade,
    name       text not null,
    -- The supplier's TRN. Input VAT is not recoverable without a valid tax invoice, and a tax
    -- invoice without the supplier's TRN is not one. Nullable because cash purchases from
    -- unregistered traders are real — those simply are not recoverable.
    trn        text,
    phone      text,
    created_at timestamptz not null default now()
);

-- Case-insensitive: "Al Noor Trading" and "AL NOOR TRADING" are one supplier, and two rows would
-- split the same account's history in half.
create unique index if not exists suppliers_shop_name_uidx
    on public.suppliers (shop_id, lower(btrim(name)));

-- ---------------------------------------------------------------------
-- 2. purchase_invoices — the bills the shop received (header only)
-- ---------------------------------------------------------------------
create table if not exists public.purchase_invoices (
    id                  uuid primary key default gen_random_uuid(),
    shop_id             uuid not null references public.shops(id) on delete cascade,
    -- restrict, not cascade: deleting a supplier must never silently delete the tax records that
    -- prove the input VAT claimed against them.
    supplier_id         uuid not null references public.suppliers(id) on delete restrict,
    -- THEIR number, exactly as printed on the paper. Never generated here — this document is not
    -- ours, and the FTA matches it against the supplier's own copy.
    supplier_invoice_no text not null,
    -- Snapshot of the supplier's TRN as it appeared on THIS bill. Suppliers register, deregister and
    -- get re-registered; the return has to say what the document said on the day.
    supplier_trn        text,
    -- The date ON the paper (a date, not a timestamp — nobody records the minute a bill was issued).
    -- This, not created_at, decides which tax period the input VAT falls in.
    invoice_date        date not null,
    subtotal            numeric(12, 2) not null,  -- ex-VAT, as printed
    vat_amount          numeric(12, 2) not null default 0,
    -- standard · zero_rated · exempt · reverse_charge · out_of_scope. Same vocabulary as
    -- invoices.vat_treatment below, so phase 5 reads one word list for both sides of the return.
    vat_treatment       text not null default 'standard'
                        check (vat_treatment in ('standard', 'zero_rated', 'exempt',
                                                 'reverse_charge', 'out_of_scope')),
    -- Is this input VAT actually claimable? Entertainment, most motor vehicles and anything bought
    -- for a non-business purpose are blocked by law, and so is any bill without a valid tax invoice.
    -- The shop decides; notes says why.
    recoverable         boolean not null default true,
    -- Object path in the existing private `shop-media` bucket ({shop_id}/purchases/…). The FTA
    -- requires the supplier's tax invoice to be retained for 5 years; a photo of the paper IS the
    -- record when the paper fades or the box floods.
    scan_path           text,
    notes               text,
    created_by          text not null,
    created_at          timestamptz not null default now()
);

-- The anti-double-claim control (see the header). Whitespace- and case-insensitive so the same
-- document typed twice by two people cannot become two claims.
create unique index if not exists purchase_invoices_no_uidx
    on public.purchase_invoices (shop_id, supplier_id, upper(btrim(supplier_invoice_no)));

-- Every view is "this shop, this tax period, newest first".
create index if not exists purchase_invoices_shop_date_idx
    on public.purchase_invoices (shop_id, invoice_date desc);

-- ---------------------------------------------------------------------
-- 3. the shop's own VAT profile
-- ---------------------------------------------------------------------
alter table public.shops
    add column if not exists emirate text,
    -- 'monthly' | 'quarterly'. Quarterly is the FTA default below AED 150m turnover.
    add column if not exists vat_period text not null default 'quarterly',
    -- WHICH quarter cycle, for quarterly filers. The FTA staggers them: a shop's quarters may end
    -- Mar/Jun/Sep/Dec (3), Jan/Apr/Jul/Oct (1) or Feb/May/Aug/Nov (2). Two thirds of quarterly
    -- filers are NOT on calendar quarters, so a report that assumes 3 shows the wrong period to most
    -- of them. Ignored entirely when vat_period = 'monthly'.
    add column if not exists vat_quarter_anchor smallint not null default 3;

do $$
begin
    alter table public.shops add constraint shops_emirate_check
        check (emirate is null or emirate in ('Abu Dhabi', 'Dubai', 'Sharjah', 'Ajman',
                                              'Umm Al Quwain', 'Ras Al Khaimah', 'Fujairah'));
exception when duplicate_object then null;
end $$;

do $$
begin
    alter table public.shops add constraint shops_vat_period_check
        check (vat_period in ('monthly', 'quarterly'));
exception when duplicate_object then null;
end $$;

do $$
begin
    alter table public.shops add constraint shops_vat_quarter_anchor_check
        check (vat_quarter_anchor between 1 and 3);
exception when duplicate_object then null;
end $$;

-- ---------------------------------------------------------------------
-- 4. output-side treatment
-- ---------------------------------------------------------------------
-- ponytail: header-level, not per line. Every invoice this system issues today is one retail supply
-- at one rate, so a document-wide word is exact. A mixed bill (standard phone + zero-rated export
-- accessory on one invoice) would need this on invoices.items instead — same word list, moved down a
-- level, when a shop actually issues one.
alter table public.invoices
    add column if not exists vat_treatment text not null default 'standard';

do $$
begin
    alter table public.invoices add constraint invoices_vat_treatment_check
        check (vat_treatment in ('standard', 'zero_rated', 'exempt',
                                 'reverse_charge', 'out_of_scope'));
exception when duplicate_object then null;
end $$;

-- ---------------------------------------------------------------------
-- 5. seal both new tables (the rule from 032)
-- ---------------------------------------------------------------------
-- RLS on with no policy = the data API answers 401 to anon and authenticated alike; the service role
-- bypasses it and is the only writer. The revokes are belt and braces for the same thing.
alter table public.suppliers enable row level security;
alter table public.purchase_invoices enable row level security;
revoke all on public.suppliers from anon, authenticated;
revoke all on public.purchase_invoices from anon, authenticated;
