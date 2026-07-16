"""Application settings, env-driven via pydantic-settings.

All secrets/configuration are read from environment variables (see `.env.example`).
Nothing is hardcoded. See ADR-001 (stack) and ADR-004 (LLM provider abstraction).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration. Loaded once via `get_settings()`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_name: str = "multi-shop-chatbot"
    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    internal_api_key: str = Field(..., description="Required X-Internal-API-Key for internal endpoints")

    # --- Supabase (ADR-001, ADR-003) ---
    supabase_url: str = Field(..., description="Supabase project URL")
    supabase_service_role_key: str = Field(..., description="Service-role JWT (RLS bypass) — backend only, never exposed")
    supabase_anon_key: str = Field("", description="Anon JWT (client-side, limited)")
    supabase_storage_bucket: str = "shop-media"
    supabase_reports_bucket: str = "shop-reports"  # Excel exports (SPEC §10)
    supabase_mgmt_token: str = Field("", description="Account-level PAT (sbp_...) for Management API / migrations")

    # --- Redis (ADR-001) ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Telegram (ADR-002; testing uses Telegram-first) ---
    # ADR-005: owner control bot + per-shop (shopkeeper bot + customer bot).
    # `telegram_bot_token` = the OWNER control bot (admin commands).
    telegram_bot_token: str = Field(..., description="OWNER control bot token (admin commands)")
    owner_telegram_id: int = Field(..., description="Owner's Telegram user ID (owner-only commands)")
    # One global rider bot (like the owner bot): riders across all shops link their Telegram to it
    # and receive delivery assignments. Empty → rider bot not run (feature off).
    telegram_rider_bot_token: str = Field("", description="Global rider bot token (delivery assignments)")
    # One global shop-owner bot (like the rider bot): client owners link via contact share and see
    # their shops' orders/inventory/reports/messages remotely. Empty → shop-owner bot not run.
    telegram_shopowner_bot_token: str = Field("", description="Global shop-owner bot token (client reports)")
    telegram_webhook_secret: str = Field("", description="Optional webhook secret token")
    # JSON list of per-shop bots: [{shop_key, keeper_token, customer_token, customer_chat_id}, ...].
    # Used by the InMemoryTenantRepo seed (tests/offline). Live runs read tokens from the
    # shops.telegram_*_bot_token columns instead (seeded via scripts/seed_shop_bots.py).
    # ponytail: env JSON is the test/offline path only. ceiling: live onboarding flow not built yet.
    # upgrade: owner onboarding command (Stage 5+) writes tokens into shops rows directly.
    telegram_shop_bots_json: str = Field("", description="JSON list of per-shop bot tokens (test/offline)")

    @property
    def shop_bots(self) -> list[dict[str, Any]]:
        """Parse TELEGRAM_SHOP_BOTS_JSON. Empty list when unset (e.g. unit tests)."""
        raw = self.telegram_shop_bots_json.strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"TELEGRAM_SHOP_BOTS_JSON is not valid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise RuntimeError("TELEGRAM_SHOP_BOTS_JSON must be a JSON list")
        return data

    # --- Twilio / WhatsApp (ADR-002; activated at Stage 13) ---
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_default_whatsapp_from: str = ""  # default sender; per-shop numbers live in `shops.whatsapp_number`
    whatsapp_webhook_base_url: str = ""  # public URL Twilio forwards to: {base}/webhook/whatsapp

    # --- LLM provider (ADR-004 rev.2) ---
    # Testing: "moonshot" = official Moonshot API, direct. Production: "openai" (GPT-4o).
    # "openrouter" remains supported as an aggregator fallback.
    ai_provider: Literal["moonshot", "openai", "openrouter"] = "moonshot"
    ai_base_url: str = "https://api.moonshot.ai/v1"  # GLOBAL endpoint; .cn rejects global keys
    ai_api_key: str = Field(..., description="Moonshot (testing) / OpenAI (prod) / OpenRouter (alt) API key")
    ai_model: str = "kimi-k2.6"  # kimi-k2.7-code* are code-specialised — not for customer chat
    # kimi-k2.* rejects any temperature except 1 (HTTP 400). Defaults must match the default
    # model, or every message 400s, retries once, and degrades to FALLBACK_REPLY. GPT-4o: ~0.2.
    ai_temperature: float = 1.0
    ai_max_tokens: int = 1024
    ai_request_timeout: float = 30.0
    # Reads hand-filled counter-sale sheets from a photo (shop-owner bot 🧾 Today sell). Chat
    # stays on ai_model — only that one flow overrides it. Verified live against the provider's
    # model list (8k/32k/128k vision-preview exist); 32k fits a sheet without paying for 128k.
    ai_vision_model: str = "moonshot-v1-32k-vision-preview"
    # Per-customer daily cap on AI-answered messages (cost/abuse ceiling; 0 disables). Far above any
    # real customer — rapid-fire (20/60s) catches bursts, this catches a sustained flood under it.
    ai_daily_msg_cap: int = 1000

    # --- Celery / Flower (ADR-001; §13) ---
    celery_broker_url: str = ""  # defaults to redis_url if empty
    celery_result_backend: str = ""
    flower_user: str = "admin"
    flower_password: str = Field(..., description="Flower basic-auth password")

    # --- Runtime toggles ---
    telegram_use_webhook: bool = False  # False = long-polling (testing), ADR-002
    whatsapp_mocked: bool = True  # True during testing; flipped False at Stage 13

    @property
    def celery_broker(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def celery_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
