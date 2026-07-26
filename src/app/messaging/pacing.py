"""Make the customer bot answer like a person types, not like a server responds.

Three separate problems, all visible in real transcripts (Stage 12k):

1. Replies arrived in ~200ms. Nothing human answers that fast.
2. A whole answer arrived as one long block, often with markdown the sender never rendered, so
   `**Galaxy S25**` reached the customer as literal asterisks.
3. A customer typing "hi" / "want iphone" / "budget 3000" as three quick messages got three
   separate AI answers, because each inbound message ran the pipeline on its own.

The fix is one worker per conversation. It waits a moment for the customer to finish, answers the
whole thought at once, then types the reply out in a couple of short messages.

Why this wraps the pipeline rather than living inside it: `process_message` holds a per-customer
Redis lock while it runs. Sleeping inside it would hold that lock through every typing pause. Here
the lock is already released before any of the waiting happens.

Why it is channel-agnostic and no longer under `telegram_bot/`: it takes `answer`, `send` and
`typing` as callables, so the Telegram bot supplies PTB sends and the WhatsApp webhook supplies its
own. It also turns out to be a capacity mechanism, not only a humanising one — at 400 msg/min the
same load answered 248 messages with zero drops through here, against 231 answered and 162 dropped
going straight at the pipeline, using 38% fewer model round-trips (scripts/loadtest.py --paced).

Interruption rules — the careful part, because customers here fire off new questions mid-answer:
  * A message arriving while we are WAITING or TYPING cancels the worker and restarts the window.
    Nothing was sent and no tokens were spent, so the newest message simply joins the batch.
  * A message arriving while the LLM call is IN FLIGHT never cancels it. The answer they already
    asked for is delivered, then the worker loops and answers the follow-up. That is what a person
    does, and it is why a message can never be dropped.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable
from uuid import UUID

logger = logging.getLogger(__name__)

# How long to wait for the customer to finish their thought before answering.
DEBOUNCE_SECONDS = 2.5
# Reading/typing speed used to size the pause before each message, plus its bounds.
CHARS_PER_SECOND = 25.0
MIN_TYPING_SECONDS = 0.6
MAX_TYPING_SECONDS = 3.5
# A person sends two or three messages, not eight. Extra chunks are merged into the last.
MAX_BUBBLES = 3

# The prompt forbids markdown, but a model slips sometimes and the sender uses no parse_mode,
# so `**bold**` would reach the customer as raw asterisks. Belt and braces.
_BOLD = re.compile(r"\*{1,3}(?=\S)(.+?)(?<=\S)\*{1,3}", re.DOTALL)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s{0,3}[-*•]\s+", re.MULTILINE)


def strip_markdown(text: str) -> str:
    """Undo the formatting a human would never type into a chat."""
    text = _BOLD.sub(r"\1", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    return text.replace("—", "-").strip()


def split_bubbles(text: str, limit: int = MAX_BUBBLES) -> list[str]:
    """One reply → the messages a person would actually send.

    Blank lines are the split points (what the prompt asks the model to use). Beyond `limit`,
    the remainder is merged into the last message rather than dropped — losing half an answer
    to a formatting rule would be far worse than one long final message.
    """
    cleaned = strip_markdown(text or "")
    if not cleaned:
        return []
    parts = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    if not parts:
        return []
    if len(parts) <= limit:
        return parts
    return parts[: limit - 1] + ["\n\n".join(parts[limit - 1 :])]


def typing_delay(text: str) -> float:
    """How long to 'type' this message. Bounded so a long answer never stalls the conversation."""
    return min(MAX_TYPING_SECONDS, max(MIN_TYPING_SECONDS, len(text) / CHARS_PER_SECOND))


class Conversation:
    """The buffered state of one customer's chat with one shop.

    Holds the delivery callbacks too: they are closures over the current Telegram update, so the
    newest message supplies them and replaces the previous set (same chat either way).
    """

    __slots__ = ("pending", "task", "interruptible", "answer", "send", "typing")

    def __init__(self) -> None:
        self.pending: list[str] = []
        self.task: asyncio.Task | None = None
        # False once the LLM call starts: from then on the answer is paid for and must be sent.
        self.interruptible = True
        self.answer: Callable[[str], Awaitable[Any]] | None = None
        self.send: Callable[[str], Awaitable[None]] | None = None
        self.typing: Callable[[], Awaitable[None]] | None = None


class Pacer:
    """Owns one worker per conversation. In-process: all bots share one event loop (ADR-005).

    ponytail: a plain dict, no persistence. Ceiling: a restart drops at most a few seconds of
    buffered fragments. Upgrade: a Redis list keyed like the session, if restarts ever matter.
    """

    def __init__(self, *, debounce: float = DEBOUNCE_SECONDS) -> None:
        self._debounce = debounce
        self._chats: dict[tuple[UUID, str], Conversation] = {}

    def submit(
        self,
        shop_id: UUID,
        identity: str,
        text: str,
        *,
        answer: Callable[[str], Awaitable[Any]],
        send: Callable[[str], Awaitable[None]],
        typing: Callable[[], Awaitable[None]] | None = None,
    ) -> asyncio.Task:
        """Queue an inbound message. Returns the worker task (tests await it; the bot doesn't)."""
        key = (shop_id, identity)
        chat = self._chats.setdefault(key, Conversation())
        chat.pending.append(text)
        chat.answer, chat.send, chat.typing = answer, send, typing

        if chat.task is not None and not chat.task.done():
            if chat.interruptible:
                chat.task.cancel()  # nothing sent yet — fold this message into the batch
            else:
                return chat.task  # mid-answer: it will loop and pick this up when it lands

        chat.task = asyncio.create_task(self._run(key))
        return chat.task

    async def _run(self, key: tuple[UUID, str]) -> None:
        chat = self._chats[key]
        try:
            while chat.pending:
                chat.interruptible = True
                await asyncio.sleep(self._debounce)  # let them finish the thought

                batch, chat.pending = chat.pending, []
                chat.interruptible = False  # from here the answer is paid for; never cancel it
                try:
                    result = await chat.answer("\n".join(batch))  # type: ignore[misc]
                except Exception:
                    logger.exception("paced answer failed shop=%s identity=%s", *key)
                    return

                await self._deliver(chat, getattr(result, "reply", None))
                # Anything that arrived while we were answering gets answered next, not dropped.
        except asyncio.CancelledError:
            raise
        finally:
            if not chat.pending and self._chats.get(key) is chat:
                self._chats.pop(key, None)

    async def _deliver(self, chat: Conversation, reply: str | None) -> None:
        for bubble in split_bubbles(reply or ""):
            if chat.typing is not None:
                try:
                    await chat.typing()
                except Exception:  # noqa: BLE001 — a cosmetic indicator must never cost a reply
                    logger.debug("typing indicator failed", exc_info=True)
            await asyncio.sleep(typing_delay(bubble))
            await chat.send(bubble)  # type: ignore[misc]
