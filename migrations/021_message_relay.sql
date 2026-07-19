-- 021: dashboard → AI session relay.
--
-- The web dashboard (separate repo, Vercel) cannot reach the backend's local Redis, so its
-- customer sends landed only in the permanent `messages` archive — the AI's last-25 session
-- never saw them. Fix: dashboard inserts archive rows flagged relay_pending; the next AI turn
-- (escalations/context.sync_relay) pushes them into the Redis session and clears the flag.
-- Bot-written rows keep the default false and are never touched.

alter table public.messages
    add column if not exists relay_pending boolean not null default false;

-- Partial index: the drain query runs on every customer message; pending rows are near-zero.
create index if not exists idx_messages_relay_pending
    on public.messages (shop_id, identity, created_at) where relay_pending;
