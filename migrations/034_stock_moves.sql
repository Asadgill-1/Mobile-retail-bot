-- 034: an append-only ledger of every stock movement.
--
-- Today products.quantity is a bare number with no history. Nothing can answer "why is this 3 and
-- not 5", "what did we hold on 30 June" or "how many of these did we ever take in" — and that last
-- one is the denominator sell-through and GMROI need, which is why those reports are impossible
-- rather than merely missing. A miscount, a theft and a sale look identical after the fact.
--
-- Shape: stock_moves is the journal, products.quantity stays the balance. Both are written by the
-- same function in the same transaction, so they cannot disagree. The invariant that holds forever
-- after this migration:
--
--   select count(*) from products p
--    where p.quantity <> coalesce((select sum(delta) from stock_moves m where m.product_id = p.id), 0);
--   -- must return 0
--
-- Sign convention is the ledger's, not the RPC's: delta is the change to quantity, so +5 is stock
-- in and -1 is a sale. That is what makes sum(delta) = quantity work. decrement_stock's `n` is the
-- opposite (it subtracts), and the shim below negates it once so no caller has to think about it.
--
-- Covering every writer without touching a line of app code:
--
--   * decrement_stock is REDEFINED IN PLACE over move_stock — same name, same signature, same
--     boolean return, same negative-n-restocks behaviour, same `quantity >= n` guarantee. All 12
--     call sites (4 in Python through one wrapper, 8 in the dashboard) start writing ledger rows
--     immediately, as reason='legacy'. No lockstep deploy between Python and the dashboards; the
--     app can adopt real reasons at its own pace and `select reason, count(*)` is the progress bar.
--   * the two paths that set quantity outside the RPC are both plain INSERTs on products
--     (actions/products.ts::createProduct, products/service.py::create_product), so one AFTER
--     INSERT trigger catches both.
--
-- product_id is ON DELETE RESTRICT on purpose. CASCADE would erase exactly the history this table
-- exists to keep, and a ledger a delete can rewrite is not a ledger. Consequence, accepted: a
-- product that has held stock can no longer be hard-deleted. Products created at quantity 0 get no
-- opening row and stay deletable, which is the case deleteProduct actually serves (a typo'd entry).
-- actions/products.ts::deleteProduct explains this rather than letting the FK error surface raw.
--
-- ponytail: no cost on the move. Valuation reads products.cost_price, so historic valuation is at
-- TODAY's cost, not the cost at the time. Correct FIFO/weighted-average needs a unit_cost column
-- here and a costing method decision — add it when purchase intake lands (Phase 6), not before.

create table if not exists public.stock_moves (
    id            bigserial primary key,
    shop_id       uuid    not null references public.shops(id)   on delete cascade,
    product_id    uuid    not null references public.products(id) on delete restrict,
    delta         integer not null check (delta <> 0),      -- + in, − out
    balance_after integer not null check (balance_after >= 0),
    reason        text    not null,
    actor         text,                                     -- 'dashboard:{email}' / telegram id
    ref_table     text,                                     -- what caused it, when known
    ref_id        uuid,
    moved_at      timestamptz not null default now()
);
create index if not exists idx_stock_moves_product on public.stock_moves(product_id, moved_at desc);
create index if not exists idx_stock_moves_shop    on public.stock_moves(shop_id, moved_at desc);

-- The one writer. Balance and journal row in a single transaction; the UPDATE's row lock serializes
-- concurrent movers, and RETURNING gives the true post-update balance rather than a re-read that
-- another session could have moved under us.
create or replace function public.move_stock(
    p_id        uuid,
    p_shop      uuid,
    p_delta     integer,
    p_reason    text,
    p_actor     text default null,
    p_ref_table text default null,
    p_ref_id    uuid default null
) returns boolean
language plpgsql
as $$
declare v_balance integer;
begin
    if p_delta = 0 then
        -- Nothing moved, and a zero-delta journal row would be noise. Still answer the question
        -- decrement_stock's boolean has always answered: is this really this shop's product?
        return exists (select 1 from public.products where id = p_id and shop_id = p_shop);
    end if;

    update public.products
       set quantity = quantity + p_delta
     where id = p_id and shop_id = p_shop and quantity + p_delta >= 0
    returning quantity into v_balance;

    if not found then
        return false;  -- unknown product, wrong shop, or it would go negative
    end if;

    insert into public.stock_moves
        (shop_id, product_id, delta, balance_after, reason, actor, ref_table, ref_id)
    values (p_shop, p_id, p_delta, v_balance, p_reason, p_actor, p_ref_table, p_ref_id);

    return true;
end;
$$;

-- Same contract as migration 003's version, now journalled. `n` is the amount to SUBTRACT, so a
-- negative n restocks; `quantity >= n` is `quantity + (-n) >= 0`, the identical guard.
create or replace function public.decrement_stock(p_id uuid, p_shop uuid, n integer)
returns boolean
language plpgsql
as $$
begin
    return public.move_stock(p_id, p_shop, -n, 'legacy');
end;
$$;

-- The two INSERT paths that set quantity without the RPC.
create or replace function public.log_opening_stock()
returns trigger
language plpgsql
as $$
begin
    if coalesce(new.quantity, 0) <> 0 then
        insert into public.stock_moves
            (shop_id, product_id, delta, balance_after, reason, ref_table, ref_id)
        values (new.shop_id, new.id, new.quantity, new.quantity, 'opening', 'products', new.id);
    end if;
    return null;   -- AFTER trigger; the return value is ignored
end;
$$;

drop trigger if exists trg_log_opening_stock on public.products;
create trigger trg_log_opening_stock
    after insert on public.products
    for each row execute function public.log_opening_stock();

-- Opening balances for everything that already exists. Only where quantity <> 0: a product sitting
-- at 0 already satisfies sum(delta) = 0 = quantity with no row, and stays hard-deletable.
insert into public.stock_moves
    (shop_id, product_id, delta, balance_after, reason, ref_table, ref_id, moved_at)
select p.shop_id, p.id, p.quantity, p.quantity, 'opening', 'products', p.id, p.created_at
  from public.products p
 where p.quantity <> 0
   and not exists (select 1 from public.stock_moves m where m.product_id = p.id);

alter table public.stock_moves enable row level security;
revoke all on public.stock_moves from anon, authenticated;
