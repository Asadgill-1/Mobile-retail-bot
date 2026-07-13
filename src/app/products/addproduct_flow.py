"""`/addproduct` — the 11-step conversational flow (SPEC §4).

Built on PTB's `ConversationHandler` (native state machine — nothing hand-rolled here).
Runs on the per-shop keeper bot, so `bot_data["shop"]` scopes every write; the shopkeeper
never supplies a `shop_id`.

ponytail: ConversationHandler keeps draft state in process memory, brushing SPEC §11
("all state in Redis, zero local memory"). ceiling: single bot process — a restart loses
in-flight drafts, and a second worker wouldn't see them. upgrade: PTB persistence backed
by Redis once the bot runs more than one process.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.products.media import MAX_IMAGES, upload_media
from app.products.service import (
    VALID_CATEGORIES,
    VALID_CONDITIONS,
    InvalidProductField,
    create_product,
    new_product_id,
    parse_category,
    parse_condition,
    parse_non_empty,
    parse_price,
    parse_quantity,
    parse_spec_line,
)

logger = logging.getLogger(__name__)

# SPEC §4 steps 1-11
CATEGORY, BRAND, MODEL, COLOR, CONDITION, SPECS, COST, SELLING, QUANTITY, MEDIA, CONFIRM = range(11)

_DRAFT = "addproduct_draft"


def _draft(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault(_DRAFT, {"specs": {}, "images": [], "video": None})


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_DRAFT, None)
    _draft(context)
    await update.message.reply_text(
        "🆕 New product.\n\n1/11 Category? " + " / ".join(VALID_CATEGORIES) + "\n(/cancel to abort)"
    )
    return CATEGORY


def _step(field: str, parse, prompt: str, next_state: int):
    """One validate-then-advance step. Invalid input re-asks; it never advances."""

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
        try:
            _draft(context)[field] = parse(update.message.text)
        except InvalidProductField as e:
            await update.message.reply_text(f"⚠️ {e}\nTry again, or /cancel.")
            return None  # PTB: None keeps the conversation in the current state
        await update.message.reply_text(prompt)
        return next_state

    return handler


_SPECS_PROMPT = (
    "6/11 Specs — one `key: value` per line, e.g. `camera: 108MP`.\n"
    "Send them one message at a time. /done when finished."
)
_MEDIA_PROMPT = (
    f"10/11 Send up to {MAX_IMAGES} images, and optionally one video.\n"
    "/skip or /done when finished."
)

category = _step("category", parse_category, "2/11 Brand?", BRAND)
brand = _step("brand", lambda t: parse_non_empty(t, "brand"), "3/11 Model?", MODEL)
model = _step("model", lambda t: parse_non_empty(t, "model"), "4/11 Color? (or `-`)", COLOR)
condition = _step("condition", parse_condition, _SPECS_PROMPT, SPECS)


async def color(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    _draft(context)["color"] = None if raw == "-" else raw  # `-` means "no colour"
    await update.message.reply_text("5/11 Condition? " + " / ".join(VALID_CONDITIONS))
    return CONDITION


async def specs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = _draft(context)
    for line in (update.message.text or "").splitlines():
        if not line.strip():
            continue
        try:
            key, value = parse_spec_line(line)
        except InvalidProductField as e:
            await update.message.reply_text(f"⚠️ {e}")
            return SPECS
        draft["specs"][key] = value
    await update.message.reply_text(f"✅ {len(draft['specs'])} spec(s). More, or /done.")
    return SPECS


async def specs_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("7/11 Cost price (AED)?")
    return COST


cost = _step("cost_price", parse_price, "8/11 Selling price (AED)?", SELLING)
selling = _step("selling_price", parse_price, "9/11 Quantity?", QUANTITY)
quantity = _step("quantity", parse_quantity, _MEDIA_PROMPT, MEDIA)


async def media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Collect media bytes now; upload only once the shopkeeper confirms the save."""
    draft = _draft(context)
    msg = update.message

    if msg.photo:
        if len(draft["images"]) >= MAX_IMAGES:
            await msg.reply_text(f"⚠️ {MAX_IMAGES} images max. /done to continue.")
            return MEDIA
        f = await msg.photo[-1].get_file()  # largest rendition
        draft["images"].append(bytes(await f.download_as_bytearray()))
        await msg.reply_text(f"📷 {len(draft['images'])}/{MAX_IMAGES}. More, or /done.")
        return MEDIA

    if msg.video:
        f = await msg.video.get_file()
        draft["video"] = bytes(await f.download_as_bytearray())
        await msg.reply_text("🎬 Video attached. /done to continue.")
        return MEDIA

    await msg.reply_text("Send a photo or video, or /skip · /done.")
    return MEDIA


