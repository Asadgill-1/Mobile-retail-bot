"""Runtime AI config overlay (migration 024): the platform-owner console switches provider/model
without a restart. Env stays the floor — clearing a console setting reverts to it."""

from __future__ import annotations

import pytest

from app.llm.llm_client import LLMClient


def _client(overlay: dict[str, str]) -> LLMClient:
    c = LLMClient()
    c._read_overlay = lambda: overlay  # type: ignore[method-assign]
    return c


def test_env_is_used_when_the_console_has_set_nothing():
    c = _client({})
    assert c.model == c._env["ai_model"]
    assert c.base_url == c._env["ai_base_url"]


@pytest.mark.asyncio
async def test_console_overlay_switches_provider_model_and_key():
    c = _client({
        "ai_provider": "openrouter",
        "ai_base_url": "https://openrouter.ai/api/v1",
        "ai_model": "anthropic/claude-sonnet-4",
        "ai_api_key": "sk-or-test",
    })
    await c._sync_overlay()

    assert c.provider == "openrouter"
    assert c.base_url == "https://openrouter.ai/api/v1"
    assert c.model == "anthropic/claude-sonnet-4"
    assert c.api_key == "sk-or-test"


@pytest.mark.asyncio
async def test_changing_the_endpoint_rebuilds_the_http_client():
    c = _client({"ai_base_url": "https://openrouter.ai/api/v1", "ai_api_key": "sk-or-test"})
    c._http = object()  # pretend a client was already built against the old endpoint
    await c._sync_overlay()
    assert c._http is None  # stale connection dropped, will rebuild on next call


@pytest.mark.asyncio
async def test_model_only_switch_keeps_the_connection():
    c = _client({"ai_model": "kimi-k2.7"})
    sentinel = object()
    c._http = sentinel
    await c._sync_overlay()
    assert c.model == "kimi-k2.7"
    assert c._http is sentinel  # same endpoint+key → no needless reconnect


@pytest.mark.asyncio
async def test_clearing_a_console_setting_reverts_to_env():
    c = _client({"ai_model": "kimi-k2.7"})
    await c._sync_overlay()
    assert c.model == "kimi-k2.7"

    c._read_overlay = lambda: {}  # type: ignore[method-assign]
    c._overlay_at = 0.0           # allow an immediate re-check
    await c._sync_overlay()
    assert c.model == c._env["ai_model"]  # env is the floor, not a one-way door


@pytest.mark.asyncio
async def test_a_settings_outage_keeps_the_last_good_config():
    c = _client({"ai_model": "kimi-k2.7"})
    await c._sync_overlay()

    def _boom():
        raise RuntimeError("supabase down")

    c._read_overlay = _boom  # type: ignore[method-assign]
    c._overlay_at = 0.0
    await c._sync_overlay()  # must not raise — the AI keeps answering

    assert c.model == "kimi-k2.7"


@pytest.mark.asyncio
async def test_overlay_is_not_re_read_on_every_message():
    calls = {"n": 0}

    def _count():
        calls["n"] += 1
        return {}

    c = LLMClient()
    c._read_overlay = _count  # type: ignore[method-assign]
    c._overlay_at = 0.0
    await c._sync_overlay()
    await c._sync_overlay()
    await c._sync_overlay()
    assert calls["n"] == 1  # TTL-gated: one DB read per minute per process, not per message
