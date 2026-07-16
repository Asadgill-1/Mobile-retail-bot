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


# --- onboarding (ADR-006): the repos always had these; nothing could reach them until now ---
from uuid import uuid4  # noqa: E402

from app.tenants.service import ClientNotFound  # noqa: E402


async def test_create_client_then_shop_then_keeper(tenant_service: TenantService):
    c = await tenant_service.create_client("Gulf Traders", "Asad", "0501234567", "a@b.ae")
    assert c.name == "Gulf Traders" and c.contact_phone == "0501234567"

    s = await tenant_service.create_shop(c.id, "Gulf Mobiles", "+971500000099")
    assert s.client_id == c.id and s.name == "Gulf Mobiles"
    assert s in await tenant_service.list_shops_by_client(c.id)

    sk = await tenant_service.create_shopkeeper(s.id, 4242, "Sami", is_owner=True)
    assert sk.shop_id == s.id and sk.telegram_id == 4242 and sk.is_owner is True


async def test_create_client_rejects_blank_name(tenant_service: TenantService):
    with pytest.raises(ValueError):
        await tenant_service.create_client("   ")


async def test_create_shop_under_unknown_client_raises(tenant_service: TenantService):
    # Never orphan a shop: the client guard runs before the insert.
    with pytest.raises(ClientNotFound):
        await tenant_service.create_shop(uuid4(), "Ghost Shop")


async def test_create_shop_rejects_blank_name(tenant_service: TenantService):
    c = await tenant_service.create_client("Gulf Traders")
    with pytest.raises(ValueError):
        await tenant_service.create_shop(c.id, "  ")


async def test_create_shopkeeper_for_unknown_shop_raises(tenant_service: TenantService):
    with pytest.raises(ShopNotFound):
        await tenant_service.create_shopkeeper(uuid4(), 1)


async def test_set_shop_tokens_sets_both(tenant_service: TenantService):
    shop = await tenant_service.get_shop_by_whatsapp_number("+10000000001")
    s = await tenant_service.set_shop_tokens(shop.id, "keeper-tok", "customer-tok")
    assert s.telegram_keeper_bot_token == "keeper-tok"
    assert s.telegram_customer_bot_token == "customer-tok"


async def test_set_shop_tokens_none_leaves_that_token_alone(tenant_service: TenantService):
    shop = await tenant_service.get_shop_by_whatsapp_number("+10000000001")
    await tenant_service.set_shop_tokens(shop.id, "keeper-tok", "customer-tok")
    s = await tenant_service.set_shop_tokens(shop.id, keeper_token="new-keeper")
    assert s.telegram_keeper_bot_token == "new-keeper"
    assert s.telegram_customer_bot_token == "customer-tok"  # untouched, not wiped


async def test_set_shop_tokens_unknown_shop_raises(tenant_service: TenantService):
    with pytest.raises(ShopNotFound):
        await tenant_service.set_shop_tokens(uuid4(), "t")
