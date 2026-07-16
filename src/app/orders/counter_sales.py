"""Counter sales: the walk-in half of the business the system was blind to.

The flow (man-in-the-middle by design — the AI never writes stock unsupervised):
  1. Shop prints the counter sheet (`products.export_counter_sheet`) and fills it by hand.
  2. Shop owner photographs the filled sheet and sends it to their bot.
  3. `extract_rows` asks the vision model to read it into strict JSON.
  4. The owner SEES the parsed rows, corrects any line, and only then confirms.
  5. `record_sales` writes them — decrementing stock exactly like a confirmed order does.

A row whose stock is insufficient is still recorded, flagged `discrepancy`, with stock left
untouched: the sheet says it sold, the system says it couldn't have. That contradiction is the
single most useful thing this table stores, so it is never silently dropped or auto-corrected.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.orders.service import _decrement_stock, _sb, notify_low_stock
from app.products.service import ProductNotFound, get_product_by_ref
from app.reports.service import DUBAI

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """You are reading a shop's handwritten counter-sale sheet.

Return ONLY a JSON array. No prose, no markdown fences. Each element:
  {"code": "<Product ID exactly as printed>", "qty": <integer>, "price": <number per unit>}

Rules:
- One element per row that has a Qty sold value. Skip rows with no sale written.
- "price" is the per-unit price written in "Price sold". If the row has no price, use null.
- Never invent a product code. Copy what is printed in the Product ID column.
- If a row is unreadable, skip it rather than guess.

Return [] if nothing was sold."""


def parse_extraction(text: str) -> list[dict]:
    """Model output → validated rows. Raises ValueError with a message a human can act on.

    Pure — the whole fragile part of vision extraction is testable without a model.
    """
    raw = (text or "").strip()
    if raw.startswith("```"):  # models fence JSON despite being told not to
        raw = raw.split("```", 2)[1] if raw.count("```") >= 2 else raw.strip("`")
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    raw = raw.strip()
    if not raw:
        raise ValueError("The sheet couldn't be read. Try a clearer, straight-on photo.")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("The sheet couldn't be read. Try a clearer, straight-on photo.") from None
    if not isinstance(data, list):
        raise ValueError("The sheet couldn't be read. Try a clearer, straight-on photo.")

    rows: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        try:
            qty = int(item.get("qty"))
        except (TypeError, ValueError):
            continue  # a row without a readable quantity is not a sale
        if qty <= 0:
            continue
        price = item.get("price")
        try:
            price = None if price in (None, "") else Decimal(str(price))
        except (InvalidOperation, TypeError):
            price = None
        if price is not None and price < 0:
            price = None
        rows.append({"code": code, "qty": qty, "price": price})
    return rows


async def extract_rows(image_bytes: bytes) -> list[dict]:
    """Photo → parsed sale rows, via the vision model. Raises ValueError if unreadable."""
    from app.core.config import settings
    from app.llm.llm_client import LLMMessage, get_llm_client

    b64 = base64.b64encode(image_bytes).decode()
    message = LLMMessage(
        role="user",
        content=[
            {"type": "text", "text": EXTRACT_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ],
    )
    response = await get_llm_client().chat([message], model=settings.ai_vision_model)
    return parse_extraction(response.content or "")


async def record_sales(
    shop: Any, rows: list[dict], *, photo_path: str | None, recorded_by: int,
    client: Any | None = None,
) -> dict:
    """Write confirmed counter sales. Returns what happened, per row.

    {saved: [...], discrepancies: [...], unknown: [codes], total: Decimal}
    """
    sb = _sb(client)
    sold_on = datetime.now(DUBAI).date().isoformat()
    saved: list[dict] = []
    discrepancies: list[dict] = []
    unknown: list[str] = []
    total = Decimal("0")

    for row in rows:
        try:
            product = await get_product_by_ref(shop.id, row["code"], client)  # the tenant guard
        except ProductNotFound:
            unknown.append(row["code"])
            continue

        qty = int(row["qty"])
        price = row.get("price")
        price = product.selling_price if price is None else Decimal(str(price))

        # Stock first: if it can't be taken, the sale is still recorded — flagged, not dropped.
        ok = await _decrement_stock(shop.id, str(product.id), qty, client)
        entry = {
            "shop_id": str(shop.id),
            "product_id": str(product.id),
            "quantity": qty,
            "sold_price": str(price),
            "sold_on": sold_on,
            "photo_path": photo_path,
            "recorded_by": recorded_by,
            "discrepancy": not ok,
        }

        def _insert(e: dict = entry) -> None:
            sb.table("counter_sales").insert(e).execute()

        await asyncio.to_thread(_insert)

        line = {"code": row["code"], "label": f"{product.brand} {product.model}".strip(),
                "qty": qty, "price": price, "stock": product.quantity}
        if ok:
            saved.append(line)
            total += price * qty
            # Stock went down — same alert hook confirm_order uses.
            await notify_low_stock(shop, product.id, client)
        else:
            discrepancies.append(line)

    return {"saved": saved, "discrepancies": discrepancies, "unknown": unknown, "total": total}


async def sales_report(
    shop_id: UUID, start: datetime, end: datetime, client: Any | None = None
) -> list[dict]:
    """Counter sales in a window (Dubai days), oldest first, for the owner's report."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("counter_sales")
            .select("*, products(brand,model,cost_price)")
            .eq("shop_id", str(shop_id))
            .gte("sold_on", start.date().isoformat())
            .lt("sold_on", end.date().isoformat())
            .order("sold_on")
            .execute()
            .data
            or []
        )

    return await asyncio.to_thread(_q)


async def counter_totals(
    shop_id: UUID, start: datetime, end: datetime, client: Any | None = None
) -> list[dict]:
    """Rows for folding counter sales into /profit. Discrepancy rows are EXCLUDED — no stock
    moved, so counting them as revenue would inflate profit with the very thing we flagged."""
    rows = await sales_report(shop_id, start, end, client)
    return [r for r in rows if not r.get("discrepancy")]
