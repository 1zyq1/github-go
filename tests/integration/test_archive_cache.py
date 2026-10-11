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
_RELEASE_URL = "https://github.com/acme/widgets/releases/download/v1.2.0/widget.zip"
_RELEASE_TOKEN = encode_proxy_token(_RELEASE_URL)
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


async def test_range_request_pulls_the_whole_payload_once(
    tmp_path: Path, account_store: AccountStore
) -> None:
    """A ranged request fills the cache instead of bypassing it.

    The first request for an uncached payload has to be fetched whole: an
    upstream range response cannot be stored, and skipping the coalescing is
    what lets one download manager's connections each hit upstream and drain
    the shared egress allowance. The client that triggered the fill is answered
    with the complete payload -- a server may always ignore a Range header --
    while every request after it, ranged or not, is served off disk.
    """
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _archive_response(_PAYLOAD)

    upstream, api = await _clients(account_store, tmp_path, handler, cache_bytes=1_000_000)
    try:
        ranged = await api.get(f"/proxy/{_ARCHIVE_TOKEN}", headers={"Range": "bytes=0-3"})
        after_range = _cache_names(tmp_path)
        second = await api.get(f"/proxy/{_ARCHIVE_TOKEN}")
        cached_range = await api.get(f"/proxy/{_ARCHIVE_TOKEN}", headers={"Range": "bytes=0-3"})
    finally:
        await upstream.aclose()
        await api.aclose()

    assert ranged.status_code == 200
    assert ranged.content == _PAYLOAD
    assert len(after_range) == 1
    assert second.status_code == 200
    assert second.content == _PAYLOAD
    assert cached_range.status_code == 206
    assert cached_range.headers["content-range"] == f"bytes 0-3/{len(_PAYLOAD)}"
    assert cached_range.content == _PAYLOAD[:4]
    assert len(requests) == 1
    assert "range" not in requests[0].headers


async def test_concurrent_range_requests_cost_one_upstream_fetch(
    tmp_path: Path, account_store: AccountStore
) -> None:
    """Two ranged requests for one payload must not each open their own fetch.

    Whatever the interleaving, the payload is pulled once: one request takes the
    fill it triggered (the complete body) and the other reads the range it asked
    for off disk. The service-level test of the coalescing itself lives in
    ``tests/unit/test_proxy.py``, where the interleaving can be pinned exactly.
    """
    requests: list[httpx2.Request] = []
    gate = anyio.Event()

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _archive_response(_PAYLOAD, gate=gate)

    upstream, api = await _clients(account_store, tmp_path, handler, cache_bytes=1_000_000)
    results: list[httpx2.Response] = []

    async def fetch(start: int) -> None:
        headers = {"Range": f"bytes={start}-{start + 2}"}
        results.append(await api.get(f"/proxy/{_ARCHIVE_TOKEN}", headers=headers))

    try:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(fetch, 0)
            _ = tasks.start_soon(fetch, 4)
            await anyio.sleep(0.2)
            gate.set()
    finally:
        await upstream.aclose()
        await api.aclose()

    assert len(requests) == 1
    assert sorted(result.status_code for result in results) == [200, 206]
    partial = next(result for result in results if result.status_code == 206)
    assert partial.content == _PAYLOAD[4:7]


async def test_release_asset_is_cached(tmp_path: Path, account_store: AccountStore) -> None:
    """Release assets name a fixed tag, so their bytes are cached like archives."""
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _archive_response(_PAYLOAD)

    upstream, api = await _clients(account_store, tmp_path, handler, cache_bytes=1_000_000)
    try:
        first = await api.get(f"/proxy/{_RELEASE_TOKEN}")
        second = await api.get(f"/proxy/{_RELEASE_TOKEN}")
    finally:
        await upstream.aclose()
        await api.aclose()

    assert first.status_code == 200
    assert first.content == _PAYLOAD
    assert second.status_code == 200
    assert second.content == _PAYLOAD
    assert len(requests) == 1


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
