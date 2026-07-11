"""Celery app (SPEC §11). Broker/backend = Redis. Beat schedules the periodic jobs (Stage 10)."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "multi_shop_chatbot",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
    include=["app.tasks.tasks"],
)

celery_app.conf.timezone = "UTC"  # crontab below is UTC — usage keys are keyed by UTC day

# §11 reliability: acks_late + prefetch 1 → a task a crashed worker was running is redelivered,
# not lost. Safe here — flush_usage is idempotent (completed-days only) and health_check is stateless.
celery_app.conf.task_acks_late = True
celery_app.conf.worker_prefetch_multiplier = 1

# Beat schedule (ADR-006). Hourly, not once-at-midnight: the flush only touches COMPLETED days,
# so extra runs are idempotent no-ops — but a single missed midnight tick would otherwise strand
# a full day of billing data until the next. Hourly makes a missed tick self-heal within the hour.
celery_app.conf.beat_schedule = {
    "flush-usage-counters": {
        "task": "flush_usage_counters",
        "schedule": crontab(minute=15),  # every hour at :15
    },
    "health-check": {
        "task": "health_check",
        "schedule": 60.0,  # every 60s (SPEC §13) → owner paged on failure
    },
}
