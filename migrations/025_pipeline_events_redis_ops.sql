-- 025: pipeline_events (the "no message is silently lost" log) + redis_ops (console → Redis outbox).
--
-- pipeline_events: today only the `attack` outcome persists (as security_incidents). Every other
-- non-AI pipeline outcome — suspended / blacklisted / quarantined / frozen / bypass / too_long /
-- rate_capped / locked / duplicate — is log-only, so the platform owner cannot answer "why did this
-- customer never get a reply?". One fire-and-forget insert in messaging/pipeline.py fixes that.
-- Deliberately NOT storing message text here: incidents already snapshot content for attacks, and
-- this table exists to count/route, not to duplicate the message archive.
--
-- redis_ops: the console runs on Vercel and has NO Redis access (Redis is on the owner's PC).
-- Rather than exposing the PC through a tunnel (the old bridge design — dropped in Stage 12g),
-- the console INSERTs an intent row here and the existing 60s health beat drains it through the
-- real security/service.py functions. Append-only + stamped: applied_at proves it ran, error
-- captures why it didn't. Propagation is "within a minute" — the owner bot stays the instant path.

create table if not exists public.pipeline_events (
    id         uuid primary key default gen_random_uuid(),
    shop_id    uuid references public.shops(id) on delete set null,
    identity   text not null,
    action     text not null,   -- pipeline outcome: suspended|blacklisted|quarantined|frozen|bypass|attack|too_long|rate_capped|locked|duplicate
    created_at timestamptz not null default now()
);
create index if not exists idx_pipeline_events_action on public.pipeline_events(action, created_at desc);
create index if not exists idx_pipeline_events_shop   on public.pipeline_events(shop_id, created_at desc);

create table if not exists public.redis_ops (
    id         uuid primary key default gen_random_uuid(),
    op         text not null check (op in (
                   'quarantine_lift','quarantine_extend','blacklist',
                   'bypass_set','bypass_remove','forward_to_shop','llm_test')),
    args       jsonb not null default '{}'::jsonb,   -- {identity, shop_id?, reason?}
    requested_by text,                               -- "dashboard:{email}"
    created_at timestamptz not null default now(),
    applied_at timestamptz,                          -- set by the beat when the op actually ran
    error      text,                                 -- set instead when it failed
    result     jsonb                                 -- llm_test: {ok, model, latency_ms, sample}
);
-- the drain reads the pending queue: unapplied, no error, oldest first
create index if not exists idx_redis_ops_pending on public.redis_ops(created_at)
    where applied_at is null and error is null;

alter table public.pipeline_events enable row level security;
create policy "tenant read own shop"  on public.pipeline_events for select using (true);
create policy "tenant write own shop" on public.pipeline_events for all    using (true) with check (true);

-- redis_ops is platform-owner only: service-role bypasses RLS, no policies for anyone else.
alter table public.redis_ops enable row level security;
