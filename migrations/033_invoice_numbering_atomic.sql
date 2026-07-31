-- 033: allocate invoice numbers INSIDE the insert, so a failed insert can never burn one.
--
-- The bug: next_invoice_number (022:89) and next_day_seq (023:38) each run in their OWN
-- transaction — a PostgREST /rpc/ round-trip commits on its way out. The app then INSERTs in a
-- SECOND round-trip. Anything that fails in between (a constraint, a network drop, a rejected
-- request, a user closing the tab) leaves the counter advanced with no document behind it, and
-- the number is gone for good.
--
-- Live proof on Shop 01 — Dubai Marina before this migration, BOTH counters affected:
--
--   invoice_number  [1, 2, 5, 6, 7, 8, 9, 10, 11, 12]     -- 3 and 4 burned
--   day_seq for Dubai day 2026-07-29  [2, 3, 4, 5, 6]     -- 1 burned; INV-29-07-001 never printed
--
-- Why it matters beyond tidiness: the FTA requires a sequential per-business numbering series, and
-- an auditor reads a gap as a suppressed sale until you prove otherwise. day_seq is the worse half
-- because day_seq is what actually PRINTS (lib/types.ts::invoiceRef -> INV-DD-MM-NNN).
--
-- The fix is to move both allocations into a BEFORE INSERT trigger. Then they share the insert's
-- transaction: if the insert rolls back, so do the counters, and nothing is consumed. Same row-lock
-- serialization as before (insert ... on conflict do update locks the counter row), so concurrent
-- checkouts still can't collide. The lock is now held until the insert commits instead of being
-- released mid-flight — a window of microseconds, and the correctness it buys is the whole point.
--
-- Assigns only when the column arrives NULL. That keeps the migration safe to apply BEFORE the
-- dashboard deploy that stops calling the RPCs: old code passes an explicit number, the trigger
-- steps aside, behaviour is unchanged. It also leaves a deliberate back-door for a back-fill or an
-- import that has to state its own number.
--
-- Counter order is invoice_counters then daily_counters in every path, so two concurrent inserts
-- can never deadlock by grabbing them in opposite orders.
--
-- ponytail: next_invoice_number is left in place though the app stops calling it — dropping it
-- would break the currently-deployed dashboard during the window between this migration and the
-- deploy. Drop it once the deploy has landed. next_day_seq STAYS regardless: orders still use it
-- for ODR-DD-MM-NNN (orders/service.py:130), which has the same burn but is cosmetic — an order
-- ref is not a tax document.
--
-- Not done here: back-filling 3, 4 and the missing day_seq. Numbers already allocated against no
-- supply are an accounting matter, not a migration's call. The VAT pack gets a Sequence sheet that
-- declares them instead.

create or replace function public.assign_invoice_numbers()
returns trigger
language plpgsql
as $$
begin
    if new.invoice_number is null then
        insert into public.invoice_counters (shop_id, last_no) values (new.shop_id, 1)
        on conflict (shop_id) do update set last_no = invoice_counters.last_no + 1
        returning last_no into new.invoice_number;
    end if;

    if new.day_seq is null then
        -- The Dubai day of the document itself, not of the server clock: issued_at already carries
        -- its default by the time a BEFORE trigger runs, and a caller that back-dates issued_at
        -- must land in that day's sequence. Matches lib/period.ts::dubaiDateISO.
        insert into public.daily_counters (shop_id, kind, day, last_no)
        values (new.shop_id, 'invoice', (new.issued_at at time zone 'Asia/Dubai')::date, 1)
        on conflict (shop_id, kind, day) do update set last_no = daily_counters.last_no + 1
        returning last_no into new.day_seq;
    end if;

    return new;
end;
$$;

drop trigger if exists trg_assign_invoice_numbers on public.invoices;
create trigger trg_assign_invoice_numbers
    before insert on public.invoices
    for each row execute function public.assign_invoice_numbers();
