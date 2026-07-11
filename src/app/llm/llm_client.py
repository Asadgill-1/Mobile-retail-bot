"""Provider-agnostic LLM client (ADR-004).

One async interface for chat + tool-calling. The provider (Moonshot for testing,
OpenAI GPT-4o for prod) is selected by env, via the OpenAI-compatible API surface
both providers share. No call site branches on provider name.

Stage 4: real function-calling via AsyncOpenAI pointed at `settings.ai_base_url`.
Transport-level retry-once lives here (SPEC §11); the user-facing fallback message
is composed by `ai/orchestrator.py`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class LLMMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
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
    return LLMResponse(
        content=choice.message.content,
        tool_calls=calls,
        finish_reason=choice.finish_reason or "",
        raw=raw,
    )


class LLMClient:
    """Thin async wrapper over an OpenAI-compatible Chat Completions API."""

    def __init__(self) -> None:
        self.provider = settings.ai_provider
        self.base_url = settings.ai_base_url
        self.model = settings.ai_model
        self.api_key = settings.ai_api_key
        self.temperature = settings.ai_temperature
        self.max_tokens = settings.ai_max_tokens
        self.timeout = settings.ai_request_timeout
        self._http: Any = None

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
    ) -> LLMResponse:
        """Chat completion with optional tool-calling. Retries once on error (SPEC §11)."""
        payload: dict[str, Any] = {
            "model": self.model,
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
