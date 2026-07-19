-- 020: dashboard auth mapping (Shop & Shop-Owner Dashboard, PLAN §3.2).
--
-- Supabase Auth (email/password) holds the credentials; this table maps each auth
-- user to a dashboard role and its tenant scope. No self-signup — rows are created
-- only by the platform owner (Owner Console; seeded manually until it exists).
-- keeper  → shop_id set (one shop's staff view)
-- owner   → client_id set (all shops of that client)

create table if not exists public.dashboard_users (
    user_id    uuid primary key references auth.users(id) on delete cascade,
    role       text not null check (role in ('keeper','owner')),
    shop_id    uuid references public.shops(id),     -- set when role='keeper'
    client_id  uuid references public.clients(id),   -- set when role='owner'
    created_at timestamptz not null default now()
);

alter table public.dashboard_users enable row level security;
-- No policies: only the service-role key (dashboard server) reads this table.
-- ponytail: same permissive-RLS posture as the rest of the schema (001); the
-- dashboard server scopes every query by the shop_ids this row resolves to.
