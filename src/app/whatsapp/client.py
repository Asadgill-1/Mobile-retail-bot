"""Outbound WhatsApp — the cutover target, deliberately unimplemented.

The provider is not chosen yet (Twilio vs Meta Cloud API is a real decision: Twilio exposes no
typing indicator, which would cost a visible part of the paced, human-feeling delivery). The seam
above this — `messaging.channel` — exists now so that choosing one is a contained change and
switching a shop is a console action rather than a build.

Everything here fails LOUDLY in the log and SOFTLY to the caller. That is the right shape while
unconfigured: `notify` promises never to raise on the customer's reply path, but a shop that has
been switched to WhatsApp without a working sender must not look healthy.

Credentials are the PLATFORM's, one set, in `platform_settings` (024) — never per shop, never sent
to a browser, handled like the service-role key. A shop row carries only `whatsapp_phone_id`.

Two things the implementation must not forget, both already load-bearing elsewhere:

1. The 24-HOUR WINDOW. Free-form replies are only allowed within 24h of the customer's last
   inbound; outside it the message must be an approved template or it is silently rejected. Five of
   the six things we send are not conversational — price approved/declined, order confirmed, out
   for delivery, delivered — and they fire hours or days later. A shopkeeper approving a price the
   next morning must not vanish.
2. `identity` is an E.164 phone number here, not a Telegram user id.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_UNCONFIGURED = "shop=%s is on the whatsapp channel but no sender is configured yet"


async def send_text(shop: Any, identity: str, text: str) -> bool:
    if not await _free_form_allowed(shop, identity):
        return False
    logger.error(_UNCONFIGURED, getattr(shop, "id", "?"))
    return False


async def _free_form_allowed(shop: Any, identity: str) -> bool:
    """Whether we may still send an ordinary message, or need an approved template.

    The tracking is live now so the implementation cannot forget it: outside the window the
    provider accepts the call and drops the message, which looks like success. A shopkeeper who
    approves a price the next morning would never learn the customer was not told.
    """
    from app.db.redis_client import get_redis
    from app.messaging.channel import within_service_window

    if await within_service_window(get_redis(), getattr(shop, "id", None), identity):
        return True
    logger.error(
        "shop=%s identity=%s is outside the 24h service window — this needs an approved "
        "template, not a free-form message. Not sent.",
        getattr(shop, "id", "?"), identity,
    )
    return False


async def send_photo(shop: Any, identity: str, photo: bytes, caption: str | None = None) -> bool:
    logger.error(_UNCONFIGURED, getattr(shop, "id", "?"))
    return False


async def typing(shop: Any, identity: str) -> None:
    """No-op. Meta's Cloud API supports a typing indicator; Twilio does not. Whichever is chosen,
    a missing indicator is cosmetic and must never cost a reply."""
    return None
