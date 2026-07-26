"""Outbound WhatsApp: pick a provider, respect the 24h window, send.

Both providers are supported. They are not equivalent — Meta's Cloud API exposes a typing
indicator and Twilio's does not — so which one is in use is a platform setting the console owns,
not a build-time constant.

Credentials are the PLATFORM's, one set, never per shop and never sent to a browser:
  meta    `wa_provider=meta`, `wa_access_token`, `wa_app_secret` in platform_settings;
          the per-shop `shops.whatsapp_phone_id` says which number to send from.
  twilio  `wa_provider=twilio`; account sid/auth token come from env (already there for the
          inbound signature check), and `shops.whatsapp_number` is the sender.

Everything here fails LOUDLY in the log and SOFTLY to the caller. `notify` promises never to raise
on the customer's reply path, but a shop switched to WhatsApp with a broken sender must not look
healthy.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

META = "meta"
TWILIO = "twilio"

_PROVIDER_KEY = "wa_provider"
_TOKEN_KEY = "wa_access_token"
_SECRET_KEY = "wa_app_secret"

# The console can change these at runtime; re-reading per message would be a DB round-trip on the
# hot path. Same TTL trick as the LLM overlay — and the same sentinel, for the same reason:
# `time.monotonic()` is uptime on Linux, so 0.0 would mean "just read" on a freshly booted box.
_TTL_SECONDS = 60.0
_NEVER = float("-inf")
_cache: dict[str, str] = {}
_cache_at = _NEVER


def _read_settings() -> dict[str, str]:
    from app.db.supabase_client import get_supabase

    rows = (
        get_supabase().table("platform_settings").select("key,value")
        .in_("key", [_PROVIDER_KEY, _TOKEN_KEY, _SECRET_KEY]).execute().data or []
    )
    return {r["key"]: v.strip() for r in rows if isinstance(v := r.get("value"), str) and v.strip()}


async def config() -> dict[str, str]:
    """Current provider + credentials, cached for a minute. Never raises: an unreachable settings
    table must not stop a reply that a still-valid cached config could deliver."""
    global _cache, _cache_at
    import asyncio

    if time.monotonic() - _cache_at < _TTL_SECONDS:
        return _cache
    _cache_at = time.monotonic()  # stamp first: a failing read must not retry on every message
    try:
        _cache = await asyncio.to_thread(_read_settings)
    except Exception:  # noqa: BLE001
        logger.warning("whatsapp settings read failed; keeping last known", exc_info=True)
    return _cache


async def provider() -> str:
    return (await config()).get(_PROVIDER_KEY) or TWILIO


async def app_secret() -> str:
    return (await config()).get(_SECRET_KEY, "")


async def _free_form_allowed(shop: Any, identity: str) -> bool:
    """Whether an ordinary message is still allowed, or this needs an approved template.

    Checked before every send because outside the window the provider ACCEPTS the call and drops
    the message — it looks like success. A shopkeeper approving a price the next morning would
    otherwise never learn the customer was not told.
    """
    from app.db.redis_client import get_redis
    from app.messaging.channel import within_service_window

    if await within_service_window(get_redis(), getattr(shop, "id", None), identity):
        return True
    logger.error(
        "shop=%s identity=%s is outside the 24h service window — this needs an approved template, "
        "not a free-form message. NOT SENT.", getattr(shop, "id", "?"), identity,
    )
    return False


async def send_text(shop: Any, identity: str, text: str) -> bool:
    if not await _free_form_allowed(shop, identity):
        return False
    cfg = await config()
    which = cfg.get(_PROVIDER_KEY) or TWILIO

    if which == META:
        phone_id, token = getattr(shop, "whatsapp_phone_id", None), cfg.get(_TOKEN_KEY)
        if not phone_id or not token:
            logger.error("meta: shop=%s missing phone id or platform token", getattr(shop, "id", "?"))
            return False
        from app.whatsapp import meta

        return await meta.send_text(phone_id, token, identity, text)

    sid, token, from_ = settings.twilio_account_sid, settings.twilio_auth_token, _sender(shop)
    if not sid or not token or not from_:
        logger.error("twilio: shop=%s missing credentials or sender", getattr(shop, "id", "?"))
        return False
    from app.whatsapp import twilio_client

    return await twilio_client.send_text(sid, token, from_, identity, text)


async def send_photo(shop: Any, identity: str, photo: bytes, caption: str | None = None) -> bool:
    """Not implemented on either provider yet, and deliberately not faked.

    Both take a URL, not bytes — a Telegram file_id is bound to the bot that received it, so the
    keeper's photo arrives here as raw bytes and has to be uploaded somewhere reachable first. The
    honest thing is to say so and tell the shop, rather than return True and lose the photo.
    """
    logger.error(
        "shop=%s: sending a shop photo over WhatsApp needs the bytes uploaded to a public URL "
        "first; not sent", getattr(shop, "id", "?"),
    )
    return False


async def send_image_url(shop: Any, identity: str, link: str, caption: str | None = None) -> bool:
    """Send an image that already has a reachable URL — which is what `_product_media` produces."""
    if not await _free_form_allowed(shop, identity):
        return False
    cfg = await config()
    if (cfg.get(_PROVIDER_KEY) or TWILIO) == META:
        from app.whatsapp import meta

        return await meta.send_image(
            getattr(shop, "whatsapp_phone_id", "") or "", cfg.get(_TOKEN_KEY, ""),
            identity, link, caption,
        )
    from app.whatsapp import twilio_client

    return await twilio_client.send_image(
        settings.twilio_account_sid, settings.twilio_auth_token, _sender(shop), identity,
        link, caption,
    )


async def typing(shop: Any, identity: str) -> None:
    """Meta supports a typing indicator; Twilio does not. Cosmetic either way — it must never
    cost a reply, so a missing one is silent."""
    return None


def _sender(shop: Any) -> str:
    return getattr(shop, "whatsapp_number", None) or settings.twilio_default_whatsapp_from
