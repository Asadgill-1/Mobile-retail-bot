"""FastAPI application entrypoint.

Stage 0: minimal app with a `/health` placeholder. Webhook routes
(`/webhook/whatsapp`, `/webhook/telegram`) are mounted in Stage 2/3.

Run (dev):
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import setup_logging
from app.whatsapp.webhook import router as whatsapp_router

setup_logging()  # §16: structured logs for the API process
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    description="Multi-shop chatbot (WhatsApp + Telegram). See docs/SPEC-source.md.",
    version="0.1.0",
)
app.include_router(whatsapp_router)  # POST /webhook/whatsapp (mocked until Stage 13, ADR-002)


@app.get("/health")
async def health() -> JSONResponse:
    """Full subsystem health (SPEC §13). Same checker the 60s beat task uses. 503 when unhealthy."""
    from app.db.factory import get_tenant_repo
    from app.db.redis_client import get_redis
    from app.reports.health import check_health

    report = await check_health(get_redis(), get_tenant_repo())
    body = {"status": "ok" if report.ok else "unhealthy", "env": settings.env,
            "checks": report.checks, "metrics": report.metrics}
    return JSONResponse(body, status_code=200 if report.ok else 503)
