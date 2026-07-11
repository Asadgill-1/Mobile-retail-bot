"""Shared pytest fixtures.

Stage 1: provides an InMemoryTenantRepo seeded like migrations/001_init.sql,
so tenant/auth logic is testable without Supabase credentials (Q-003).
"""

from __future__ import annotations

import os

import pytest

# Provide minimal env so settings() can load even without a .env file.
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:test")
os.environ.setdefault("OWNER_TELEGRAM_ID", "100000001")
os.environ.setdefault("AI_API_KEY", "test-llm-key")
os.environ.setdefault("FLOWER_PASSWORD", "test-flower")

from app.db.in_memory import InMemoryTenantRepo  # noqa: E402
from app.tenants.service import TenantService  # noqa: E402


@pytest.fixture
def tenant_repo() -> InMemoryTenantRepo:
    repo = InMemoryTenantRepo()
    repo.seed_default()
    return repo


@pytest.fixture
def tenant_service(tenant_repo: InMemoryTenantRepo) -> TenantService:
    return TenantService(tenant_repo)
