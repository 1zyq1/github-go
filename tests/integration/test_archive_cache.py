"""Integration tests for cached archive proxying."""

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import override

import anyio
import httpx2
import pytest
from app.accounts import AccountStore
from app.config import Settings
from app.main import create_app
from app.url_policy import encode_proxy_token

from tests.support import RecordingMailer, sign_in

pytestmark = pytest.mark.asyncio

_ARCHIVE_URL = "https://github.com/acme/widgets/archive/refs/tags/v1.0.0.zip"
_ARCHIVE_TOKEN = encode_proxy_token(_ARCHIVE_URL)
_RAW_URL = "https://raw.githubusercontent.com/acme/widgets/main/file.bin"
_RAW_TOKEN = encode_proxy_token(_RAW_URL)
_PAYLOAD = b"PK\x03\x04archive-payload"


class _Stream(httpx2.AsyncByteStream):
    """Yield a fixed payload, optionally stalling before completion."""

    def __init__(
        self,
        chunks: tuple[bytes, ...],
        *,
        gate: anyio.Event | None = None,
        error: Exception | None = None,
    ) -> None:
        self._chunks: tuple[bytes, ...] = chunks
        self._gate: anyio.Event | None = gate
        self._error: Exception | None = error

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk
        if self._gate is not None:
            await self._gate.wait()
        if self._error is not None:
            raise self._error

    @override
    async def aclose(self) -> None:
        pass


def _cache_names(directory: Path) -> list[str]:
    """List the cache entries a directory holds."""
    return sorted(entry.name for entry in directory.iterdir())


def _archive_response(payload: bytes, *, gate: anyio.Event | None = None) -> httpx2.Response:
    return httpx2.Response(
        200,
        headers={"content-type": "application/zip"},
        stream=_Stream((payload,), gate=gate),
    )


async def _clients(
    account_store: AccountStore,
    tmp_path: Path,
    handler: Callable[[httpx2.Request], httpx2.Response],
    *,
    cache_bytes: int,
) -> tuple[httpx2.AsyncClient, httpx2.AsyncClient]:
    upstream = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    app = create_app(
        settings=Settings(archive_cache_dir=tmp_path, archive_cache_max_bytes=cache_bytes),
        upstream_client=upstream,
        capacity_limiter=anyio.CapacityLimiter(8),
        mailer=RecordingMailer(),
        account_store=account_store,
    )
    api = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )
    return upstream, await sign_in(api)


