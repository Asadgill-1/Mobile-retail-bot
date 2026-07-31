-- =====================================================================
-- 032_rls_relockdown.sql — re-seal the data API. 006 did this once; it came undone.
--
-- 006 enabled RLS and revoked anon/authenticated on every table that existed IN JULY. Every
-- migration since that created a table pasted the old permissive scaffold back in:
--
--   010:42-44 counter_sales      022:103-113 product_units, invoices, invoice_counters
--   023:83-89 daily_counters, offers        025:42-47 pipeline_events, redis_ops
--
--   create policy "tenant read own shop"  ... for select using (true);
--   create policy "tenant write own shop" ... for all    using (true) with check (true);
--
-- and Supabase's default privileges granted anon/authenticated on each new table. Permissive
-- policy + grant = open door. Verified against the live project with the public anon key (the one
-- in the shop dashboard's browser bundle) on 2026-07-30 — TWELVE tables were readable:
--
--   invoices, counter_sales, product_units, invoice_counters, daily_counters, offers,
--   cod_ledger, messages, dashboard_users, platform_settings, pipeline_events, redis_ops
--
-- and writable: DELETE on invoices/counter_sales returned 204, UPDATE on invoice_counters
-- returned 204, INSERT on invoices reached the FK constraint. So anyone who loaded the dashboard
-- could read every tenant's customer names, phones, addresses, TRNs, line items and IMEIs, read
-- customer chat transcripts, forge invoices, corrupt the numbering counters, and DELETE tax
-- records the shop is legally required to retain for five years.
--
-- Safe to apply: nothing reads these tables with the anon key. Both dashboards use the
-- service-role client (`lib/db.ts`, marked `server-only`), which bypasses RLS; the anon key is
-- used only by `lib/supabase-auth.ts` for Supabase Auth, which touches the `auth` schema, not
-- `public`. The Python backend uses the service role too.
--
-- Same sweep as 006 rather than a hand-written table list: a list is what rotted last time.
--
-- ponytail: this is a floor, not per-tenant enforcement. Real DB-level tenancy needs a JWT
-- claim -> shop_id context no client here establishes; app-layer shop_id scoping (lib/scope.ts,
-- assertShop) remains the tenant control, now with the data API sealed behind it again.
--
-- AFTER APPLYING, ANY NEW TABLE MUST CARRY:
--   alter table public.<t> enable row level security;
--   revoke all on public.<t> from anon, authenticated;
-- and MUST NOT carry a `using (true)` policy. See docs/05-CONVENTIONS.md.
-- =====================================================================

do $$
declare r record;
begin
  for r in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table public.%I enable row level security', r.tablename);
    execute format('revoke all on public.%I from anon, authenticated', r.tablename);
  end loop;
  for r in select policyname, tablename from pg_policies where schemaname = 'public' loop
    execute format('drop policy if exists %I on public.%I', r.policyname, r.tablename);
  end loop;
end $$;

-- Stop the rot at the source: Supabase's default privileges are what re-granted each new table.
alter default privileges in schema public revoke all on tables from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke all on functions from anon, authenticated;
