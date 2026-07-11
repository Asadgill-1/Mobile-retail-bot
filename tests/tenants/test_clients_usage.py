"""Tests for the client layer + usage tracking (ADR-006)."""

from __future__ import annotations

from datetime import date

import pytest

from app.tenants.service import ClientNotFound, TenantService

pytestmark = pytest.mark.asyncio


async def test_seed_creates_two_clients(tenant_service: TenantService):
    clients = await tenant_service.list_clients()
    assert len(clients) == 2
    names = {c.name for c in clients}
    assert "Client A — TechStore Group" in names
    assert "Client B — Sharjah Mobiles" in names


async def test_client_a_owns_two_shops(tenant_service: TenantService):
    clients = await tenant_service.list_clients()
    client_a = next(c for c in clients if "TechStore" in c.name)
    shops = await tenant_service.list_shops_by_client(client_a.id)
    assert len(shops) == 2
    assert all(s.client_id == client_a.id for s in shops)


async def test_client_b_owns_one_shop(tenant_service: TenantService):
    clients = await tenant_service.list_clients()
    client_b = next(c for c in clients if "Sharjah" in c.name)
    shops = await tenant_service.list_shops_by_client(client_b.id)
    assert len(shops) == 1
    assert shops[0].status.value == "suspended"  # shop 03 seed


async def test_unknown_client_raises(tenant_service: TenantService):
    import uuid

    with pytest.raises(ClientNotFound):
        await tenant_service.get_client(uuid.uuid4())


async def test_usage_upsert_and_read(tenant_service: TenantService, tenant_repo):
    clients = await tenant_service.list_clients()
    client_a = next(c for c in clients if "TechStore" in c.name)
    shops = await tenant_service.list_shops_by_client(client_a.id)
    shop1 = shops[0]

    today = date(2026, 7, 7)
    await tenant_repo.upsert_usage(client_a.id, shop1.id, today, "customer_msg_in", 42)
    # upsert replaces
    await tenant_repo.upsert_usage(client_a.id, shop1.id, today, "customer_msg_in", 50)
    await tenant_repo.upsert_usage(client_a.id, shop1.id, today, "escalation", 3)

    usage = await tenant_service.get_usage(client_a.id, today)
    by_metric = {u.metric: u.count for u in usage}
    assert by_metric["customer_msg_in"] == 50  # upserted, not accumulated
    assert by_metric["escalation"] == 3


async def test_shop_status_includes_client_id(tenant_service: TenantService):
    shop1 = await tenant_service.get_shop_by_whatsapp_number("+10000000001")
    info = await tenant_service.shop_status(shop1.id)
    assert info.client_id == shop1.client_id
