"""Logging setup (SPEC §16). One call at process start; consistent structured lines everywhere.

Format is `time level logger key=value message` — greppable and parseable without a JSON dependency.
A JSON formatter can slot in here (one Formatter subclass) if a log pipeline ever needs it —
`ponytail:` not built until there's an aggregator to consume it.
"""

from __future__ import annotations

import logging

from app.core.config import settings

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def setup_logging() -> None:
    """Configure the root logger from `settings.log_level`. Idempotent (`force=True`)."""
    logging.basicConfig(level=settings.log_level, format=_FORMAT, datefmt=_DATEFMT, force=True)
    # httpx/httpcore log every request URL at INFO — and Telegram/Supabase URLs embed the BOT TOKEN
    # and API key. Silence them to WARNING so secrets never land in logs (SPEC §16, audit Phase 2).
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
