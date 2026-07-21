-- 026: soft-delete columns the platform-owner console needs for offboarding.
--
-- Hard deletes are forbidden across this schema: orders reference products, riders and shops with
-- `on delete restrict` / `set null`, so a real DELETE either fails or silently orphans history the
-- owner is legally keeping (invoices, COD ledger, audit trail). Offboarding is therefore a STATUS
-- change everywhere:
--   client "deleted"  → clients.status = 'offboarded'  (enum already exists, migration 001)
--   shop   "deleted"  → shops.status   = 'archived'    (NEW here; hidden from every list + bot builder)
--   rider  "deleted"  → delivery_persons.active = false (NEW here; assigned orders keep their rider)
-- Shopkeepers are the one safe hard delete (nothing references them) and stay a real DELETE.

alter table public.delivery_persons
    add column if not exists active boolean not null default true;

-- shops.status gains 'archived' (was: active | suspended, inline check from migration 001)
alter table public.shops drop constraint if exists shops_status_check;
alter table public.shops add constraint shops_status_check
    check (status in ('active','suspended','archived'));
