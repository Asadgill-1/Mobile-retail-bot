-- 028: per-shop customer channel (Telegram now, WhatsApp at cutover).
--
-- Only the CUSTOMER bot moves. Keeper, shop-owner, rider and platform-owner bots stay on Telegram
-- permanently, so this column governs exactly one bot per shop.
--
-- The platform owns every WhatsApp number: the provider choice, access token and app secret are
-- ONE set of credentials living in platform_settings (024), server-side only, handled like the
-- service-role key. A shop row carries only the routing — which number is his, and whether he has
-- been switched over yet.
--
-- Default 'telegram' is deliberate: applying this migration must change nothing. The cutover is a
-- console action per shop, not a deployment.

alter table public.shops
    add column if not exists customer_channel  text not null default 'telegram',
    add column if not exists whatsapp_phone_id text;

-- Constraint added separately so a re-run on a table that already has the column still gets it.
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'shops_customer_channel_check'
    ) then
        alter table public.shops
            add constraint shops_customer_channel_check
            check (customer_channel in ('telegram', 'whatsapp'));
    end if;
end $$;

-- The provider's id for the shop's number (Meta phone_number_id, or the Twilio sender). Inbound
-- webhooks route on it, so two shops sharing one would cross-deliver customers between tenants.
create unique index if not exists shops_whatsapp_phone_id_key
    on public.shops(whatsapp_phone_id)
    where whatsapp_phone_id is not null;

comment on column public.shops.customer_channel is
    'Which channel this shop''s CUSTOMERS use. Staff bots are always Telegram.';
comment on column public.shops.whatsapp_phone_id is
    'Provider id for the shop''s WhatsApp number; inbound webhooks route on it. Unique per shop.';
