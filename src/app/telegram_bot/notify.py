"""Outbound Telegram sends, outside any handler (SPEC §3, §13; ADR-009).

Lives in `telegram_bot/` because that module owns Telegram. `escalations/` imports this;
this imports nothing of ours but config — so there is no cycle back through `bot.py`.

Every send is **best-effort**. A notification that fails must never propagate: it runs on the
customer's reply path (often inside an `except` block), and a Telegram outage must not turn a
degraded answer into no answer at all (ADR-009).

In Telegram-first testing (ADR-002) the "customer" is a Telegram user, reached through the
shop's customer bot. At Stage 13 `send_to_customer` becomes the Twilio WhatsApp send; nothing
else here changes.
"""

from __future__ import annotations

import logging
from typing import Iterable

from telegram import Bot
from telegram.error import TelegramError

from app.core.config import settings
from app.tenants.models import Shop, Shopkeeper

logger = logging.getLogger(__name__)


async def _send(token: str | None, chat_id: int | str, text: str, *, what: str) -> bool:
    """Send one message. Returns success; never raises."""
    if not token:
        logger.error("cannot send %s: no bot token configured", what)
        return False
    try:
        # ponytail: a fresh Bot per send (one HTTP session each). ceiling: wasteful under load.
        # upgrade: hold long-lived Bot instances once notification volume justifies it.
        async with Bot(token) as bot:
            await bot.send_message(chat_id=chat_id, text=text)
        return True
    except TelegramError as e:
        # 403 "chat not found" is normal until the recipient has pressed /start on that bot.
        logger.error("failed to send %s to chat=%s: %s", what, chat_id, e)
        return False
    except Exception:
        logger.exception("unexpected error sending %s to chat=%s", what, chat_id)
        return False


async def send_to_owner(text: str) -> bool:
    """Technical alerts. The owner is the ONLY audience that learns a system failed (ADR-009)."""
    return await _send(
        settings.telegram_bot_token, settings.owner_telegram_id, text, what="owner alert"
    )


async def send_to_shopkeepers(shop: Shop, shopkeepers: Iterable[Shopkeeper], text: str) -> int:
    """Notify a shop's staff on that shop's keeper bot. Returns how many were reached."""
    reached = 0
    for sk in shopkeepers:
        if await _send(shop.telegram_keeper_bot_token, sk.telegram_id, text, what="shopkeeper notice"):
            reached += 1
    if reached == 0:
        logger.error("no shopkeeper reached for shop=%s (%s)", shop.id, shop.name)
    return reached


async def send_to_rider(telegram_id: int, text: str) -> bool:
    """Push a delivery assignment to a rider on the global rider bot. Best-effort (never raises).

    Fails (returns False) if the rider bot token is unset or the rider hasn't pressed /start yet —
    the caller tells the shopkeeper the rider wasn't reached, it never breaks the assignment.
    """
    return await _send(
        settings.telegram_rider_bot_token, telegram_id, text, what="rider assignment"
    )


async def send_to_customer(shop: Shop, identity: str, text: str) -> bool:
    """Reach the customer on the shop's customer-facing channel.

    Testing: the shop's customer bot, `identity` is a Telegram user id (ADR-002/005).
    Stage 13: this becomes the Twilio WhatsApp send and `identity` is a phone number.
    """
    return await _send(shop.telegram_customer_bot_token, identity, text, what="customer message")
