"""Escalation lifecycle (SPEC §3; ADR-009). Supabase + Telegram faked."""

from __future__ import annotations

from uuid import uuid4

import fakeredis.aioredis
import pytest

import app.escalations.service as svc
from app.escalations.context import history, remember
from app.escalations.service import (
    DeliveryFailed,
    NoPendingEscalation,
    escalate,
    freeze,
    handover,
    is_frozen,
    reply,
)
from app.tenants.models import Shop, Shopkeeper


def _shop(name: str = "Shop 01") -> Shop:
    return Shop(
        id=uuid4(),
        client_id=uuid4(),
        name=name,
        telegram_keeper_bot_token="111:AAA",
        telegram_customer_bot_token="222:BBB",
    )


def _keeper(shop_id) -> Shopkeeper:
    return Shopkeeper(id=uuid4(), shop_id=shop_id, telegram_id=555, name="Sam")


@pytest.fixture
def redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def wire(monkeypatch) -> dict:
    """Fake every outbound edge: Supabase rows, shopkeeper Telegram, customer Telegram, owner."""
    sent: dict = {"rows": [], "resolved": [], "keepers": [], "customer": [], "owner": []}

    async def _open(shop_id, identity, message, client):
        sent["rows"].append({"shop_id": shop_id, "phone": identity, "message": message})

    async def _resolve(shop_id, identity, client):
        sent["resolved"].append((shop_id, identity))
        return 1

    async def _shopkeepers(shop_id):
        return [_keeper(shop_id)]

    async def _to_keepers(shop, keepers, text):
        sent["keepers"].append(text)
        return len(list(keepers))

    async def _to_customer(shop, identity, text):
        sent["customer"].append((identity, text))
        return True

    async def _to_owner(text):
        sent["owner"].append(text)
        return True

    monkeypatch.setattr(svc, "_open_escalation", _open)
    monkeypatch.setattr(svc, "_resolve_escalation", _resolve)
    monkeypatch.setattr(svc, "_shopkeepers", _shopkeepers)
    monkeypatch.setattr(svc, "send_to_shopkeepers", _to_keepers)
    monkeypatch.setattr(svc, "send_to_customer", _to_customer)
    monkeypatch.setattr(svc, "send_to_owner", _to_owner)
    return sent


# --- SPEC §3 steps 1, 2, 4 ---
@pytest.mark.asyncio
async def test_escalate_writes_row_freezes_and_notifies(redis, wire):
    shop = _shop()
    await escalate(redis, shop, "p1", "I want a refund", "refund request")

    assert wire["rows"][0]["phone"] == "p1"
    assert await is_frozen(redis, shop.id, "p1")
    notice = wire["keepers"][0]
    assert "Escalation from p1" in notice and "I want a refund" in notice
    assert "/reply p1" in notice and "/handover p1" in notice
    assert wire["owner"] == []  # a refund is not an owner problem


@pytest.mark.asyncio
async def test_escalate_freezes_before_notifying(redis, wire, monkeypatch):
    """If notification is slow, the customer's next message must already miss the AI."""
    shop = _shop()
    frozen_at_notify = {}

    async def _to_keepers(s, keepers, text):
        frozen_at_notify["yes"] = await is_frozen(redis, shop.id, "p1")
        return 1

    monkeypatch.setattr(svc, "send_to_shopkeepers", _to_keepers)
    await escalate(redis, shop, "p1", "hi", "reason")
    assert frozen_at_notify["yes"] is True


@pytest.mark.asyncio
async def test_unreachable_shopkeeper_pages_the_owner(redis, wire, monkeypatch):
    """The customer was promised a specialist. If nobody heard, that is an owner problem."""

    async def _nobody(shop, keepers, text):
        return 0

    monkeypatch.setattr(svc, "send_to_shopkeepers", _nobody)
    await escalate(redis, _shop(), "p1", "help", "complaint")

    assert len(wire["owner"]) == 1
    alert = wire["owner"][0]
    assert "no shopkeeper reachable" in alert.lower()
    assert "p1" in alert and "Action" in alert


# --- /reply (SPEC §3) ---
@pytest.mark.asyncio
async def test_reply_reaches_the_customer_and_is_remembered(redis, wire):
    shop = _shop()
    await freeze(redis, shop.id, "p1")
    await reply(redis, shop, "p1", "We can refund you today.")

    assert wire["customer"] == [("p1", "We can refund you today.")]
    # recorded as a real turn, so the AI knows what the human said after /handover
    assert await history(redis, shop.id, "p1") == [
        {"role": "shopkeeper", "content": "We can refund you today."}
    ]


@pytest.mark.asyncio
async def test_reply_to_a_customer_who_was_never_escalated_is_refused(redis, wire):
    with pytest.raises(NoPendingEscalation):
        await reply(redis, _shop(), "stranger", "hello")
    assert wire["customer"] == []


@pytest.mark.asyncio
async def test_shop_b_cannot_reply_to_shop_a_escalation(redis, wire):
    """The customer id is shopkeeper free text. Freeze state is per-shop; so is /reply."""
    shop_a, shop_b = _shop("A"), _shop("B")
    await freeze(redis, shop_a.id, "p1")

    with pytest.raises(NoPendingEscalation):
        await reply(redis, shop_b, "p1", "hijack attempt")
    assert wire["customer"] == []


@pytest.mark.asyncio
async def test_undeliverable_reply_raises_and_is_not_remembered(redis, wire, monkeypatch):
    async def _fails(shop, identity, text):
        return False

    monkeypatch.setattr(svc, "send_to_customer", _fails)
    shop = _shop()
    await freeze(redis, shop.id, "p1")

    with pytest.raises(DeliveryFailed):
        await reply(redis, shop, "p1", "hello?")
    assert await history(redis, shop.id, "p1") == []  # never claim we said something we didn't


# --- /handover (SPEC §3) ---
@pytest.mark.asyncio
async def test_handover_unfreezes_resolves_and_keeps_the_history(redis, wire):
    shop = _shop()
    await escalate(redis, shop, "p1", "refund?", "refund request")
    await remember(redis, shop.id, "p1", "shopkeeper", "Sorted, anything else?")

    await handover(redis, shop, "p1")

    assert not await is_frozen(redis, shop.id, "p1")
    assert wire["resolved"] == [(shop.id, "p1")]
    # "resumes with full Redis context" — nothing was restored, because nothing was lost
    assert [t["content"] for t in await history(redis, shop.id, "p1")] == ["Sorted, anything else?"]


@pytest.mark.asyncio
async def test_handover_for_an_unfrozen_customer_is_refused(redis, wire):
    with pytest.raises(NoPendingEscalation):
        await handover(redis, _shop(), "p1")
    assert wire["resolved"] == []


@pytest.mark.asyncio
async def test_shop_b_cannot_hand_over_shop_a_escalation(redis, wire):
    shop_a, shop_b = _shop("A"), _shop("B")
    await freeze(redis, shop_a.id, "p1")

    with pytest.raises(NoPendingEscalation):
        await handover(redis, shop_b, "p1")
    assert await is_frozen(redis, shop_a.id, "p1")  # untouched
