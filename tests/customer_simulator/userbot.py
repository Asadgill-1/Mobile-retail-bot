"""Telethon userbot skeleton — drives the bots as real Telegram users (ADR-005).

NOT unit-tested here: needs real Telegram phone-login sessions (`.session` files).
Build out in Stage 3 once the WhatsApp/webhook path exists to inject into.

Usage (after one-time `login.py` per account):
    ub = Userbot("tests/customer_simulator/sessions/account_a.session")
    await ub.send("@mscbot_shop1", "good camera phone under 1200 AED")
"""

from __future__ import annotations

from typing import Any


class Userbot:
    """Thin wrapper over a Telethon client (real phone-number session)."""  # ponytail: skeleton, untested without real .session files. ceiling: no e2e customer flow. upgrade: Stage 3 build-out + login.py.

    def __init__(self, session_path: str, api_id: int = 0, api_hash: str = "") -> None:
        self.session_path = session_path
        self.api_id = api_id
        self.api_hash = api_hash
        self._client: Any = None  # TelegramClient, created lazily

    async def __aenter__(self) -> "Userbot":
        from telethon import TelegramClient  # local import; dev-only dep

        self._client = TelegramClient(self.session_path, self.api_id, self.api_hash)
        await self._client.connect()
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._client is not None:
            await self._client.disconnect()

    async def send(self, bot_username: str, text: str) -> None:
        if self._client is None:
            raise RuntimeError("Userbot not started (use `async with Userbot(...)`)")
        await self._client.send_message(bot_username, text)
