"""Twilio WhatsApp inbound webhook (SPEC §1, §9 step 1, §11; ADR-002).

Verifies the Twilio signature, resolves the shop from the `To` number, enqueues
the SPEC §9 pipeline to Celery, and returns 200 immediately (SPEC §11). Mocked
during Telegram-first testing (no real numbers); activated at Stage 13. Signature
verification is real and unit-tested now so Stage 13 is a cutover, not a build.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.db.factory import get_tenant_repo
from app.tasks.tasks import process_whatsapp_message

logger = logging.getLogger(__name__)
router = APIRouter()


def verify_twilio_signature(url: str, form: dict[str, str], signature: str | None) -> bool:
    """True iff the Twilio signature is valid for this request (SPEC §9 step 1)."""
    if not signature or not settings.twilio_auth_token:
        return False
    return RequestValidator(settings.twilio_auth_token).validate(url, form, signature)


def _strip_whatsapp(number: str) -> str:
    """Twilio sends 'whatsapp:+123'; shops.whatsapp_number stores '+123' (SPEC §1)."""
    return number.removeprefix("whatsapp:")


@router.post("/webhook/whatsapp")
async def whatsapp_inbound(request: Request) -> Response:
    form = dict(await request.form())
    if not verify_twilio_signature(str(request.url), form, request.headers.get("X-Twilio-Signature")):
        logger.warning("whatsapp webhook: bad Twilio signature")
        return Response(status_code=403)

    to = _strip_whatsapp(form.get("To", ""))
    shop = await get_tenant_repo().get_shop_by_whatsapp_number(to)
    if shop is None:
        logger.warning("whatsapp webhook: no shop for To=%s", to)
        return Response(status_code=200)  # ack silently — don't leak which numbers are live

    process_whatsapp_message.delay(
        shop_id=str(shop.id),
        identity=_strip_whatsapp(form.get("From", "")),
        body=form.get("Body", ""),
        message_sid=form.get("MessageSid"),
    )
    return Response(status_code=200)  # SPEC §11: return immediately, process in Celery
