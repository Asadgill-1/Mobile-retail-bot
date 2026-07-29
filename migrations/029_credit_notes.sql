-- 029: tax credit notes — reversing a supply that was already invoiced.
--
-- The gap this closes: voiding a counter sale (022) wrote a reversing negative counter_sales row,
-- so revenue and stock netted out — but the tax invoice it had already produced stayed standing,
-- and every report sums invoices.vat_amount, so output VAT was overstated by every void. Cancelling
-- an order after invoicing had the same hole. Under UAE FTA rules a reversed supply is not an
-- erased invoice: the original document stands and a CREDIT NOTE referencing it reverses the tax.
--
-- Shape follows the same trick 022 used for voids: a credit note is an ordinary invoices row with
-- NEGATIVE amounts and credit_of pointing at what it reverses. Every existing aggregate is a plain
-- sum over invoices, so the reversal nets out with zero changes to reporting code — the same reason
-- counter_sales voids were modelled as negative rows rather than deletes.
--
-- Numbering: a credit note takes the NEXT invoice number in the shop's sequence (FTA wants one
-- unbroken per-business sequence covering both document types), via next_invoice_number (022).
--
-- Note deliberately NOT done: back-filling a credit note for the already-voided live sale
-- (invoice #1 on Shop 01). That is an accounting record — issuing it is the owner's decision, not
-- a migration's.

alter table public.invoices
    -- What this document reverses. NULL on an ordinary invoice.
    add column if not exists credit_of uuid references public.invoices(id) on delete restrict,
    -- Why it was reversed — printed on the note; FTA expects a stated reason.
    add column if not exists reason text;

-- One credit note per invoice: a second reversal would double-refund the VAT.
create unique index if not exists invoices_credit_of_key
    on public.invoices(credit_of) where credit_of is not null;

-- A credit note must carry non-positive money and an ordinary invoice non-negative, so a sum over
-- the table can never be gamed by a sign in the wrong row.
alter table public.invoices
    drop constraint if exists invoices_credit_sign_check;
alter table public.invoices
    add constraint invoices_credit_sign_check check (
        (credit_of is null     and total >= 0 and vat_amount >= 0 and subtotal >= 0)
     or (credit_of is not null and total <= 0 and vat_amount <= 0 and subtotal <= 0)
    );
