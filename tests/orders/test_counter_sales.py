"""Counter sales: vision extraction (fragile), stock writes, discrepancy flag, profit fold.

The model's output is untrusted input — parse_extraction is where that gets contained, so it is
tested hardest. No model and no DB are involved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.orders.counter_sales import parse_extraction, record_sales
from app.orders.models import ProfitSummary
from app.orders.service import merge_counter


# --- parse_extraction: whatever the model returns, we never crash and never invent a sale ---
def test_parse_extraction_plain_json():
    rows = parse_extraction('[{"code": "PR0001", "qty": 2, "price": 3400}]')
    assert rows == [{"code": "PR0001", "qty": 2, "price": Decimal("3400")}]


def test_parse_extraction_strips_markdown_fences():
    # Models fence JSON despite being told not to.
    rows = parse_extraction('```json\n[{"code": "PR0001", "qty": 1, "price": 100}]\n```')
    assert rows[0]["code"] == "PR0001"


def test_parse_extraction_null_price_means_use_list_price():
    rows = parse_extraction('[{"code": "PR0001", "qty": 1, "price": null}]')
    assert rows[0]["price"] is None


def test_parse_extraction_empty_sheet_is_empty_list_not_an_error():
    assert parse_extraction("[]") == []


@pytest.mark.parametrize("bad", ["", "   ", "I could not read this sheet", "{}", "null", "[1,2]"])
def test_parse_extraction_rejects_unreadable_output(bad):
    if bad in ("{}", "null"):
        with pytest.raises(ValueError):
            parse_extraction(bad)
        return
    if bad == "[1,2]":
        assert parse_extraction(bad) == []  # a list of junk yields no sales, not a crash
        return
    with pytest.raises(ValueError) as e:
        parse_extraction(bad)
    assert "clearer" in str(e.value)  # the message tells the owner what to do


@pytest.mark.parametrize("row", [
    '{"code": "", "qty": 1}',            # no code
    '{"qty": 1}',                        # no code key
    '{"code": "PR1", "qty": 0}',         # zero sold
    '{"code": "PR1", "qty": -3}',        # negative
    '{"code": "PR1", "qty": "two"}',     # unreadable qty
    '{"code": "PR1"}',                   # no qty at all
    '"just a string"',                   # not even a row
])
def test_parse_extraction_drops_unusable_rows(row):
    assert parse_extraction(f"[{row}]") == []


def test_parse_extraction_bad_price_falls_back_to_list_price():
    rows = parse_extraction('[{"code": "PR1", "qty": 1, "price": "abc"}]')
    assert rows[0]["price"] is None
    rows = parse_extraction('[{"code": "PR1", "qty": 1, "price": -5}]')
    assert rows[0]["price"] is None  # a negative price is not a price


def test_parse_extraction_keeps_good_rows_beside_bad_ones():
    rows = parse_extraction('[{"code": "PR1", "qty": 2}, {"qty": 9}, {"code": "PR2", "qty": 1}]')
    assert [r["code"] for r in rows] == ["PR1", "PR2"]


# --- record_sales ---
class _InsertSB:
    def __init__(self):
        self.inserts = []

    def table(self, name):
        self._t = name
        return self

    def insert(self, row):
        self.inserts.append((self._t, row))
        return self

    def execute(self):
        class _R:
            data = [{}]
        return _R()


def _product(number=1, qty=5, price="3400"):
    from app.products.models import Product

    return Product(id=uuid4(), shop_id=uuid4(), category="Mobile", brand="Samsung", model="S23",
                   condition="New", cost_price=Decimal("2800"), selling_price=Decimal(price),
                   quantity=qty, product_number=number)


@pytest.fixture
def sale_wire(monkeypatch):
    cap = {"decrements": [], "stock_ok": True, "low_stock": [], "products": {}}

    async def _by_ref(shop_id, ref, client=None):
        from app.products.service import ProductNotFound

        if ref not in cap["products"]:
            raise ProductNotFound(ref)
        return cap["products"][ref]

    async def _dec(shop_id, pid, qty, client=None):
        cap["decrements"].append((str(pid), qty))
        return cap["stock_ok"]

    async def _low(shop, pid, client=None):
        cap["low_stock"].append(str(pid))
        return True

    monkeypatch.setattr("app.orders.counter_sales.get_product_by_ref", _by_ref)
    monkeypatch.setattr("app.orders.counter_sales._decrement_stock", _dec)
    monkeypatch.setattr("app.orders.counter_sales.notify_low_stock", _low)
    return cap


def _shop():
    return SimpleNamespace(id=uuid4(), name="Shop 01", client_id=uuid4())


@pytest.mark.asyncio
async def test_record_sales_decrements_stock_and_writes_row(sale_wire):
    p = _product()
    sale_wire["products"]["PR0001"] = p
    sb = _InsertSB()

    res = await record_sales(_shop(), [{"code": "PR0001", "qty": 2, "price": Decimal("3400")}],
                             photo_path="proof.jpg", recorded_by=555, client=sb)

    assert sale_wire["decrements"] == [(str(p.id), 2)]
    table, row = sb.inserts[0]
    assert table == "counter_sales"
    assert row["quantity"] == 2 and row["sold_price"] == "3400"  # PER UNIT, not the line total
    assert row["discrepancy"] is False and row["photo_path"] == "proof.jpg"
    assert row["recorded_by"] == 555
    assert res["total"] == Decimal("6800")  # 3400 × 2
    assert sale_wire["low_stock"] == [str(p.id)]  # stock went down → same hook as confirm_order


@pytest.mark.asyncio
async def test_record_sales_without_price_uses_the_list_price(sale_wire):
    sale_wire["products"]["PR0001"] = _product(price="3400")
    sb = _InsertSB()
    res = await record_sales(_shop(), [{"code": "PR0001", "qty": 1, "price": None}],
                             photo_path=None, recorded_by=1, client=sb)
    assert sb.inserts[0][1]["sold_price"] == "3400"
    assert res["total"] == Decimal("3400")


@pytest.mark.asyncio
async def test_record_sales_flags_discrepancy_and_still_records(sale_wire):
    # The sheet says it sold; stock says it couldn't have. That contradiction is the point —
    # it must be stored, not dropped, and it must not count as revenue.
    sale_wire["products"]["PR0001"] = _product(qty=1)
    sale_wire["stock_ok"] = False
    sb = _InsertSB()

    res = await record_sales(_shop(), [{"code": "PR0001", "qty": 5, "price": Decimal("3400")}],
                             photo_path=None, recorded_by=1, client=sb)

    assert sb.inserts[0][1]["discrepancy"] is True  # durably flagged
    assert res["saved"] == [] and len(res["discrepancies"]) == 1
    assert res["total"] == Decimal("0")             # never counted as takings
    assert sale_wire["low_stock"] == []             # no decrement → no low-stock alert


@pytest.mark.asyncio
async def test_record_sales_skips_unknown_codes_without_touching_stock(sale_wire):
    sb = _InsertSB()
    res = await record_sales(_shop(), [{"code": "PR9999", "qty": 1, "price": None}],
                             photo_path=None, recorded_by=1, client=sb)
    assert res["unknown"] == ["PR9999"]
    assert sb.inserts == [] and sale_wire["decrements"] == []


@pytest.mark.asyncio
async def test_record_sales_mixed_batch(sale_wire):
    sale_wire["products"]["PR0001"] = _product(number=1)
    sb = _InsertSB()
    res = await record_sales(
        _shop(),
        [{"code": "PR0001", "qty": 1, "price": Decimal("100")},
         {"code": "PR0404", "qty": 1, "price": None}],
        photo_path=None, recorded_by=1, client=sb,
    )
    assert len(res["saved"]) == 1 and res["unknown"] == ["PR0404"]
    assert res["total"] == Decimal("100")


# --- merge_counter: the money fold ---
def _crow(qty, unit, cost, brand="Samsung", model="S23"):
    return {"quantity": qty, "sold_price": str(unit),
            "products": {"cost_price": str(cost), "brand": brand, "model": model}}


def test_merge_counter_adds_revenue_cost_and_profit():
    base = ProfitSummary(orders=1, revenue=Decimal("2499"), cost=Decimal("2000"),
                         profit=Decimal("499"))
    out = merge_counter(base, [_crow(2, 3400, 2800)])  # 6800 revenue, 5600 cost, 1200 profit

    assert out.orders == 2
    assert out.revenue == Decimal("9299") and out.cost == Decimal("7600")
    assert out.profit == Decimal("1699")
    assert out.counter_revenue == Decimal("6800") and out.counter_profit == Decimal("1200")


def test_merge_counter_no_rows_returns_the_summary_untouched():
    base = ProfitSummary(orders=1, revenue=Decimal("100"))
    assert merge_counter(base, []) is base


def test_merge_counter_merges_top_products_across_both_channels():
    from app.orders.models import ProfitLine

    base = ProfitSummary(top=[ProfitLine("Samsung S23", 1, Decimal("500"))])
    out = merge_counter(base, [_crow(2, 3400, 2800)])  # same product, +1200 profit
    assert out.top[0].label == "Samsung S23"
    assert out.top[0].qty == 3 and out.top[0].profit == Decimal("1700")


def test_merge_counter_keeps_discounts_and_clearance_from_online_only():
    # A counter sheet has no discount column and no tags — those stay whatever orders said.
    base = ProfitSummary(discounts=Decimal("200"), clearance_profit=Decimal("50"))
    out = merge_counter(base, [_crow(1, 100, 60)])
    assert out.discounts == Decimal("200") and out.clearance_profit == Decimal("50")


def test_merge_counter_margin_reflects_both_channels():
    out = merge_counter(ProfitSummary(), [_crow(1, 100, 50)])
    assert out.profit == Decimal("50") and out.cost == Decimal("50")
    assert round(out.margin, 1) == 100.0


# --- extract_rows: the vision-model override must actually be used ---
@pytest.mark.asyncio
async def test_extract_rows_uses_the_vision_model_and_sends_an_image_part(monkeypatch):
    from app.orders import counter_sales as cs

    seen = {}

    class _FakeLLM:
        async def chat(self, messages, tools=None, tool_choice=None, model=None):
            seen["model"] = model
            seen["content"] = messages[0].content
            return SimpleNamespace(content='[{"code": "PR0001", "qty": 1, "price": 10}]')

    monkeypatch.setattr("app.llm.llm_client.get_llm_client", lambda: _FakeLLM())

    rows = await cs.extract_rows(b"\xff\xd8jpegbytes")

    from app.core.config import settings

    assert seen["model"] == settings.ai_vision_model  # not the chat model
    parts = seen["content"]
    assert isinstance(parts, list)  # content-parts, not a plain string
    assert parts[0]["type"] == "text" and parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert rows == [{"code": "PR0001", "qty": 1, "price": Decimal("10")}]


@pytest.mark.asyncio
async def test_extract_rows_propagates_unreadable_as_valueerror(monkeypatch):
    from app.orders import counter_sales as cs

    class _FakeLLM:
        async def chat(self, messages, tools=None, tool_choice=None, model=None):
            return SimpleNamespace(content="sorry, I can't read that")

    monkeypatch.setattr("app.llm.llm_client.get_llm_client", lambda: _FakeLLM())
    with pytest.raises(ValueError):
        await cs.extract_rows(b"x")


# --- counter_totals excludes flagged rows from the money ---
@pytest.mark.asyncio
async def test_counter_totals_excludes_discrepancy_rows(monkeypatch):
    from app.orders import counter_sales as cs

    async def _report(shop_id, start, end, client=None):
        return [dict(_crow(1, 100, 50), discrepancy=False),
                dict(_crow(9, 100, 50), discrepancy=True)]  # phantom sale

    monkeypatch.setattr(cs, "sales_report", _report)
    rows = await cs.counter_totals(uuid4(), datetime.now(UTC), datetime.now(UTC))
    assert len(rows) == 1 and rows[0]["quantity"] == 1  # flagged row never inflates profit
