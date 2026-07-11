"""Tests for TenantService (shop lifecycle + suspension, SPEC §1, §2)."""

from __future__ import annotations

import pytest

from app.tenants.models import ShopStatus
from app.tenants.service import ShopNotFound, TenantService

pytestmark = pytest.mark.asyncio


async def test_is_suspended_reflects_status(tenant_service: TenantService):
    shop3 = await tenant_service.get_shop_by_whatsapp_number("+10000000003")
    assert await tenant_service.is_suspended(shop3.id) is True
    shop1 = await tenant_service.get_shop_by_whatsapp_number("+10000000001")
    assert await tenant_service.is_suspended(shop1.id) is False


async def test_suspend_requires_reason(tenant_service: TenantService):
    shop1 = await tenant_service.get_shop_by_whatsapp_number("+10000000001")
    with pytest.raises(ValueError):
        await tenant_service.suspend_shop(shop1.id, "   ")


async def test_suspend_then_resume(tenant_service: TenantService):
    shop1 = await tenant_service.get_shop_by_whatsapp_number("+10000000001")
    s = await tenant_service.suspend_shop(shop1.id, "audit")
    assert s.status == ShopStatus.SUSPENDED
    assert s.suspension_reason == "audit"
    r = await tenant_service.resume_shop(shop1.id)
    assert r.status == ShopStatus.ACTIVE


async def test_shop_status_includes_shopkeepers(tenant_service: TenantService):
    shop1 = await tenant_service.get_shop_by_whatsapp_number("+10000000001")
    info = await tenant_service.shop_status(shop1.id)
    assert info.status == ShopStatus.ACTIVE
    assert any(sk.is_owner for sk in info.shopkeepers)


async def test_unknown_shop_raises(tenant_service: TenantService):
    import uuid

    with pytest.raises(ShopNotFound):
        await tenant_service.get_shop(uuid.uuid4())
