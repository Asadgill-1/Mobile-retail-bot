"""Twilio WhatsApp inbound webhook (SPEC §1, §9 step 1, §11; ADR-002).

Verifies the Twilio signature, resolves the shop from the `To` number, hands the message to this
conversation's paced worker, and returns 200 immediately (SPEC §11). Mocked during Telegram-first
testing (no real numbers); activated once a shop is switched to the whatsapp channel (028).

Why the Pacer and not Celery. The obvious shape — enqueue and let a worker run the pipeline — loses
two things at once. Every bit of the paced, human-feeling delivery (debounce, fragment coalescing,
typing, short bubbles) lives in in-process memory, so it cannot survive a hop to another process.
And any worker could pick up a conversation another worker is mid-way through, which is exactly the
session-lock contention the load harness measured: driven straight at the pipeline, 400 messages a
minute lost 162 of them; through the Pacer, none, at half the p95 and 38% fewer model round-trips.

So the `api` process owns the customer path end to end, the way the bot process owns Telegram. The
cost is that the customer path wants ONE uvicorn worker — conversations sharded across processes
would break coalescing again. At ~7 messages/second of I/O-bound work that is not the bottleneck.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request, Response
from twilio.request_validator import RequestValidator

from app.whatsapp import client as wa_client

from app.core.config import settings
from app.db.factory import get_tenant_repo
from app.db.redis_client import get_redis
from app.messaging.channel import WHATSAPP, channel_for
from app.messaging.pacing import Pacer
from app.messaging.pipeline import InboundMessage, process_message
from app.tenants.models import Shop

logger = logging.getLogger(__name__)
router = APIRouter()

# One per process, keyed by (shop, identity) inside. The bot process holds its own in bot_data;
# this is the same class serving the other channel.
_pacer = Pacer()


def verify_twilio_signature(url: str, form: dict[str, str], signature: str | None) -> bool:
    """True iff the Twilio signature is valid for this request (SPEC §9 step 1)."""
    if not signature or not settings.twilio_auth_token:
        return False
    return RequestValidator(settings.twilio_auth_token).validate(url, form, signature)


def _strip_whatsapp(number: str) -> str:
    """Twilio sends 'whatsapp:+123'; shops.whatsapp_number stores '+123' (SPEC §1)."""
    return number.removeprefix("whatsapp:")


def _deliver(shop: Shop, identity: str, text: str) -> None:
    """Queue the message on this conversation's worker. Returns at once; the worker does the rest."""
    channel = channel_for(shop)
    redis = get_redis()

    async def answer(batch: str):
        return await process_message(InboundMessage(shop, identity, batch), redis)

    async def send(bubble: str) -> None:
        await channel.send_text(shop, identity, bubble)

    async def typing() -> None:
        await channel.typing(shop, identity)

    _pacer.submit(shop.id, identity, text, answer=answer, send=send, typing=typing)


async def _accept(shop, identity: str, text: str) -> bool:
    """Shared gate for both providers. False means we ack and do nothing."""
    if shop is None:
        return False
    # A shop still on Telegram must not be drivable through this endpoint: its customers are a
    # different identity space (Telegram user ids, not phone numbers), so accepting one here would
    # start a parallel conversation under a stranger's identity.
    if shop.customer_channel != WHATSAPP:
        logger.warning(
            "whatsapp webhook: shop=%s is on the %s channel; ignoring", shop.id, shop.customer_channel
        )
        return False
    if not identity or not text:
        return False
    return True


@router.get("/webhook/whatsapp")
async def whatsapp_verify(request: Request) -> Response:
    """Meta's one-time subscription handshake: echo hub.challenge if the verify token matches.
    Twilio has no equivalent, so this is inert when Twilio is the provider."""
    q = request.query_params
    expected = await wa_client.app_secret()
    if q.get("hub.mode") == "subscribe" and expected and q.get("hub.verify_token") == expected:
        return Response(content=q.get("hub.challenge", ""), status_code=200)
    logger.warning("whatsapp webhook: verification handshake rejected")
    return Response(status_code=403)


@router.post("/webhook/whatsapp")
async def whatsapp_inbound(request: Request) -> Response:
    """One URL, both providers. Meta posts JSON signed with X-Hub-Signature-256; Twilio posts a
    form signed with X-Twilio-Signature. Dispatching on the header rather than on the configured
    provider means a stray delivery during a provider switch is still verified correctly instead
    of being waved through or rejected."""
    raw = await request.body()

    if request.headers.get("X-Hub-Signature-256"):
        from app.whatsapp import meta

        secret = await wa_client.app_secret()
        if not meta.verify_signature(secret, raw, request.headers.get("X-Hub-Signature-256")):
            logger.warning("whatsapp webhook: bad Meta signature")
            return Response(status_code=403)
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return Response(status_code=200)  # malformed: ack, never retry-storm us

        repo = get_tenant_repo()
        for msg in meta.parse_inbound(payload):
            shop = await repo.get_shop_by_whatsapp_phone_id(msg["phone_id"])
            if await _accept(shop, msg["from"], msg["text"]):
                _deliver(shop, msg["from"], msg["text"])
        return Response(status_code=200)

    form = dict(await request.form())
    if not verify_twilio_signature(str(request.url), form, request.headers.get("X-Twilio-Signature")):
        logger.warning("whatsapp webhook: bad Twilio signature")
        return Response(status_code=403)

    to = _strip_whatsapp(form.get("To", ""))
    shop = await get_tenant_repo().get_shop_by_whatsapp_number(to)
    if shop is None:
        logger.warning("whatsapp webhook: no shop for To=%s", to)
        return Response(status_code=200)  # ack silently — don't leak which numbers are live

    identity, text = _strip_whatsapp(form.get("From", "")), form.get("Body", "")
    if await _accept(shop, identity, text):
        _deliver(shop, identity, text)
    return Response(status_code=200)  # SPEC §11: return immediately, the worker answers
