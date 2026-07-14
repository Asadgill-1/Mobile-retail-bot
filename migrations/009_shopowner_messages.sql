-- 009: shop-owner bot — clients Telegram linking + persistent chat messages.
--
-- clients.telegram_id: a client (shop owner) links their Telegram on the global shop-owner bot by
-- sharing their contact; the bot matches contact_phone and stores their chat id here (mirrors
-- delivery_persons.telegram_id, migration 007). Nullable — a client exists before linking.
-- NOT unique: matching is by normalized phone; one person's number may sit on several client rows.
--
-- messages: every conversation turn (customer / assistant / shopkeeper), persisted permanently at
-- the same choke point that feeds the Redis session (escalations/context.remember). Redis stays the
-- AI's working memory (last 25, 24h TTL); this table is the shop-owner-facing archive. Deletion is
-- a PLATFORM-owner-only action (owner bot 🧹 Messages menu).

alter table public.clients
    add column if not exists telegram_id bigint;

create index if not exists idx_clients_telegram on public.clients(telegram_id);

create table if not exists public.messages (
    id          uuid primary key default gen_random_uuid(),
    shop_id     uuid not null references public.shops(id) on delete cascade,
    identity    text not null,               -- customer key: Telegram user id (testing) / phone (prod)
    role        text not null check (role in ('customer','assistant','shopkeeper')),
    content     text not null,
    created_at  timestamptz not null default now()
);

create index if not exists idx_messages_conv         on public.messages(shop_id, identity, created_at);
create index if not exists idx_messages_shop_created on public.messages(shop_id, created_at desc);

alter table public.messages enable row level security;
create policy "tenant read own shop"  on public.messages for select using (true);
create policy "tenant write own shop" on public.messages for all    using (true) with check (true);
-- ponytail: permissive policies match the repo-wide RLS scaffold (001_init.sql); the app layer
-- scopes every query by shop_id. Tighten with the rest when app.shop_id wiring lands.
