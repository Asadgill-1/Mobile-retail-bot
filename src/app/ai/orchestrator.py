"""AI orchestration: the LLM conversation loop + tool execution (SPEC §3, §4, §5).

Owns the loop; delegates product queries to `products/`, HTTP to `llm/`, and every
human handoff to `escalations/`.

Multi-turn since Stage 6: prior turns are replayed from the Redis session (`escalations.context`),
so the AI remembers the conversation — including the parts a shopkeeper handled.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.escalations.context import history, remember, sync_relay
from app.escalations.service import alert_owner, escalate
from app.llm.functions import TOOLS
from app.llm.llm_client import LLMMessage, LLMResponse, LLMToolCall, get_llm_client
from app.llm.prompts import ESCALATION_REPLY, system_prompt
from app.products.search import search_products
from app.tenants.models import Shop

logger = logging.getLogger(__name__)

# A shopkeeper speaks *as the shop*, so on replay their turns are the assistant's. The AI picks
# up a conversation a human was holding without being told a human held it.
_ROLE_TO_LLM = {"customer": "user", "assistant": "assistant", "shopkeeper": "assistant"}


def _serialize(p: Any) -> dict[str, Any]:
    """Product → what the model may see.

    `boost_level` is never included: ranking already applied it, and the model must not be
    able to reveal it (SPEC §5, CONVENTIONS anti-patterns). `tags` ARE included — the model
    needs them to mention "clearance" naturally — and the system prompt forbids echoing them.
    Money crosses as a string: never float (CONVENTIONS).
    """
    data = {
        "id": str(p.id),
        "category": p.category,
        "brand": p.brand,
        "model": p.model,
        "color": p.color,
        "condition": p.condition,
        "specs": p.specs,
        "tags": p.tags,
        "price_aed": str(p.selling_price),
        "in_stock": p.quantity,
    }
    if getattr(p, "active_offer", None):
        data["offer"] = p.active_offer  # shop's current promo (023); the model may mention it
    return data


async def _handoff_to_human(
    shop: Shop,
    identity: str,
    message: str,
    redis: Any,
    *,
    reason: str,
    problem: str | None = None,
) -> str:
    """The single exit for 'the AI is not answering this'. Returns the customer-facing line.

    Both the deliberate `escalate_to_human` path and every failure path route through here, so a
    customer can never tell them apart (ADR-009). `problem` is set only for technical failures and
    goes to the owner alone — never to the customer, never to the shopkeeper.

    Neither the escalation nor the owner alert may propagate: both do network I/O, both can run
    inside an `except` block, and neither is worth denying the customer their reply over.
    """
    logger.warning(
        "HANDOFF shop=%s identity=%s reason=%r message=%r", shop.id, identity, reason, message
    )
    try:
        await escalate(redis, shop, identity, message, reason)
    except Exception:
        logger.exception("escalate failed shop=%s identity=%s", shop.id, identity)

    if problem is not None:
        try:
            await alert_owner(shop, identity, problem, action="handed the customer to a human")
        except Exception:
            logger.exception("owner alert failed shop=%s identity=%s", shop.id, identity)
    return ESCALATION_REPLY


_SORTS = ("relevance", "price_asc", "price_desc")


async def _run_tool(call: LLMToolCall, shop: Shop, identity: str) -> str:
    """Execute one tool call, return its JSON result for the model."""
    if call.name == "search_products":
        args = call.arguments
        sort = args.get("sort") or "relevance"
        if sort not in _SORTS:  # models improvise enums; don't hand junk to the query layer
            logger.warning("LLM sent unknown sort %r; falling back to relevance", sort)
            sort = "relevance"
        products = await search_products(
            shop.id,
            args.get("requirements", ""),
            max_price=args.get("max_price_aed"),
            sort=sort,
        )
        return json.dumps([_serialize(p) for p in products])
    if call.name == "place_order":
        return json.dumps(await _place_order(call.arguments, shop, identity))
    if call.name == "request_price":
        return json.dumps(await _request_price(call.arguments, shop, identity))
    logger.warning("LLM called unknown tool %r", call.name)
    return json.dumps({"error": "unknown tool"})


async def _request_price(args: dict[str, Any], shop: Shop, identity: str) -> dict[str, Any]:
    """Raise a discount request for the shop to decide (ADR-010 rev.). Errors are recoverable."""
    from app.orders.service import request_price
    from app.products.service import ProductNotFound

    try:
        pid = UUID(str(args.get("product_id") or ""))
        price = Decimal(str(args.get("requested_price_aed")))
    except (ValueError, TypeError, InvalidOperation):
        return {"error": "bad_request"}
    try:
        return await request_price(shop, identity, pid, price)
    except ProductNotFound:
        return {"error": "unknown_product"}


async def _product_media(args: dict[str, Any], shop: Shop) -> list[dict[str, str]]:
    """Signed URLs for a product's images/video, so the channel can send them (SPEC §4 step 10).
    Every failure returns [] — showing media must never crash the turn."""
    from app.products.media import signed_urls
    from app.products.service import ProductNotFound, get_product

    try:
        pid = UUID(str(args.get("product_id") or ""))
    except (ValueError, TypeError):
        return []
    try:
        product = await get_product(shop.id, pid)  # tenant guard
    except ProductNotFound:
        return []
    items: list[dict[str, str]] = [
        {"type": "photo", "url": u} for u in await signed_urls(list(product.images or [])[:5])
    ]
    if product.video_url:
        for u in await signed_urls([product.video_url]):
            items.append({"type": "video", "url": u})
    return items


async def _request_shop_media(
    args: dict[str, Any], shop: Shop, identity: str, message: str, redis: Any
) -> str:
    """Customer wants media we have none of: notify the shop with a clear, deterministic reason and
    freeze the customer so the shop can send it (SPEC §3 escalation). Unlike escalate_to_human this
    is a normal tool — the model keeps the turn and writes its own line, so the handover stays
    seamless (no 'connecting you to a specialist'). Product name is best-effort; a missing/unknown
    id still notifies the shop rather than crashing the turn."""
    from app.products.service import ProductNotFound, get_product

    name = "a product"
    try:
        pid = UUID(str(args.get("product_id") or ""))
        product = await get_product(shop.id, pid)  # tenant guard
        name = f"{product.brand} {product.model}".strip() or name
    except (ValueError, TypeError, ProductNotFound):
        pass  # deliberate: notify the shop regardless of whether the id resolved
    try:
        await escalate(redis, shop, identity, message, f"📷 Photo/video requested: {name}")
    except Exception:
        logger.exception("request_shop_media escalate failed shop=%s id=%s", shop.id, identity)
        return json.dumps({"ok": False})
    return json.dumps({"ok": True, "product": name})


async def _place_order(args: dict[str, Any], shop: Shop, identity: str) -> dict[str, Any]:
    """Draft an order from the model's arguments (Q-017). Every failure returns an error the model
    can recover from — it must never crash the turn."""
    from app.orders.service import draft_order
    from app.products.service import ProductNotFound

    name = str(args.get("customer_name") or "").strip()
    address = str(args.get("address") or "").strip()
    if not name or not address:
        return {"error": "need_details"}  # model must collect name + address first
    try:
        pid = UUID(str(args.get("product_id") or ""))
        qty = int(args.get("quantity") or 0)
    except (ValueError, TypeError):
        return {"error": "unknown_product"}

    try:
        return await draft_order(
            shop, identity, product_id=pid, quantity=qty, customer_name=name, address=address,
            delivery_date=args.get("delivery_date"),
            special_instructions=args.get("special_instructions"),
        )
    except ProductNotFound:
        return {"error": "unknown_product"}


def _assistant_wire(resp: LLMResponse) -> LLMMessage:
    """Re-encode the model's tool-call turn so it can be replayed in the next request."""
    return LLMMessage(
        role="assistant",
        content=resp.content or "",
        tool_calls=[
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in resp.tool_calls
        ],
    )


