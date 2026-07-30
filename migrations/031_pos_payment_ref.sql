-- 031: card payments carry the acquirer's reference (approval code / RRN / terminal receipt no).
--
-- Cash reconciles against the drawer. A card sale reconciles against the acquirer's settlement file,
-- and without the reference printed on the terminal slip there is nothing to match a line to — which
-- is why every till that takes cards captures it at the point of sale. counter_sales already carries
-- `payment_method` (022); this is the other half of it.
--
-- Two deliberate shapes:
--
-- 1. The CHECK is NOT VALID. One card sale predates this column and genuinely has no reference;
--    backfilling a placeholder would invent a payment record. NOT VALID enforces the rule on every
--    new and updated row while leaving that one honest. `VALIDATE CONSTRAINT` once it is resolved.
--
-- 2. The CHECK exempts reversals (quantity < 0). A void copies the original row's tender, so a void
--    of that one legacy card sale would have no reference to copy and the constraint would make it
--    unreversible — the same trap as an invoice that cannot be credited (029). A reversal is traced
--    through the sale it mirrors, so it needs no reference of its own.
--
-- payment_ref is a REFERENCE, never a card number. The write path (actions/pos.ts →
-- lib/types.ts::paymentRefError) rejects anything that looks like a PAN before it reaches here:
-- storing one would be a PCI incident, and a free-text box at a counter is where that happens.

alter table counter_sales add column if not exists payment_ref text;

alter table counter_sales drop constraint if exists counter_sales_card_needs_ref;
alter table counter_sales add constraint counter_sales_card_needs_ref
  check (
    payment_method is distinct from 'card'
    or payment_ref is not null
    or quantity < 0
  ) not valid;

comment on column counter_sales.payment_ref is
  'Acquirer reference for a card payment (approval code / RRN). Never a card number.';
