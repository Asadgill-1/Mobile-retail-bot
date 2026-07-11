"""System health check (SPEC §13). ONE checker, two callers: the `/health` endpoint and the
60s Celery-beat task. Deps (redis, repo) are injected so tests skip real IO.

Liveness (`ok`) gates on the subsystems that must be up for the app to serve a customer: DB,
Redis, and at least one Celery worker. LLM/Twilio are reported as *configuration state*, not
liveness — Twilio is mocked by design until Stage 13, so gating on it would alert forever, and a
live LLM ping every 60s would burn tokens (ponytail: swap in a cheap models-list ping if provider
outages ever need catching).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.escalations.context import session_key
from app.security.service import quarantine_key

logger = logging.getLogger(__name__)


@dataclass
class HealthReport:
    ok: bool
    checks: dict[str, str] = field(default_factory=dict)   # subsystem -> "ok" / "down: …" / "mocked"
    metrics: dict[str, int] = field(default_factory=dict)  # active_conversations, quarantined


def _worker_count() -> int:
    """Responding Celery workers via the broker. Module-level so tests can monkeypatch it."""
    from app.tasks.celery_app import celery_app

    replies = celery_app.control.ping(timeout=1)  # [{worker: {'ok': 'pong'}}, …]
    return len(replies or [])


async def _count_keys(redis: Any, pattern: str) -> int:
    n = 0
    async for _ in redis.scan_iter(match=pattern):
        n += 1
    return n


async def check_health(redis: Any, repo: Any, *, include_celery: bool = True) -> HealthReport:
    """Probe every §13 subsystem. Never raises — a checker that crashes reports nothing.

    `include_celery=False` skips the worker ping — used by the beat task, which runs *on* a worker
    (so one is alive by construction) and can't answer its own ping under a busy solo pool. The
    `/health` endpoint and `/owner health` (external observers) keep it on.
    """
    checks: dict[str, str] = {}

    try:
        checks["db"] = "ok" if await repo.health_check() else "down: probe returned false"
    except Exception as e:
        checks["db"] = f"down: {e}"

    try:
        checks["redis"] = "ok" if await redis.ping() else "down: no pong"
    except Exception as e:
        checks["redis"] = f"down: {e}"

    try:
        from app.llm.llm_client import LLMClient

        checks["llm"] = "configured" if LLMClient().is_configured else "unconfigured"
    except Exception as e:
        checks["llm"] = f"error: {e}"

    checks["twilio"] = "mocked (Stage 13)" if settings.whatsapp_mocked else "configured"

    if include_celery:
        try:
            workers = await asyncio.to_thread(_worker_count)
            checks["celery"] = f"ok ({workers} worker{'s' if workers != 1 else ''})" if workers else "down: no workers"
        except Exception as e:
            checks["celery"] = f"down: {e}"

    metrics: dict[str, int] = {}
    try:
        metrics["active_conversations"] = await _count_keys(redis, session_key("*", "*"))
        metrics["quarantined"] = await _count_keys(redis, quarantine_key("*"))
    except Exception as e:  # metrics are best-effort — never fail the check over a count
        logger.warning("health metrics failed: %s", e)

    ok = (
        checks["db"] == "ok"
        and checks["redis"] == "ok"
        and (not include_celery or checks["celery"].startswith("ok"))
    )
    return HealthReport(ok=ok, checks=checks, metrics=metrics)


def format_health(r: HealthReport) -> str:
    """Text for the owner alert + `/owner health`."""
    head = "🟢 System healthy" if r.ok else "🔴 System UNHEALTHY"
    lines = [head, ""]
    lines += [f"  {'✅' if v == 'ok' or v.startswith(('ok', 'mocked', 'configured')) else '❌'} {k}: {v}"
              for k, v in r.checks.items()]
    if r.metrics:
        lines += ["", f"Active conversations: {r.metrics.get('active_conversations', 0)}",
                  f"Quarantined: {r.metrics.get('quarantined', 0)}"]
    return "\n".join(lines)