def _summary(draft: dict) -> str:
    specs_txt = "\n".join(f"  {k}: {v}" for k, v in draft["specs"].items()) or "  (none)"
    profit = draft["selling_price"] - draft["cost_price"]
    return (
        f"11/11 Confirm:\n\n"
        f"{draft['category']} · {draft['brand']} {draft['model']}\n"
        f"Color: {draft.get('color') or '—'} · Condition: {draft['condition']}\n"
        f"Specs:\n{specs_txt}\n"
        f"Cost: {draft['cost_price']} AED · Selling: {draft['selling_price']} AED "
        f"(margin {profit} AED)\n"
        f"Qty: {draft['quantity']} · Images: {len(draft['images'])} · "
        f"Video: {'yes' if draft['video'] else 'no'}\n\n"
        f"/save to store it, /cancel to discard."
    )


async def media_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(_summary(_draft(context)))
    return CONFIRM


async def save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 11: upload media under the new product id, then one insert."""
    draft = _draft(context)
    shop = context.application.bot_data["shop"]
    product_id = new_product_id()  # generated here so media can be pathed before the insert

    try:
        image_paths = [
            await upload_media(shop.id, product_id, f"image_{i}.jpg", data, "image/jpeg")
            for i, data in enumerate(draft["images"])
        ]
        video_path = (
            await upload_media(shop.id, product_id, "video.mp4", draft["video"], "video/mp4")
            if draft["video"]
            else None
        )
        product = await create_product(
            shop.id,
            product_id=product_id,
            category=draft["category"],
            brand=draft["brand"],
            model=draft["model"],
            color=draft.get("color"),
            condition=draft["condition"],
            specs=draft["specs"],
            cost_price=draft["cost_price"],
            selling_price=draft["selling_price"],
            quantity=draft["quantity"],
            images=image_paths,
            video_url=video_path,
        )
    except Exception:
        logger.exception("addproduct save failed shop=%s", shop.id)
        await update.message.reply_text("❌ Could not save the product. Nothing was stored.")
        return ConversationHandler.END

    context.user_data.pop(_DRAFT, None)
    # brand/model are shopkeeper free-text: a name like 'Galaxy_S24' or 'Note*' would break
    # Markdown parsing (400 Bad Request → no confirmation shown). Use Telegram HTML with the
    # dynamic parts escaped so any characters render literally and the send can never 400.
    from app.telegram_bot.format import escape_html

    await update.message.reply_text(
        f"✅ Saved.\n{escape_html(product.brand)} {escape_html(product.model)}\n"
        f"id: <code>{product.id}</code>\n"
        f"Use it with /boost, /tag, /feature.",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_DRAFT, None)
    await update.message.reply_text("🚫 Cancelled. Nothing was saved.")
    return ConversationHandler.END


def build_addproduct_handler() -> ConversationHandler:
    """SPEC §4's 11-step flow. Registered on the per-shop keeper bot only."""
    text = filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        entry_points=[CommandHandler("addproduct", start)],
        states={
            CATEGORY: [MessageHandler(text, category)],
            BRAND: [MessageHandler(text, brand)],
            MODEL: [MessageHandler(text, model)],
            COLOR: [MessageHandler(text, color)],
            CONDITION: [MessageHandler(text, condition)],
            SPECS: [CommandHandler("done", specs_done), MessageHandler(text, specs)],
            COST: [MessageHandler(text, cost)],
            SELLING: [MessageHandler(text, selling)],
            QUANTITY: [MessageHandler(text, quantity)],
            MEDIA: [
                CommandHandler("done", media_done),
                CommandHandler("skip", media_done),
                MessageHandler(filters.PHOTO | filters.VIDEO, media),
                MessageHandler(text, media),
            ],
            CONFIRM: [CommandHandler("save", save)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="addproduct",
    )
