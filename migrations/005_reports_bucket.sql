-- =====================================================================
-- 005_reports_bucket.sql — Storage bucket for Excel exports (SPEC §10)
-- Source: docs/SPEC-source.md §10. Bucket name matches settings.supabase_reports_bucket.
--
-- Private, like `shop-media`: the backend uploads with the service-role key and
-- hands the shopkeeper a 24h signed URL — the .xlsx is never a public object.
-- Deferred from 002 until Stage 9, when the export actually writes here.
-- =====================================================================

insert into storage.buckets (id, name, public)
values ('shop-reports', 'shop-reports', false)
on conflict (id) do nothing;