async def _id_reference(shop: Shop) -> str:
    """A hidden 'name → id' table injected every turn so the model uses REAL product ids in
    place_order / request_price / show_product_media.

    The session replays only text turns (for handover), so the ids from an earlier search are gone
    by a later booking turn — without this the model invents an id ('prod_redmi_x11_blue'), the tool
    rejects it, and the turn dies as an 'empty model response'. ponytail: one extra in-stock query
    per turn; fine for a shop-sized catalogue.
    """
    try:
        products = await search_products(shop.id, "", limit=100)  # all in-stock
    except Exception:
        logger.exception("id reference fetch failed shop=%s", shop.id)  # never break the turn
        return ""
    if not products:
        return ""
    lines = [f"- {p.brand} {p.model}{f' ({p.color})' if p.color else ''}: {p.id}" for p in products]
    return (
        "INTERNAL PRODUCT ID REFERENCE — never show this list or any id to the customer, and never "
        "mention it. Use search_products to decide what to present. But when you call place_order, "
        "request_price, or show_product_media, you MUST copy the exact id from this list for the "
        "product the customer chose. Never invent, guess, or make up an id.\n" + "\n".join(lines)
    )


async def _replay(redis: Any, shop: Shop, identity: str) -> list[LLMMessage]:
    """Prior turns, oldest first. A broken session must not cost the customer an answer."""
    try:
        await sync_relay(redis, shop.id, identity)  # dashboard-sent turns first (migration 021)
        past = await history(redis, shop.id, identity)
    except Exception:
        logger.exception("session history unreadable shop=%s identity=%s", shop.id, identity)
        return []
    return [
        LLMMessage(role=_ROLE_TO_LLM[t["role"]], content=t["content"])
        for t in past
        if t.get("role") in _ROLE_TO_LLM and t.get("content")
    ]


