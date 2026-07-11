"""LLM tool declarations (SPEC §3, §4). Provider-agnostic OpenAI tool-calling schemas.

Declaration lives here; execution lives in `ai/orchestrator.py` (which delegates
product queries to `products/`). See `src/app/products/README.md` boundaries.
"""

from __future__ import annotations

from typing import Any

SEARCH_PRODUCTS: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_products",
        "description": (
            "Search this shop's live inventory. This is the ONLY source of product data — "
            "you have no product knowledge of your own. Call this before naming any product, "
            "price, or specification.\n\n"
            "Results are capped, so a default (relevance) search CANNOT tell you which product "
            "is cheapest or most expensive. To answer any superlative or price question you MUST "
            "set `sort` (and `max_price_aed` for a budget), or your answer will be wrong."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "requirements": {
                    "type": "string",
                    "description": (
                        "The customer's requirements in natural language, e.g. "
                        "'Samsung phone with a good camera' or 'gaming laptop'. "
                        "Leave empty to search the whole catalogue."
                    ),
                },
                "sort": {
                    "type": "string",
                    "enum": ["relevance", "price_asc", "price_desc"],
                    "description": (
                        "How to order results. 'relevance' (default) for normal browsing. "
                        "'price_asc' for cheapest / most affordable / lowest price. "
                        "'price_desc' for most expensive / top-of-the-range."
                    ),
                },
                "max_price_aed": {
                    "type": "number",
                    "description": (
                        "Only return products at or below this price in AED. Use whenever the "
                        "customer states a budget, e.g. 'under 3000' or 'around 2500 max'."
                    ),
                },
            },
            "required": ["requirements"],
        },
    },
}

ESCALATE_TO_HUMAN: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "escalate_to_human",
        "description": (
            "Hand the conversation to a human shopkeeper. Call this — and do NOT answer yourself — "
            "for refunds, complaints, repairs, price negotiations, legal questions, or whenever the "
            "customer asks to talk to a human (SPEC §3)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Short reason for the escalation, e.g. 'refund request'.",
                }
            },
            "required": ["reason"],
        },
    },
}

PLACE_ORDER: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "place_order",
        "description": (
            "Submit an order to the shop for confirmation once the customer has decided to buy. "
            "Use the `id` of a product from a previous search_products result. Only call this after "
            "you have the customer's name, delivery address, and quantity, and the item is in stock. "
            "This creates a DRAFT — a human at the shop confirms it. Do NOT tell the customer the "
            "order is placed or give an order number; after this call, reply briefly and naturally "
            "and let the confirmation come from the shop."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "The product id from a search_products result."},
                "quantity": {"type": "integer", "description": "How many units. Must be in stock."},
                "customer_name": {"type": "string", "description": "The customer's name for the order."},
                "address": {"type": "string", "description": "Delivery address."},
                "delivery_date": {"type": "string", "description": "Requested delivery date, if the customer gave one."},
                "special_instructions": {"type": "string", "description": "Any special instructions from the customer."},
            },
            "required": ["product_id", "quantity", "customer_name", "address"],
        },
    },
}

REQUEST_PRICE: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "request_price",
        "description": (
            "Ask the shop whether a discounted price is acceptable. Call this when the customer haggles "
            "or asks for a lower price — do NOT quote a discount yourself. The shop approves, counters, "
            "or declines, and the customer is told the outcome. If the shop has negotiation turned off, "
            "this returns an error and you must hold at the listed price. Any discount the customer "
            "eventually pays comes only from a shop-approved request; place_order applies it automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "The product id the customer is haggling over."},
                "requested_price_aed": {
                    "type": "number",
                    "description": "The per-unit price the customer is asking for, in AED.",
                },
            },
            "required": ["product_id", "requested_price_aed"],
        },
    },
}

SHOW_PRODUCT_MEDIA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "show_product_media",
        "description": (
            "Send the customer this product's photos (and video, if any). Call it when the customer "
            "asks to see a product, or when a picture helps them decide on one you're recommending. "
            "Use the `id` from a search_products result. The media is delivered to the customer "
            "automatically — after calling, just introduce it in one short line. Never claim you "
            "cannot share photos."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "The product id from a search_products result."},
            },
            "required": ["product_id"],
        },
    },
}

TOOLS: list[dict[str, Any]] = [
    SEARCH_PRODUCTS, ESCALATE_TO_HUMAN, PLACE_ORDER, REQUEST_PRICE, SHOW_PRODUCT_MEDIA
]
