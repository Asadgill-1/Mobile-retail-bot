"""Health checker (§13) — one checker, two callers. fakeredis + in-memory repo; no real IO."""

from __future__ import annotations

from uuid import uuid4

import fakeredis.aioredis
import pytest

import app.reports.health as health
from app.db.in_memory import InMemoryTenantRepo
from app.escalations.context import session_key
from app.reports.health import HealthReport, check_health, format_health
from app.security.service import quarantine_key


def _repo() -> InMemoryTenantRepo:
    repo = InMemoryTenantRepo()
    repo.seed_default()
    return repo


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_healthy_when_all_up_and_counts_metrics(redis, monkeypatch):
    monkeypatch.setattr(health, "_worker_count", lambda: 1)
    await redis.set(session_key(uuid4(), "+199"), "[]")       # one active conversation
    await redis.set(quarantine_key("+666"), "1")              # one quarantined

    report = await check_health(redis, _repo())

    assert report.ok
    assert report.checks["db"] == "ok" and report.checks["redis"] == "ok"
    assert report.checks["celery"].startswith("ok")
    assert report.metrics == {"active_conversations": 1, "quarantined": 1}


@pytest.mark.asyncio
async def test_unhealthy_when_no_workers(redis, monkeypatch):
    monkeypatch.setattr(health, "_worker_count", lambda: 0)
    report = await check_health(redis, _repo())
    assert not report.ok
    assert report.checks["celery"].startswith("down")


@pytest.mark.asyncio
async def test_unhealthy_when_db_down(redis, monkeypatch):
    monkeypatch.setattr(health, "_worker_count", lambda: 1)
    repo = _repo()

    async def _boom() -> bool:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(repo, "health_check", _boom)
    report = await check_health(redis, repo)
    assert not report.ok
    assert report.checks["db"].startswith("down:")


def test_format_health_renders_both_states():
    ok = format_health(HealthReport(ok=True, checks={"db": "ok"}, metrics={"active_conversations": 2, "quarantined": 0}))
    assert "healthy" in ok.lower() and "Active conversations: 2" in ok
    bad = format_health(HealthReport(ok=False, checks={"db": "down: x"}))
    assert "UNHEALTHY" in bad and "❌ db" in bad


@pytest.mark.asyncio
async def test_run_health_check_pages_owner_only_when_unhealthy(redis, monkeypatch):
    sent: list[str] = []

    async def _send(text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr("app.telegram_bot.notify.send_to_owner", _send)

    async def _bad(_r, _repo, **_k):
        return HealthReport(ok=False, checks={"db": "down: x"})

    monkeypatch.setattr("app.reports.health.check_health", _bad)
    from app.tasks.tasks import run_health_check

    assert await run_health_check(_repo(), redis) == "alerted"
    assert len(sent) == 1 and "CRITICAL" in sent[0]

    sent.clear()

    async def _good(_r, _repo, **_k):
        return HealthReport(ok=True, checks={"db": "ok"})

    monkeypatch.setattr("app.reports.health.check_health", _good)
    assert await run_health_check(_repo(), redis) == "ok"
    assert sent == []  # healthy → owner not paged


@pytest.mark.asyncio
async def test_beat_path_excludes_celery_and_stays_healthy_without_workers(redis, monkeypatch):
    """The beat task runs ON a worker, so it must NOT gate on the worker ping (solo can't self-ping)."""
    def _boom():
        raise AssertionError("worker ping must not run when include_celery=False")

    monkeypatch.setattr(health, "_worker_count", _boom)
    report = await check_health(redis, _repo(), include_celery=False)
    assert report.ok  # db+redis up → healthy, no celery check, no owner spam
    assert "celery" not in report.checks
