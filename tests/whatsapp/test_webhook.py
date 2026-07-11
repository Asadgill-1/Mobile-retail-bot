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


def _shop() -> Shop:
    return Shop(id=uuid4(), client_id=uuid4(), name="Shop 01", whatsapp_number="+10000000001")


def test_bad_signature_returns_403(client, monkeypatch):
    monkeypatch.setattr(webhook, "verify_twilio_signature", lambda *a: False)
    delay = MagicMock()
    monkeypatch.setattr(webhook.process_whatsapp_message, "delay", delay)
    r = client.post("/webhook/whatsapp", data={"To": "whatsapp:+10000000001", "Body": "hi"})
    assert r.status_code == 403
    delay.assert_not_called()


def test_known_shop_enqueues_and_returns_200(client, monkeypatch):
    shop = _shop()
    monkeypatch.setattr(webhook, "verify_twilio_signature", lambda *a: True)
    monkeypatch.setattr(webhook, "get_tenant_repo", lambda: _FakeRepo(shop))
    delay = MagicMock()
    monkeypatch.setattr(webhook.process_whatsapp_message, "delay", delay)
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
    delay.assert_called_once()
    kw = delay.call_args.kwargs
    assert kw["shop_id"] == str(shop.id)
    assert kw["identity"] == "+19999999999"  # whatsapp: prefix stripped
    assert kw["message_sid"] == "SM1"


def test_unknown_shop_returns_200_without_enqueue(client, monkeypatch):
    monkeypatch.setattr(webhook, "verify_twilio_signature", lambda *a: True)
    monkeypatch.setattr(webhook, "get_tenant_repo", lambda: _FakeRepo(None))
    delay = MagicMock()
    monkeypatch.setattr(webhook.process_whatsapp_message, "delay", delay)
    r = client.post("/webhook/whatsapp", data={"To": "whatsapp:+1404", "Body": "hi"})
    assert r.status_code == 200
    delay.assert_not_called()
