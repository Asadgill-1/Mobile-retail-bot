"""WhatsApp webhook tests (SPEC §9 step 1, §11; ADR-002). Twilio + Celery mocked."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

import app.whatsapp.webhook as webhook
from app.tenants.models import Shop


# --- pure signature verification (the ADR-002 value tested early) ---
def test_verify_twilio_signature(monkeypatch):
    token = "test-token"
    monkeypatch.setattr(webhook.settings, "twilio_auth_token", token)
    url = "https://svc.example/webhook/whatsapp"
    form = {"To": "whatsapp:+10000000001", "Body": "hi"}
    good = RequestValidator(token).compute_signature(url, form)
    assert webhook.verify_twilio_signature(url, form, good) is True
    assert webhook.verify_twilio_signature(url, form, "wrong") is False
    assert webhook.verify_twilio_signature(url, form, None) is False


def test_verify_rejects_when_no_token(monkeypatch):
    monkeypatch.setattr(webhook.settings, "twilio_auth_token", "")
    assert webhook.verify_twilio_signature("u", {}, "sig") is False


# --- route behavior ---
class _FakeRepo:
    def __init__(self, shop: Shop | None) -> None:
        self._shop = shop

    async def get_shop_by_whatsapp_number(self, number: str) -> Shop | None:
        return self._shop


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def _shop(channel: str = "whatsapp") -> Shop:
    return Shop(
        id=uuid4(),
        client_id=uuid4(),
        name="Shop 01",
        whatsapp_number="+10000000001",
        customer_channel=channel,
    )


@pytest.fixture
def delivered(monkeypatch) -> MagicMock:
    """Spy on the hand-off to the paced worker (which replaced the Celery enqueue)."""
    spy = MagicMock()
    monkeypatch.setattr(webhook, "_deliver", spy)
    return spy


def test_bad_signature_returns_403(client, monkeypatch, delivered):
    monkeypatch.setattr(webhook, "verify_twilio_signature", lambda *a: False)
    r = client.post("/webhook/whatsapp", data={"To": "whatsapp:+10000000001", "Body": "hi"})
    assert r.status_code == 403
    delivered.assert_not_called()


def test_known_shop_is_handed_to_its_paced_worker(client, monkeypatch, delivered):
    shop = _shop()
    monkeypatch.setattr(webhook, "verify_twilio_signature", lambda *a: True)
    monkeypatch.setattr(webhook, "get_tenant_repo", lambda: _FakeRepo(shop))
    r = client.post(
        "/webhook/whatsapp",
        data={
            "To": "whatsapp:+10000000001",
            "From": "whatsapp:+19999999999",
            "Body": "hi",
            "MessageSid": "SM1",
        },
    )
    assert r.status_code == 200
    delivered.assert_called_once()
    got_shop, identity, text = delivered.call_args.args
    assert got_shop.id == shop.id
    assert identity == "+19999999999"  # whatsapp: prefix stripped
    assert text == "hi"


def test_a_shop_still_on_telegram_is_not_drivable_through_this_endpoint(client, monkeypatch, delivered):
    """Tenant safety, not tidiness: a Telegram shop's customers are identified by Telegram user id,
    so accepting a phone number here would open a conversation under a stranger's identity."""
    monkeypatch.setattr(webhook, "verify_twilio_signature", lambda *a: True)
    monkeypatch.setattr(webhook, "get_tenant_repo", lambda: _FakeRepo(_shop(channel="telegram")))
    r = client.post(
        "/webhook/whatsapp",
        data={"To": "whatsapp:+10000000001", "From": "whatsapp:+19999999999", "Body": "hi"},
    )
    assert r.status_code == 200  # ack, but nothing happens
    delivered.assert_not_called()


def test_unknown_shop_returns_200_without_delivering(client, monkeypatch, delivered):
    monkeypatch.setattr(webhook, "verify_twilio_signature", lambda *a: True)
    monkeypatch.setattr(webhook, "get_tenant_repo", lambda: _FakeRepo(None))
    r = client.post("/webhook/whatsapp", data={"To": "whatsapp:+1404", "Body": "hi"})
    assert r.status_code == 200
    delivered.assert_not_called()


# --- Meta Cloud API path (same URL, different shape) ---
def _meta_body(phone_id: str = "PID1", text: str = "hi") -> bytes:
    import json as _json

    return _json.dumps({"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": phone_id},
        "messages": [{"id": "w1", "from": "971500000000", "type": "text", "text": {"body": text}}],
    }}]}]}).encode()


class _PhoneIdRepo:
    def __init__(self, shop: Shop | None) -> None:
        self._shop = shop

    async def get_shop_by_whatsapp_phone_id(self, phone_id: str) -> Shop | None:
        return self._shop


@pytest.fixture
def meta_secret(monkeypatch):
    """Both the signature check and the GET handshake read the app secret from platform_settings."""
    import app.whatsapp.client as wa_client

    async def _secret():
        return "app-secret"

    monkeypatch.setattr(wa_client, "app_secret", _secret)
    return "app-secret"


def _sig(body: bytes, secret: str) -> str:
    import hashlib
    import hmac

    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_meta_message_is_routed_by_phone_number_id(client, monkeypatch, delivered, meta_secret):
    shop = _shop()
    monkeypatch.setattr(webhook, "get_tenant_repo", lambda: _PhoneIdRepo(shop))
    body = _meta_body()
    r = client.post(
        "/webhook/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": _sig(body, meta_secret), "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    delivered.assert_called_once()
    got_shop, identity, text = delivered.call_args.args
    assert got_shop.id == shop.id and identity == "971500000000" and text == "hi"


def test_a_forged_meta_signature_is_rejected(client, monkeypatch, delivered, meta_secret):
    monkeypatch.setattr(webhook, "get_tenant_repo", lambda: _PhoneIdRepo(_shop()))
    body = _meta_body()
    r = client.post(
        "/webhook/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": _sig(body, "wrong-secret"), "Content-Type": "application/json"},
    )
    assert r.status_code == 403
    delivered.assert_not_called()


def test_meta_verification_handshake_echoes_the_challenge(client, meta_secret):
    r = client.get(
        "/webhook/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": meta_secret, "hub.challenge": "12345"},
    )
    assert r.status_code == 200 and r.text == "12345"


def test_the_handshake_rejects_a_wrong_verify_token(client, meta_secret):
    r = client.get(
        "/webhook/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "12345"},
    )
    assert r.status_code == 403
