"""Which channel a shop's CUSTOMERS are on, and how to reach them.

Only the customer bot moves to WhatsApp. Keeper, shop-owner, rider and platform-owner bots stay on
Telegram permanently, so after the cutover every staff action that reaches a customer crosses
channels: a shopkeeper approving a price on Telegram, answering a customer on WhatsApp.

That is survivable only because `notify.send_to_customer` is already the single outbound choke
point — all nine call sites (escalation replies, price approved/declined, order confirmed, rider
updates) route through it. This module is what that one function dispatches on, so the switch is a
column, not a rewrite.

Imports are deliberately lazy inside the methods: `telegram_bot.notify` calls back into here, and
`whatsapp.client` is only needed once a shop has actually been switched over.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from app.tenants.models import Shop

logger = logging.getLogger(__name__)

TELEGRAM = "telegram"
WHATSAPP = "whatsapp"

# When this customer last wrote to this shop. WhatsApp only allows a free-form reply within 24h of
# that; outside it a message must be an approved template or the provider drops it. Five of the six
# things we send a customer are NOT conversational — price approved, price declined, order
# confirmed, out for delivery, delivered — and they fire hours or days later, so a shopkeeper
# approving a price the next morning is exactly the case that silently fails.
#
# Recorded on every channel, not just WhatsApp: it costs one Redis write on a path that already
# does several, and a stamp that only exists after cutover would be missing for every conversation
# in flight at the moment a shop is switched.
_LAST_INBOUND_KEY = "last_in:{shop_id}:{identity}"
_LAST_INBOUND_TTL = 172_800  # 2 days — longer than the window it answers questions about
SERVICE_WINDOW_SECONDS = 86_400


def last_inbound_key(shop_id: Any, identity: str) -> str:
    return _LAST_INBOUND_KEY.format(shop_id=shop_id, identity=identity)


async def note_inbound(redis: Any, shop_id: Any, identity: str) -> None:
    """Stamp 'they wrote to us just now'. Best-effort: never costs a customer their answer."""
    import time

    try:
        await redis.set(last_inbound_key(shop_id, identity), str(int(time.time())),
                        ex=_LAST_INBOUND_TTL)
    except Exception:  # noqa: BLE001
        logger.debug("could not stamp last inbound", exc_info=True)


async def within_service_window(redis: Any, shop_id: Any, identity: str) -> bool:
    """True if a free-form reply is still allowed. Unknown (no stamp) counts as OUTSIDE: assuming
    we may write freely is the failure that loses the message silently."""
    import time

    try:
        raw = await redis.get(last_inbound_key(shop_id, identity))
    except Exception:  # noqa: BLE001
        return False
    if not raw:
        return False
    try:
        return (time.time() - int(raw)) < SERVICE_WINDOW_SECONDS
    except (TypeError, ValueError):
        return False


class CustomerChannel(Protocol):
    """What any customer-facing channel must be able to do.

    Every method is best-effort and must never raise: they run on the customer's reply path, often
    inside an `except` block, and an outage must not turn a degraded answer into no answer (ADR-009).
    """

    async def send_text(self, shop: Shop, identity: str, text: str) -> bool: ...

    async def send_photo(
        self, shop: Shop, identity: str, photo: bytes, caption: str | None = None
    ) -> bool: ...

    async def typing(self, shop: Shop, identity: str) -> None: ...


class TelegramChannel:
    """Today's behaviour, unchanged. `identity` is a Telegram user id."""

    name = TELEGRAM

    async def send_text(self, shop: Shop, identity: str, text: str) -> bool:
        from app.telegram_bot.notify import _send

        return await _send(
            shop.telegram_customer_bot_token, identity, text, what="customer message"
        )

    async def send_photo(
        self, shop: Shop, identity: str, photo: bytes, caption: str | None = None
    ) -> bool:
        from app.telegram_bot.notify import _send_photo

        return await _send_photo(shop.telegram_customer_bot_token, identity, photo, caption)

    async def typing(self, shop: Shop, identity: str) -> None:
        from telegram import Bot
        from telegram.constants import ChatAction

        token = shop.telegram_customer_bot_token
        if not token:
            return
        try:
            async with Bot(token) as bot:
                await bot.send_chat_action(chat_id=identity, action=ChatAction.TYPING)
        except Exception:  # noqa: BLE001 — a cosmetic indicator must never cost a reply
            logger.debug("typing indicator failed", exc_info=True)


class WhatsAppChannel:
    """The cutover target. `identity` is an E.164 phone number.

    Stubbed until a provider is chosen and credentials land in platform_settings — the seam exists
    now so switching a shop is a console action rather than a build. Every method fails loudly in
    the log and softly to the caller, which is the correct shape while unconfigured: a shop that has
    been switched over without credentials must not silently look healthy.
    """

    name = WHATSAPP

    async def send_text(self, shop: Shop, identity: str, text: str) -> bool:
        from app.whatsapp.client import send_text

        return await send_text(shop, identity, text)

    async def send_photo(
        self, shop: Shop, identity: str, photo: bytes, caption: str | None = None
    ) -> bool:
        from app.whatsapp.client import send_photo

        return await send_photo(shop, identity, photo, caption)

    async def typing(self, shop: Shop, identity: str) -> None:
        from app.whatsapp.client import typing

        await typing(shop, identity)


_TELEGRAM = TelegramChannel()
_WHATSAPP = WhatsAppChannel()


def channel_for(shop: Any) -> CustomerChannel:
    """The shop's customer channel. Anything unrecognised falls back to Telegram — a shop whose
    column is somehow junk keeps answering customers rather than going silent."""
    if getattr(shop, "customer_channel", TELEGRAM) == WHATSAPP:
        return _WHATSAPP
    return _TELEGRAM
