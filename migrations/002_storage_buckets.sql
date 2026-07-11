-- =====================================================================
-- 002_storage_buckets.sql — Storage buckets for product media (SPEC §4 step 10)
-- Source: docs/SPEC-source.md §4. Bucket name matches settings.supabase_storage_bucket.
--
-- Private bucket: the backend uploads with the service-role key (bypasses RLS),
-- and customer-facing links are time-limited signed URLs — never public objects.
-- `shop-reports` (SPEC §10 Excel export) is created in Stage 9, when something writes to it.
-- =====================================================================

insert into storage.buckets (id, name, public)
values ('shop-media', 'shop-media', false)
on conflict (id) do nothing;
