"""Twilio WhatsApp — send only; inbound verification lives in webhook.py where the raw form is.

Kept alongside Meta because it is the faster path to a working number and its signature check was
already built and tested. What it cannot do is a typing indicator: Twilio's WhatsApp API exposes
no such thing, so a shop on this provider gets paced bubbles with pauses but no "typing…" above
them. That is a real difference in how human the chat feels, and it is why the provider is a
per-platform choice rather than an implementation detail.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0


def _wa(number: str) -> str:
    """Twilio addresses WhatsApp endpoints as 'whatsapp:+971...'."""
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


async def _send(sid: str, token: str, from_: str, to: str, form: dict[str, str]) -> bool:
    """One Messages call. Never raises."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            r = await http.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                auth=(sid, token),
                data={"From": _wa(from_), "To": _wa(to), **form},
            )
        if r.status_code >= 400:
            logger.error("twilio send failed status=%s body=%s", r.status_code, r.text[:400])
            return False
        return True
    except Exception:  # noqa: BLE001
        logger.exception("twilio send raised to=%s", to)
        return False


async def send_text(sid: str, token: str, from_: str, to: str, text: str) -> bool:
    return await _send(sid, token, from_, to, {"Body": text})


async def send_image(
    sid: str, token: str, from_: str, to: str, link: str, caption: str | None
) -> bool:
    form = {"MediaUrl": link}
    if caption:
        form["Body"] = caption
    return await _send(sid, token, from_, to, form)
