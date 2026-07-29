"""The staff↔customer bridge, and the 24h service window.

Only the customer moves to WhatsApp — keeper, shop-owner, rider and platform-owner bots stay on
Telegram forever. So after the cutover every staff action that reaches a customer crosses channels:
a shopkeeper on Telegram approving a price, answering someone on WhatsApp.

These tests assert a POSITIVE send on the right channel, never merely "no exception". A silent
failure here means a customer is told nothing after being promised a price, which is worse than a
visible error and is exactly what an over-tolerant test would wave through.
"""

from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

import fakeredis.aioredis
import pytest

import app.messaging.channel as channel
from app.messaging.channel import (
    SERVICE_WINDOW_SECONDS,
    channel_for,
    last_inbound_key,
    note_inbound,
    within_service_window,
)
from app.tenants.models import Shop


def _shop(customer_channel: str = "telegram") -> Shop:
    return Shop(
        id=uuid4(), client_id=uuid4(), name="Shop 01",
        customer_channel=customer_channel, telegram_customer_bot_token="0:test",
    )


@pytest.fixture
def redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


# --- which channel a shop is on ---
def test_a_shop_defaults_to_telegram():
    assert channel_for(_shop()).name == "telegram"


def test_a_switched_shop_uses_whatsapp():
    assert channel_for(_shop("whatsapp")).name == "whatsapp"


def test_a_junk_channel_keeps_answering_customers():
    """Fail toward the channel that works. A shop whose column is somehow wrong should still be
    talking to its customers, not silently mute."""
    assert channel_for(_shop("carrier-pigeon")).name == "telegram"


# --- the bridge: staff on Telegram, customer wherever they are ---
@pytest.mark.asyncio
async def test_every_staff_to_customer_path_follows_the_customers_channel(monkeypatch):
    """The five things staff send a customer — escalation reply, price approved, price declined,
    order confirmed, rider update — all route through send_to_customer, so one dispatch covers
    them. This pins that they leave on the WhatsApp channel and NOT via Telegram."""
    from app.telegram_bot import notify

    telegram_sends: list[str] = []
    whatsapp_sends: list[str] = []

    async def _telegram(token, chat_id, text, *, what, reply_markup=None):
        telegram_sends.append(text)
        return True

    async def _whatsapp(shop, identity, text):
        whatsapp_sends.append(text)
        return True

    monkeypatch.setattr(notify, "_send", _telegram)
    monkeypatch.setattr("app.whatsapp.client.send_text", _whatsapp)

    shop = _shop("whatsapp")
    for message in (
        "Good news, the shop accepted 1269 AED.",
        "Sorry, the shop can't go that low.",
        "Your order is confirmed.",
        "Your order is out for delivery.",
        "Delivered — thanks for shopping with us.",
    ):
        assert await notify.send_to_customer(shop, "+971500000000", message) is True

    assert whatsapp_sends == [
        "Good news, the shop accepted 1269 AED.",
        "Sorry, the shop can't go that low.",
        "Your order is confirmed.",
        "Your order is out for delivery.",
        "Delivered — thanks for shopping with us.",
    ]
    assert telegram_sends == [], "a WhatsApp customer must never be messaged over Telegram"


@pytest.mark.asyncio
async def test_a_telegram_shop_is_untouched_by_the_seam(monkeypatch):
    """The cutover must not change a shop that has not been switched."""
    from app.telegram_bot import notify

    sent: list[tuple] = []

    async def _telegram(token, chat_id, text, *, what, reply_markup=None):
        sent.append((token, chat_id, text))
        return True

    monkeypatch.setattr(notify, "_send", _telegram)
    shop = _shop()

    assert await notify.send_to_customer(shop, "5215780245", "your order is confirmed") is True
    assert sent == [("0:test", "5215780245", "your order is confirmed")]


@pytest.mark.asyncio
async def test_a_shop_photo_follows_the_channel_too(monkeypatch):
    """send_photo_to_customer used to go straight to Telegram. It takes raw bytes because a
    Telegram file_id is bound to the bot that received it, and WhatsApp needs an upload, so it
    cannot be left behind when the shop switches."""
    from app.telegram_bot import notify

    uploaded: list[bytes] = []

    async def _whatsapp_photo(shop, identity, photo, caption=None):
        uploaded.append(photo)
        return True

    monkeypatch.setattr("app.whatsapp.client.send_photo", _whatsapp_photo)

    ok = await notify.send_photo_to_customer(_shop("whatsapp"), "+971500000000", b"jpegbytes")
    assert ok is True and uploaded == [b"jpegbytes"]


