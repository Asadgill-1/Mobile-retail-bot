-- 030: POS discounts + VAT charged ON TOP of the saved price.
--
-- products.selling_price is the price BEFORE VAT. Until now the POS treated it as VAT-inclusive
-- (vat = total × 5/105), so a shop that priced ex-VAT was absorbing the 5% out of its own margin.
-- The POS now charges price + 5%; the invoice carries subtotal / VAT / total as it always did, only
-- the arithmetic changed (money.ts::vatOnNet). No column moves for that — it is a code change.
--
-- What DOES need columns is the discount:
--
-- counter_sales.discount_amount — mirrors orders.discount_amount so both channels report the same
--   way: sold_price stays the GROSS per-unit ex-VAT price and the giveaway is recorded beside it.
--   Revenue therefore keeps its meaning (Σ sold_price × quantity) in Python /profit, the shop
--   dashboard and the console alike, and "who discounted what" becomes answerable per sale.
--   NO >= 0 check: a void is a reversing row (022) and carries the NEGATIVE discount, so a voided
--   discount cancels itself out of every sum exactly the way its quantity and price do.
--
-- invoices.discount — display only, always the EX-VAT giveaway, so a printed document reads
--   gross (subtotal + discount) → discount → subtotal → VAT → total on both channels. subtotal
--   stays the taxable amount AFTER discount, which is what VAT is computed on and what every
--   existing report already sums. A credit note (029) negates it with the rest.

alter table public.counter_sales
    add column if not exists discount_amount numeric(12,2) not null default 0;

alter table public.invoices
    add column if not exists discount numeric(12,2) not null default 0;
