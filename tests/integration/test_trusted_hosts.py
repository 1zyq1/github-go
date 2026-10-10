"""Integration tests for the optional Host header allowlist."""

import anyio
import httpx2
import pytest
from app.accounts import AccountStore
from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.asyncio

_ALLOWED_HOST = "accelerator.example.com"
_FOREIGN_HOST = "attacker.example.net"


async def _get_with_host(
    account_store: AccountStore,
    trusted_hosts: tuple[str, ...],
    host: str,
) -> httpx2.Response:
    upstream = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(500))
    )
    api = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(
            app=create_app(
                settings=Settings(trusted_hosts=trusted_hosts),
                upstream_client=upstream,
                capacity_limiter=anyio.CapacityLimiter(1),
                account_store=account_store,
            )
        ),
        base_url="http://test",
    )
    try:
        return await api.get("/api/health", headers={"Host": host})
    finally:
        await upstream.aclose()
        await api.aclose()


async def test_any_host_is_accepted_when_the_allowlist_is_empty(
    account_store: AccountStore,
) -> None:
    response = await _get_with_host(account_store, (), _FOREIGN_HOST)

    assert response.status_code == 200


async def test_configured_host_is_accepted(account_store: AccountStore) -> None:
    response = await _get_with_host(account_store, (_ALLOWED_HOST,), _ALLOWED_HOST)

    assert response.status_code == 200


async def test_unlisted_host_is_rejected(account_store: AccountStore) -> None:
    response = await _get_with_host(account_store, (_ALLOWED_HOST,), _FOREIGN_HOST)

    assert response.status_code == 400
    assert response.text == "Invalid host header"