# --- the 24h service window ---
@pytest.mark.asyncio
async def test_a_customer_who_just_wrote_is_inside_the_window(redis):
    shop_id = uuid4()
    await note_inbound(redis, shop_id, "+971500000000")
    assert await within_service_window(redis, shop_id, "+971500000000") is True


@pytest.mark.asyncio
async def test_a_customer_who_wrote_days_ago_is_outside_it(redis):
    """The shopkeeper-approves-a-price-next-morning case."""
    shop_id = uuid4()
    stale = int(time.time()) - SERVICE_WINDOW_SECONDS - 60
    await redis.set(last_inbound_key(shop_id, "+971500000000"), str(stale))
    assert await within_service_window(redis, shop_id, "+971500000000") is False


@pytest.mark.asyncio
async def test_never_having_heard_from_them_counts_as_outside(redis):
    """Fail closed. Assuming we may write freely is the mistake that loses the message silently —
    the provider accepts the call and drops it, which reads as success."""
    assert await within_service_window(redis, uuid4(), "+971500000000") is False


@pytest.mark.asyncio
async def test_a_corrupt_stamp_does_not_crash_the_send(redis):
    shop_id = uuid4()
    await redis.set(last_inbound_key(shop_id, "+971500000000"), "not-a-timestamp")
    assert await within_service_window(redis, shop_id, "+971500000000") is False


@pytest.mark.asyncio
async def test_the_window_is_per_shop(redis):
    """Writing to one shop does not license the other 29 to message someone."""
    a, b = uuid4(), uuid4()
    await note_inbound(redis, a, "+971500000000")
    assert await within_service_window(redis, a, "+971500000000") is True
    assert await within_service_window(redis, b, "+971500000000") is False


def test_no_customer_send_bypasses_the_channel_seam():
    """The cutover footgun, pinned.

    `send_to_customer` / `send_photo_to_customer` are the only two functions that know which channel
    a shop's customers are on. A staff-side path that reaches for `Bot(token).send_message` directly
    still works today — every shop is on Telegram — and goes permanently silent the moment that shop
    is switched to WhatsApp, with no error anywhere. Grep-level, because the failure is the ABSENCE
    of a call: no fixture can catch a path that was never wired up.
    """
    src = Path(__file__).resolve().parents[2] / "src" / "app"
    allowed = {Path("telegram_bot") / "notify.py", Path("telegram_bot") / "bot.py"}

    offenders = []
    for path in src.rglob("*.py"):
        rel = path.relative_to(src)
        if rel in allowed or rel.parts[0] == "whatsapp":
            continue
        body = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(body.splitlines(), 1):
            if "Bot(" in line and (".send_message" in line or ".send_photo" in line):
                offenders.append(f"{rel}:{lineno}")

    assert offenders == [], (
        "these send to a chat directly instead of through the channel seam, so they will go silent "
        f"for any shop switched to WhatsApp: {offenders}"
    )


def test_every_customer_notification_goes_through_one_of_the_two_senders():
    """Counts the choke-point call sites so a new customer-facing message can't be added without
    someone noticing it has to work cross-channel. Update the number when you add a real one."""
    src = Path(__file__).resolve().parents[2] / "src" / "app"
    callers = set()
    for path in src.rglob("*.py"):
        if path.name == "notify.py":
            continue
        body = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(body.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", '"', "'")) or "import" in stripped:
                continue
            if "send_to_customer(" in line or "send_photo_to_customer(" in line:
                callers.add(f"{path.relative_to(src)}:{lineno}")

    # escalation reply + escalation photo, price approved, price declined, order confirmed,
    # delivery update, rider out-for-delivery, rider delivered.
    assert len(callers) == 8, f"customer-facing sends changed: {sorted(callers)}"


@pytest.mark.asyncio
async def test_the_pipeline_stamps_the_window_on_every_inbound(redis, monkeypatch):
    """Including for a blacklisted customer: the stamp records that THEY wrote, which is a fact
    about them, not a decision about whether we answer."""
    import app.messaging.pipeline as pipeline
    from app.messaging.pipeline import InboundMessage, process_message

    async def _answer(shop, identity, text, redis_, media_sink=None, usage_sink=None):
        return "ok"

    monkeypatch.setattr(pipeline, "answer_customer", _answer)
    shop = _shop()

    await process_message(InboundMessage(shop, "p1", "hi"), redis)

    assert await within_service_window(redis, shop.id, "p1") is True
