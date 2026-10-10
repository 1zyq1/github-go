"""Integration tests for same-origin proxy routes."""

from collections.abc import Callable

import anyio
import httpx2
import pytest
from app.accounts import AccountStore
from app.errors import ErrorEnvelope
from app.main import create_app
from app.schemas import ResolveResponse
from pydantic import TypeAdapter

from tests.support import RecordingMailer, sign_in

pytestmark = pytest.mark.asyncio

_SOURCE_URL = "https://raw.githubusercontent.com/acme/widgets/main/file.bin"


async def _clients(
    account_store: AccountStore,
    handler: Callable[[httpx2.Request], httpx2.Response],
    *,
    capacity: int = 4,
) -> tuple[httpx2.AsyncClient, httpx2.AsyncClient]:
    upstream = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    limiter = anyio.CapacityLimiter(capacity)
    app = create_app(
        upstream_client=upstream,
        capacity_limiter=limiter,
        mailer=RecordingMailer(),
        account_store=account_store,
    )
    api = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )
    return upstream, await sign_in(api)


def _error_code(response: httpx2.Response) -> str:
    payload = TypeAdapter(ErrorEnvelope).validate_json(response.content)
    return payload["error"]["code"]


async def _resolve_path(api: httpx2.AsyncClient) -> str:
    response = await api.post("/api/resolve", json={"url": _SOURCE_URL})
    assert response.status_code == 200
    return TypeAdapter(ResolveResponse).validate_json(response.content).proxy_url


async def test_get_streams_resolved_file_and_forwards_range(account_store: AccountStore) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            206,
            headers={
                "content-type": "application/octet-stream",
                "content-range": "bytes 0-2/11",
                "content-length": "3",
            },
            content=b"pay",
        )

    upstream, api = await _clients(account_store, handler)
    try:
        proxy_path = await _resolve_path(api)
        response = await api.get(proxy_path, headers={"Range": "bytes=0-2"})

        assert response.status_code == 206
        assert response.content == b"pay"
        assert response.headers["content-range"] == "bytes 0-2/11"
        assert requests[0].headers["range"] == "bytes=0-2"
    finally:
        await api.aclose()
        await upstream.aclose()


async def test_head_returns_bodyless_file_metadata(account_store: AccountStore) -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/plain", "content-length": "11", "etag": '"v1"'},
            content=b"hello world",
        )

    upstream, api = await _clients(account_store, handler)
    try:
        response = await api.head(await _resolve_path(api))

        assert response.status_code == 200
        assert response.content == b""
        assert response.headers["content-type"] == "text/plain"
        assert response.headers["etag"] == '"v1"'
        assert "content-length" not in response.headers
    finally:
        await api.aclose()
        await upstream.aclose()


async def test_maps_not_found_and_upstream_failure_without_leaking_body(
    account_store: AccountStore,
) -> None:
    for upstream_status, expected_status, expected_code in (
        (404, 404, "upstream_not_found"),
        (500, 502, "upstream_unavailable"),
    ):

        def handler(_request: httpx2.Request, status: int = upstream_status) -> httpx2.Response:
            return httpx2.Response(
                status,
                headers={"set-cookie": "secret=true", "content-type": "text/html"},
                content=b"<html>secret</html>",
            )

        upstream, api = await _clients(account_store, handler)
        try:
            response = await api.get(await _resolve_path(api))

            assert response.status_code == expected_status
            assert _error_code(response) == expected_code
            assert b"secret" not in response.content
            assert "set-cookie" not in response.headers
        finally:
            await api.aclose()
            await upstream.aclose()


async def test_preserves_bodyless_304_and_range_error(account_store: AccountStore) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.headers.get("range"):
            return httpx2.Response(
                416,
                headers={"content-range": "bytes */11", "set-cookie": "secret=true"},
                content=b"secret",
            )
        return httpx2.Response(304, headers={"etag": '"v1"', "content-length": "11"})

    upstream, api = await _clients(account_store, handler)
    try:
        proxy_path = await _resolve_path(api)
        not_modified = await api.get(proxy_path, headers={"If-None-Match": '"v1"'})
        range_error = await api.get(proxy_path, headers={"Range": "bytes=20-30"})

        assert not_modified.status_code == 304
        assert not_modified.content == b""
        assert "content-length" not in not_modified.headers
        assert range_error.status_code == 416
        assert range_error.headers["content-range"] == "bytes */11"
        assert _error_code(range_error) == "range_not_satisfiable"
        assert b"secret" not in range_error.content
    finally:
        await api.aclose()
        await upstream.aclose()


async def test_rejects_invalid_proxy_token_and_health_is_local(account_store: AccountStore) -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        message = "health and invalid-token requests must not reach upstream"
        raise AssertionError(message)

    upstream, api = await _clients(account_store, handler)
    try:
        invalid = await api.get("/proxy/not-a-valid-token")
        health = await api.get("/api/health")

        assert invalid.status_code == 400
        assert _error_code(invalid) == "invalid_proxy_token"
        assert health.status_code == 200
        assert health.json() == {"service": "github-accelerator", "status": "ok"}
    finally:
        await api.aclose()
        await upstream.aclose()
