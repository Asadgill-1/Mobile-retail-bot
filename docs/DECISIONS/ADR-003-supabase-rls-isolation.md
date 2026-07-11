# ADR-003 — Supabase RLS for tenant isolation

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** owner
- **Stage when decided:** 0

## Context

The system serves 30 shops from a single deployment with "complete tenant isolation" (SPEC §1). A bug in application-level `shop_id` filtering alone is unacceptable — a single missed `WHERE shop_id = ?` leaks cross-shop data (also an attack pattern, §7). We need defense-in-depth.

## Options considered

### Option A — Application-level `shop_id` filtering only
- Pros: simple.
- Cons: one missed query = leak; not defense-in-depth; fails the "never leak data" goal.

### Option B — Separate database/schema per shop
- Pros: physical isolation.
- Cons: 30 schemas → operational complexity; cross-shop owner reports hard; Supabase Storage bucketing awkward.

### Option C — Single schema + Postgres Row-Level Security (RLS) by `shop_id`, plus app-level filtering
- Pros: defense-in-depth; DB enforces scoping even if app forgets; owner cross-shop reports via service-role bypass; clean with Supabase.
- Cons: RLS policy maintenance; must use service-role key carefully.

## Decision

Adopt **Option C** — enable Supabase/Postgres **Row-Level Security** on all tenant tables, with policies scoping by `shop_id`. The backend uses the **service-role key** (RLS bypass) for trusted cross-shop operations (owner reports, health); per-request tenant context is also enforced in the application layer. The two layers are complementary.

## Rationale

"Never leak data" is a primary goal (§1, §7). RLS gives defense-in-depth without 30 schemas. Supabase supports RLS natively. Application-level filtering remains for correctness and to set context.

## Consequences

- Positive: a missed app filter does not leak data; clear ownership of tenant scoping.
- Negative: every tenant table needs RLS policies (maintained in `migrations/`); service-role key must be guarded (never exposed to clients).
- Follow-ups: write RLS in `migrations/001_init.sql`; add tests asserting cross-shop queries return nothing.

## Related

- ADR-001 (stack), `migrations/001_init.sql`, `05-CONVENTIONS.md` (no cross-module DB access).
- SPEC §1, §7.
