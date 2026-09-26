"""Integration tests for the local readiness endpoint."""

import anyio
import httpx2
import pytest
from app.main import create_app
from app.schemas import HealthResponse
from pydantic import TypeAdapter

pytestmark = pytest.mark.asyncio

_EXPECTED = HealthResponse()


async def test_health_reports_ready_without_contacting_github() -> None:
    upstream = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(500))
    )
    api = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(
            app=create_app(upstream_client=upstream, capacity_limiter=anyio.CapacityLimiter(1))
        ),
        base_url="http://test",
    )
    try:
        response = await api.get("/api/health")
    finally:
        await upstream.aclose()
        await api.aclose()

    assert response.status_code == 200
    assert TypeAdapter(HealthResponse).validate_json(response.content) == _EXPECTED
