-- 007: rider onboarding + Telegram linking.
--
-- Riders are onboarded by the owner (name + phone). They connect their own Telegram to the global
-- rider bot by sharing their contact; the bot matches the phone and stores their chat id here so
-- delivery assignments can be pushed to them. Nullable — a rider exists (and can be assigned) before
-- they have linked Telegram. NOT unique: one person may be a rider for more than one shop (a row
-- per shop), and all their rows share the same telegram_id.

alter table public.delivery_persons
    add column if not exists telegram_id bigint;

create index if not exists idx_delivery_persons_telegram on public.delivery_persons(telegram_id);
