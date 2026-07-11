"""Tests for the InMemoryTenantRepo (and the TenantRepo contract)."""

from __future__ import annotations

import pytest

from app.db.in_memory import InMemoryTenantRepo
from app.tenants.models import ShopStatus

pytestmark = pytest.mark.asyncio


async def test_seed_creates_three_shops():
    repo = InMemoryTenantRepo()
    repo.seed_default()
    shops = await repo.list_shops()
    assert len(shops) == 3
    names = {s.name for s in shops}
    assert any("Dubai Marina" in n for n in names)


async def test_get_shop_by_whatsapp_number_resolves_shop():
    repo = InMemoryTenantRepo()
    repo.seed_default()
    shop = await repo.get_shop_by_whatsapp_number("+10000000001")
    assert shop is not None
    assert shop.name.startswith("Shop 01")
    assert shop.status == ShopStatus.ACTIVE


async def test_unknown_number_returns_none():
    repo = InMemoryTenantRepo()
    repo.seed_default()
    assert await repo.get_shop_by_whatsapp_number("+19999999999") is None


async def test_seed_marks_shop3_suspended():
    repo = InMemoryTenantRepo()
    repo.seed_default()
    shop3 = await repo.get_shop_by_whatsapp_number("+10000000003")
    assert shop3 is not None
    assert shop3.status == ShopStatus.SUSPENDED
    assert shop3.suspension_reason is not None


async def test_get_shopkeeper_by_telegram_id():
    repo = InMemoryTenantRepo()
    repo.seed_default()
    owner = await repo.get_shopkeeper_by_telegram_id(100000001)
    assert owner is not None
    assert owner.is_owner is True


async def test_suspend_and_resume_round_trip():
    repo = InMemoryTenantRepo()
    repo.seed_default()
    shop = await repo.get_shop_by_whatsapp_number("+10000000001")
    suspended = await repo.suspend_shop(shop.id, "maintenance")
    assert suspended.status == ShopStatus.SUSPENDED
    assert suspended.suspension_reason == "maintenance"
    resumed = await repo.resume_shop(shop.id)
    assert resumed.status == ShopStatus.ACTIVE
    assert resumed.suspension_reason is None
