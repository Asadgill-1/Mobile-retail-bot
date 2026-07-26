"""WhatsApp Business Cloud API (Meta) — send, inbound parse, signature check.

Chosen alongside Twilio rather than instead of it: this one supports typing indicators and read
receipts, which the paced delivery depends on, and it is cheaper per message. The platform owns
every number, so the access token and app secret are ONE set in `platform_settings`; a shop row
carries only its `whatsapp_phone_id`, which is what inbound webhooks route on.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"
_TIMEOUT = 15.0


async def _post(phone_id: str, token: str, payload: dict) -> bool:
    """One Graph call. Never raises — an outbound failure must not break the customer's turn."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            r = await http.post(
                f"{GRAPH}/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
        if r.status_code >= 400:
            # Meta returns 200 for "accepted", so anything else is worth the log line in full:
            # a 24h-window rejection and a bad token look identical from the outside otherwise.
            logger.error("meta send failed phone_id=%s status=%s body=%s",
                         phone_id, r.status_code, r.text[:400])
            return False
        return True
    except Exception:  # noqa: BLE001
        logger.exception("meta send raised phone_id=%s", phone_id)
        return False


async def send_text(phone_id: str, token: str, to: str, text: str) -> bool:
    return await _post(phone_id, token, {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    })


async def send_image(phone_id: str, token: str, to: str, link: str, caption: str | None) -> bool:
    """Send by URL. Meta fetches it, so the link must be reachable and live long enough — the
    signed Supabase URLs the product-media path already produces are exactly that."""
    image: dict[str, Any] = {"link": link}
    if caption:
        image["caption"] = caption
    return await _post(phone_id, token, {
        "messaging_product": "whatsapp", "to": to, "type": "image", "image": image,
    })


async def mark_typing(phone_id: str, token: str, message_id: str) -> bool:
    """Read receipt + typing indicator, which is why this provider is worth supporting: without
    it the paced reply arrives as silence and then a wall of text."""
    return await _post(phone_id, token, {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    })


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256 over the RAW body. Compared with compare_digest — a timing-safe check
    costs nothing and this is the only thing standing between the pipeline and the open internet."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse_inbound(payload: dict) -> list[dict]:
    """Meta's webhook envelope → flat messages.

    The shape is deeply nested and batched: entry[] → changes[] → value.messages[]. A status
    callback (delivered/read) carries no `messages` key at all and must yield nothing rather than
    being mistaken for a customer writing in.
    """
    out: list[dict] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            phone_id = ((value.get("metadata") or {}).get("phone_number_id")) or ""
            for msg in value.get("messages") or []:
                if msg.get("type") != "text":
                    continue  # images/audio/location: nothing to answer with yet
                out.append({
                    "phone_id": phone_id,
                    "from": msg.get("from") or "",
                    "text": ((msg.get("text") or {}).get("body")) or "",
                    "message_id": msg.get("id") or "",
                })
    return out
