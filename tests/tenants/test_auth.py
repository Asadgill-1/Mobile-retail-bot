"""Tests for tenant auth — owner + shopkeeper verification (SPEC §1)."""

from __future__ import annotations

import pytest

from app.tenants.auth import NotAuthorized, is_owner, require_owner, resolve_shopkeeper


def test_is_owner_true_for_configured_owner_id():
    assert is_owner(100000001) is True


def test_is_owner_false_for_others():
    assert is_owner(999999) is False


@pytest.mark.asyncio
async def test_require_owner_passes_for_owner(tenant_repo):
    await require_owner(100000001)  # no raise


@pytest.mark.asyncio
async def test_require_owner_raises_for_non_owner(tenant_repo):
    with pytest.raises(NotAuthorized):
        await require_owner(4242)


@pytest.mark.asyncio
async def test_resolve_shopkeeper_returns_registered_shopkeeper(tenant_repo):
    sk = await resolve_shopkeeper(100000001, tenant_repo)
    assert sk.is_owner is True


@pytest.mark.asyncio
async def test_resolve_shopkeeper_raises_for_unknown(tenant_repo):
    with pytest.raises(NotAuthorized):
        await resolve_shopkeeper(777, tenant_repo)