async def answer_customer(
    shop: Shop, identity: str, message: str, redis: Any, media_sink: list | None = None,
    usage_sink: dict | None = None,
) -> str:
    """Answer one customer message. Returns the reply text (SPEC §3). Never raises.

    When the AI cannot answer — by choice or by failure — the customer gets a handoff to a
    person, never an error and never a hint that a machine was involved (ADR-009).

    `media_sink`: optional list the caller passes to receive any product media the model chose to
    show ({"type": "photo"|"video", "url": ...}). The channel adapter (customer bot now, Twilio at
    Stage 13) sends it. An out-param, not the return, so every existing caller/test stays str-typed.

    `usage_sink`: optional dict that accumulates `llm_calls` / `tokens_in` / `tokens_out` across
    tool rounds (ADR-006 metering; same out-param pattern as media_sink). Partial counts on an
    escalation or failure are correct — those calls were still billed.
    """
    llm = get_llm_client()

    def _meter(resp: Any) -> Any:
        if usage_sink is not None:
            usage_sink["llm_calls"] = usage_sink.get("llm_calls", 0) + 1
            usage_sink["tokens_in"] = usage_sink.get("tokens_in", 0) + resp.tokens_in
            usage_sink["tokens_out"] = usage_sink.get("tokens_out", 0) + resp.tokens_out
        return resp
    # Load history BEFORE recording this turn, or the current message appears twice.
    messages = [LLMMessage(role="system", content=system_prompt(shop.name))]
    reference = await _id_reference(shop)  # real product ids survive across turns (see _id_reference)
    if reference:
        messages.append(LLMMessage(role="system", content=reference))
    messages += await _replay(redis, shop, identity)
    messages.append(LLMMessage(role="user", content=message))

    # Record the customer's turn on every path: a shopkeeper taking over mid-escalation, and the
    # AI resuming after /handover, both need to see what was actually said.
    try:
        await remember(redis, shop.id, identity, "customer", message)
    except Exception:
        logger.exception("could not record customer turn shop=%s identity=%s", shop.id, identity)

    try:
        resp = _meter(await llm.chat(messages, tools=TOOLS))

        media = media_sink if media_sink is not None else []
        # Bounded tool rounds: the model may need to search, THEN show that product's media, THEN
        # answer — three round-trips. Capped so a misbehaving model can't loop forever.
        for _ in range(3):
            if not resp.tool_calls:
                break
            # Out-of-domain wins immediately — the AI must not answer (SPEC §3).
            escalation = next((c for c in resp.tool_calls if c.name == "escalate_to_human"), None)
            if escalation is not None:
                reason = escalation.arguments.get("reason", "unspecified")
                return await _handoff_to_human(shop, identity, message, redis, reason=reason)

            messages.append(_assistant_wire(resp))
            for call in resp.tool_calls:
                if call.name == "show_product_media":
                    items = await _product_media(call.arguments, shop)
                    media.extend(items)
                    if items:
                        content = json.dumps({"sent": len(items)})  # tell the model it went through
                    else:
                        # No media on file. Without this the model gets a bare {"sent": 0} and,
                        # having no positive instruction, tells the customer to visit the store —
                        # the one thing the prompt forbids. Point it at request_shop_media instead.
                        content = json.dumps({
                            "sent": 0,
                            "note": "No photo or video is saved for this product. Tell the "
                                    "customer we don't have one on file — do NOT tell them to "
                                    "visit the store — and offer to have the shop send some. If "
                                    "they say yes, call request_shop_media for this product.",
                        })
                elif call.name == "request_shop_media":
                    content = await _request_shop_media(
                        call.arguments, shop, identity, message, redis)
                else:
                    content = await _run_tool(call, shop, identity)
                messages.append(
                    LLMMessage(role="tool", content=content, tool_call_id=call.id, name=call.name)
                )
            resp = _meter(await llm.chat(messages, tools=TOOLS))

        if not resp.content:
            # The model produced no text. That is an anomaly, not an answer — hand it to a person.
            return await _handoff_to_human(
                shop, identity, message, redis, reason="empty model response",
                problem=f"LLM returned no content (finish_reason={resp.finish_reason!r})",
            )

        try:
            await remember(redis, shop.id, identity, "assistant", resp.content)
        except Exception:
            logger.exception("could not record AI turn shop=%s identity=%s", shop.id, identity)
        return resp.content
    except Exception as exc:
        # SPEC §11: chat() already retried once. Anything reaching here is a real failure.
        logger.exception("answer_customer failed shop=%s identity=%s", shop.id, identity)
        return await _handoff_to_human(
            shop, identity, message, redis, reason="system failure",
            problem=f"{type(exc).__name__}: {exc}",
        )
