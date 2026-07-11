-- =====================================================================
-- 006_rls_lockdown.sql — lock the Supabase data API to the service-role backend (audit Phase 5)
--
-- The whole app is a pure backend that talks to Supabase with the SERVICE-ROLE key, which bypasses
-- RLS. No client ever uses the anon key (there is no browser bundle). The original RLS was a
-- permissive `using(true)` scaffold, and four tables (shops — which holds bot tokens! — clients,
-- price_requests, blacklisted_phones) had RLS off entirely, so a leaked anon key could read/write
-- everything.
--
-- This migration: enable RLS on every public table, revoke anon/authenticated grants, and drop the
-- permissive scaffold policies. RLS-on + no policy = anon/authenticated get nothing. The service-role
-- backend is unaffected (it bypasses RLS and keeps its own grants). Real per-tenant DB enforcement
-- (JWT claim → shop_id) would need a request-tenant context this backend doesn't use; app-layer
-- `shop_id` scoping remains the tenant control, now with the data API sealed behind it.
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
