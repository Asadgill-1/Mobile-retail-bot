"""Provider-agnostic LLM client (ADR-004).

One async interface for chat + tool-calling. The provider (Moonshot for testing,
OpenAI GPT-4o for prod) is selected by env, via the OpenAI-compatible API surface
both providers share. No call site branches on provider name.

Stage 4: real function-calling via AsyncOpenAI pointed at `settings.ai_base_url`.
Transport-level retry-once lives here (SPEC §11); the user-facing fallback message
is composed by `ai/orchestrator.py`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class LLMMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    # str for ordinary chat; a list of OpenAI content-parts for vision
    # ([{"type": "text", ...}, {"type": "image_url", ...}]). `_to_wire` passes either through
    # untouched, so the wire format is already correct for both.
    content: str | list[dict[str, Any]]
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


@dataclass
class LLMToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    finish_reason: str = ""
    raw: Any = None
    tokens_in: int = 0  # prompt tokens billed for this call (0 when the provider omits usage)
    tokens_out: int = 0


def _to_wire(m: LLMMessage) -> dict[str, Any]:
    """LLMMessage → OpenAI chat message dict."""
    wire: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.name:
        wire["name"] = m.name
    if m.tool_call_id:
        wire["tool_call_id"] = m.tool_call_id
    if m.tool_calls:
        wire["tool_calls"] = m.tool_calls
    return wire


def _to_response(raw: Any) -> LLMResponse:
    """OpenAI completion → LLMResponse. Malformed tool arguments degrade to `{}`."""
    choice = raw.choices[0]
    calls = []
    for tc in choice.message.tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            logger.warning("LLM returned unparsable tool arguments: %r", tc.function.arguments)
            args = {}
        calls.append(LLMToolCall(id=tc.id, name=tc.function.name, arguments=args))
    usage = getattr(raw, "usage", None)
    return LLMResponse(
        content=choice.message.content,
        tool_calls=calls,
        finish_reason=choice.finish_reason or "",
        raw=raw,
        tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
        tokens_out=getattr(usage, "completion_tokens", 0) or 0,
    )


# Runtime config overlay (migration 024): the platform-owner console writes ai_provider /
# ai_base_url / ai_model / ai_api_key into `platform_settings`; env stays the fallback for any
# key the console hasn't set. Checked at most once per TTL, off the event loop — a model switch
# reaches every bot/worker process within ~a minute, no restart.
_OVERLAY_KEYS = ("ai_provider", "ai_base_url", "ai_model", "ai_api_key")
_OVERLAY_TTL_SECONDS = 60.0


class LLMClient:
    """Thin async wrapper over an OpenAI-compatible Chat Completions API."""

    def __init__(self) -> None:
        # env values are the floor; the console overlay may replace any of them at runtime
        self._env = {
            "ai_provider": settings.ai_provider,
            "ai_base_url": settings.ai_base_url,
            "ai_model": settings.ai_model,
            "ai_api_key": settings.ai_api_key,
        }
        self.temperature = settings.ai_temperature
        self.max_tokens = settings.ai_max_tokens
        self.timeout = settings.ai_request_timeout
        self._http: Any = None
        self._overlay_at = 0.0
        self._apply({})

    def _apply(self, overlay: dict[str, str]) -> None:
        """Effective config = overlay value if set, else env. Rebuilds the HTTP client when the
        connection identity (base_url/api_key) changed — a model-only switch reuses it."""
        eff = {k: (overlay.get(k) or self._env[k]) for k in _OVERLAY_KEYS}
        if eff["ai_base_url"] != getattr(self, "base_url", None) or eff["ai_api_key"] != getattr(
            self, "api_key", None
        ):
            self._http = None
        self.provider = eff["ai_provider"]
        self.base_url = eff["ai_base_url"]
        self.model = eff["ai_model"]
        self.api_key = eff["ai_api_key"]

    def _read_overlay(self) -> dict[str, str]:
        """Sync Supabase read (called via to_thread). Values are jsonb scalars."""
        from app.db.supabase_client import get_supabase

        rows = (
            get_supabase().table("platform_settings").select("key,value")
            .in_("key", list(_OVERLAY_KEYS)).execute().data or []
        )
        out: dict[str, str] = {}
        for r in rows:
            v = r.get("value")
            if isinstance(v, str) and v.strip():
                out[r["key"]] = v.strip()
        return out

    async def _sync_overlay(self) -> None:
        """Refresh the console overlay at most once per TTL. Best-effort: a settings-table
        outage must never stop the AI answering — we keep whatever config we already have."""
        now = time.monotonic()
        if now - self._overlay_at < _OVERLAY_TTL_SECONDS:
            return
        self._overlay_at = now  # stamp first: a failing read must not retry on every message
        try:
            self._apply(await asyncio.to_thread(self._read_overlay))
        except Exception:
            logger.warning("platform_settings overlay read failed; keeping current config", exc_info=True)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def _client(self) -> Any:
        """Lazily build the AsyncOpenAI client (works for OpenAI/Moonshot/OpenRouter)."""
        if self._http is None:
            from openai import AsyncOpenAI

            self._http = AsyncOpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
            )
        return self._http

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Chat completion with optional tool-calling. Retries once on error (SPEC §11).

        `model` overrides the configured chat model for this call — the vision model reads
        counter-sale sheets; everything else stays on the chat model.
        """
        await self._sync_overlay()  # console-set provider/model land here (migration 024)
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [_to_wire(m) for m in messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

        client = self._client()
        try:
            return _to_response(await client.chat.completions.create(**payload))
        except Exception:
            logger.warning("LLM call failed; retrying once (SPEC §11)", exc_info=True)
            return _to_response(await client.chat.completions.create(**payload))


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Return a process-wide singleton LLMClient."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
