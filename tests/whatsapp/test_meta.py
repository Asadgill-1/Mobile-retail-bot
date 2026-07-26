"""Meta Cloud API: signature verification and the inbound envelope.

Both are load-bearing in a way the send path is not. The signature is the only thing between the
pipeline and the open internet, and the envelope is deeply nested and batched — a status callback
that got mistaken for a customer writing in would open conversations nobody started.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from app.whatsapp.meta import parse_inbound, verify_signature

SECRET = "app-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_a_correct_signature_passes():
    body = b'{"entry":[]}'
    assert verify_signature(SECRET, body, _sign(body)) is True


def test_a_tampered_body_fails():
    assert verify_signature(SECRET, b'{"entry":[1]}', _sign(b'{"entry":[]}')) is False


def test_a_signature_from_the_wrong_secret_fails():
    body = b'{"entry":[]}'
    assert verify_signature(SECRET, body, _sign(body, "someone-elses-secret")) is False


def test_missing_or_malformed_headers_fail_closed():
    body = b"{}"
    assert verify_signature(SECRET, body, None) is False
    assert verify_signature(SECRET, body, "") is False
    assert verify_signature(SECRET, body, "md5=abc") is False       # wrong algorithm prefix
    assert verify_signature("", body, _sign(body)) is False          # no secret configured


def _envelope(messages: list[dict], phone_id: str = "PID1") -> dict:
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": phone_id},
        "messages": messages,
    }}]}]}


def test_a_text_message_is_extracted_with_its_routing_id():
    got = parse_inbound(_envelope([
        {"id": "wamid.1", "from": "971500000000", "type": "text", "text": {"body": "do you have it"}}
    ]))
    assert got == [{
        "phone_id": "PID1", "from": "971500000000",
        "text": "do you have it", "message_id": "wamid.1",
    }]


def test_a_delivery_status_callback_is_not_a_customer_message():
    """These arrive constantly. Treating one as inbound would start a conversation nobody had."""
    assert parse_inbound({"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "PID1"},
        "statuses": [{"id": "wamid.1", "status": "delivered"}],
    }}]}]}) == []


def test_non_text_messages_are_skipped_not_answered_blankly():
    assert parse_inbound(_envelope([
        {"id": "w1", "from": "971500000000", "type": "image", "image": {"id": "i1"}}
    ])) == []


def test_a_batch_yields_every_message():
    """Meta batches; answering only the first would silently drop the rest."""
    got = parse_inbound(_envelope([
        {"id": "w1", "from": "971500000001", "type": "text", "text": {"body": "one"}},
        {"id": "w2", "from": "971500000002", "type": "text", "text": {"body": "two"}},
    ]))
    assert [m["text"] for m in got] == ["one", "two"]


def test_junk_payloads_do_not_raise():
    for payload in ({}, {"entry": None}, {"entry": [{}]}, {"entry": [{"changes": [{}]}]}):
        assert parse_inbound(payload) == []
