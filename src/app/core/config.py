"""Re-export settings for convenience.

Modules import config via `from app.core.config import settings` rather than
touching pydantic directly. Keeps a single import path.
"""

from config.settings import Settings, get_settings

settings: Settings = get_settings()

__all__ = ["settings", "Settings", "get_settings"]