async def test_second_request_is_served_from_disk(
    tmp_path: Path, account_store: AccountStore
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _archive_response(_PAYLOAD)

    upstream, api = await _clients(account_store, tmp_path, handler, cache_bytes=1_000_000)
    try:
        first = await api.get(f"/proxy/{_ARCHIVE_TOKEN}")
        second = await api.get(f"/proxy/{_ARCHIVE_TOKEN}")
    finally:
        await upstream.aclose()
        await api.aclose()

    assert first.status_code == 200
    assert first.content == _PAYLOAD
    assert second.status_code == 200
    assert second.content == _PAYLOAD
    assert len(requests) == 1


async def test_cached_archive_supports_range_requests(
    tmp_path: Path, account_store: AccountStore
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _archive_response(_PAYLOAD)

    upstream, api = await _clients(account_store, tmp_path, handler, cache_bytes=1_000_000)
    try:
        _ = await api.get(f"/proxy/{_ARCHIVE_TOKEN}")
        ranged = await api.get(
            f"/proxy/{_ARCHIVE_TOKEN}",
            headers={"Range": "bytes=0-3"},
        )
    finally:
        await upstream.aclose()
        await api.aclose()

    assert ranged.status_code == 206
    assert ranged.headers["content-range"] == f"bytes 0-3/{len(_PAYLOAD)}"
    assert ranged.content == _PAYLOAD[:4]
    assert len(requests) == 1


async def test_range_request_does_not_populate_the_cache(
    tmp_path: Path, account_store: AccountStore
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _archive_response(_PAYLOAD)

    upstream, api = await _clients(account_store, tmp_path, handler, cache_bytes=1_000_000)
    try:
        ranged = await api.get(f"/proxy/{_ARCHIVE_TOKEN}", headers={"Range": "bytes=0-3"})
        after_range = _cache_names(tmp_path)
        second = await api.get(f"/proxy/{_ARCHIVE_TOKEN}")
    finally:
        await upstream.aclose()
        await api.aclose()

    assert ranged.status_code == 200
    assert after_range == []
    assert second.status_code == 200
    assert len(requests) == 2


async def test_concurrent_requests_share_one_upstream_fetch(
    tmp_path: Path, account_store: AccountStore
) -> None:
    requests: list[httpx2.Request] = []
    gate = anyio.Event()

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _archive_response(_PAYLOAD, gate=gate)

    upstream, api = await _clients(account_store, tmp_path, handler, cache_bytes=1_000_000)
    bodies: list[bytes] = []

    async def fetch() -> None:
        response = await api.get(f"/proxy/{_ARCHIVE_TOKEN}")
        bodies.append(response.content)

    try:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(fetch)
            _ = tasks.start_soon(fetch)
            await anyio.sleep(0.2)
            gate.set()
    finally:
        await upstream.aclose()
        await api.aclose()

    assert len(requests) == 1
    assert bodies == [_PAYLOAD, _PAYLOAD]


async def test_zero_cap_disables_caching(tmp_path: Path, account_store: AccountStore) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _archive_response(_PAYLOAD)

    upstream, api = await _clients(account_store, tmp_path, handler, cache_bytes=0)
    try:
        first = await api.get(f"/proxy/{_ARCHIVE_TOKEN}")
        second = await api.get(f"/proxy/{_ARCHIVE_TOKEN}")
    finally:
        await upstream.aclose()
        await api.aclose()

    assert first.content == _PAYLOAD
    assert second.content == _PAYLOAD
    assert len(requests) == 2
    assert _cache_names(tmp_path) == []


async def test_raw_content_is_never_cached(tmp_path: Path, account_store: AccountStore) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, headers={"content-type": "text/plain"}, content=b"hello")

    upstream, api = await _clients(account_store, tmp_path, handler, cache_bytes=1_000_000)
    try:
        first = await api.get(f"/proxy/{_RAW_TOKEN}")
        second = await api.get(f"/proxy/{_RAW_TOKEN}")
    finally:
        await upstream.aclose()
        await api.aclose()

    assert first.content == b"hello"
    assert second.content == b"hello"
    assert len(requests) == 2
    assert _cache_names(tmp_path) == []


async def test_failed_leader_does_not_strand_followers(
    tmp_path: Path, account_store: AccountStore
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(404, headers={}, content=b"")

    upstream, api = await _clients(account_store, tmp_path, handler, cache_bytes=1_000_000)
    codes: list[int] = []

    async def fetch() -> None:
        response = await api.get(f"/proxy/{_ARCHIVE_TOKEN}")
        codes.append(response.status_code)

    try:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(fetch)
            _ = tasks.start_soon(fetch)
    finally:
        await upstream.aclose()
        await api.aclose()

    assert codes == [404, 404]
    assert len(requests) == 2


async def test_midstream_failure_leaves_no_entry_and_a_usable_flight(
    tmp_path: Path, account_store: AccountStore
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(
                200,
                headers={"content-type": "application/zip"},
                stream=_Stream((b"partial",), error=httpx2.ReadTimeout("stall")),
            )
        return _archive_response(_PAYLOAD)

    upstream, api = await _clients(account_store, tmp_path, handler, cache_bytes=1_000_000)
    try:
        first = await api.get(f"/proxy/{_ARCHIVE_TOKEN}")
        second = await api.get(f"/proxy/{_ARCHIVE_TOKEN}")
    finally:
        await upstream.aclose()
        await api.aclose()

    assert first.status_code == 200
    assert first.content == b"partial"
    assert second.status_code == 200
    assert second.content == _PAYLOAD
    assert len(requests) == 2
