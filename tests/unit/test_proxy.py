"""Unit tests for bounded streaming proxy behavior."""

from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import override

import anyio
import httpx2
import pytest
from app.cache import DownloadCache
from app.errors import AppError
from app.proxy import ProxyService
from app.url_policy import encode_proxy_token
from starlette.responses import FileResponse, Response, StreamingResponse

pytestmark = pytest.mark.asyncio
_TOKEN = encode_proxy_token("https://raw.githubusercontent.com/acme/widgets/main/file.bin")
_ARCHIVE_TOKEN = encode_proxy_token("https://github.com/acme/widgets/archive/refs/tags/v1.0.0.zip")


class _Stream(httpx2.AsyncByteStream):
    def __init__(
        self,
        chunks: tuple[bytes, ...] = (),
        *,
        error: Exception | None = None,
        gate: anyio.Event | None = None,
    ) -> None:
        self.chunks: tuple[bytes, ...] = chunks
        self.error: Exception | None = error
        self.gate: anyio.Event | None = gate
        self.closed: bool = False

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error

    @override
    async def aclose(self) -> None:
        self.closed = True


def _response(
    status_code: int,
    headers: Mapping[str, str] | None = None,
    stream: httpx2.AsyncByteStream | None = None,
    *,
    content: bytes = b"",
) -> httpx2.Response:
    if stream is None:
        return httpx2.Response(status_code, headers=dict(headers or {}), content=content)
    return httpx2.Response(status_code, headers=dict(headers or {}), stream=stream)


def _service(
    handler: Callable[[httpx2.Request], httpx2.Response],
    *,
    capacity: int = 1,
    queue_timeout: float = 0.0,
    cache: DownloadCache | None = None,
) -> tuple[ProxyService, httpx2.AsyncClient, anyio.CapacityLimiter]:
    """Build a proxy over a mock transport.

    The queue wait defaults to zero so every other test sees a full limiter as
    an immediate refusal; the tests that exercise queueing ask for a wait
    explicitly rather than making the rest of the file sit through one.
    """
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    limiter = anyio.CapacityLimiter(capacity)
    service = ProxyService(
        client=client,
        limiter=limiter,
        cache=cache,
        queue_timeout_seconds=queue_timeout,
    )
    return service, client, limiter


async def _body(response: Response) -> bytes:
    if not isinstance(response, StreamingResponse):
        return bytes(response.body)
    body = bytearray()
    async for chunk in response.body_iterator:
        assert isinstance(chunk, bytes)
        body.extend(chunk)
    return bytes(body)


def _read_file(path: str) -> bytes:
    """Read a file the proxy published, from a plain helper rather than a task."""
    return Path(path).read_bytes()


def _entry_suffixes(directory: Path) -> list[str]:
    """List what the cache directory holds, by suffix."""
    return sorted(entry.suffix for entry in directory.iterdir())


@pytest.mark.parametrize("status_code", [200, 206])
async def test_streams_file_responses_without_buffering(status_code: int) -> None:
    stream = _Stream((b"first", b"second"))
    service, client, limiter = _service(
        lambda _request: _response(
            status_code,
            {
                "content-type": "application/octet-stream",
                "content-length": "11",
                "set-cookie": "secret=true",
                "connection": "keep-alive",
                "transfer-encoding": "chunked",
                "access-control-allow-origin": "*",
                "x-secret": "do-not-forward",
            },
            stream,
        )
    )
    try:
        response = await service.handle(_TOKEN, "GET", {})
        assert isinstance(response, StreamingResponse)
        assert response.status_code == status_code
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-length"] == "11"
        assert "set-cookie" not in response.headers
        assert "connection" not in response.headers
        assert "transfer-encoding" not in response.headers
        assert "access-control-allow-origin" not in response.headers
        assert "x-secret" not in response.headers
        assert await _body(response) == b"firstsecond"
        assert stream.closed is True
        assert limiter.borrowed_tokens == 0
    finally:
        await client.aclose()


async def test_forwards_only_safe_request_headers() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _response(200, content=b"ok")

    service, client, _limiter = _service(handler)
    try:
        response = await service.handle(
            _TOKEN,
            "GET",
            {
                "Range": "bytes=0-3",
                "If-Range": '"etag"',
                "If-None-Match": '"etag"',
                "If-Modified-Since": "Wed, 21 Oct 2015 07:28:00 GMT",
                "Authorization": "Bearer secret",
                "Cookie": "session=secret",
                "X-Forwarded-For": "127.0.0.1",
            },
        )
        assert await _body(response) == b"ok"
    finally:
        await client.aclose()

    request = requests[0]
    assert request.headers["range"] == "bytes=0-3"
    assert request.headers["if-range"] == '"etag"'
    assert request.headers["if-none-match"] == '"etag"'
    assert request.headers["if-modified-since"].startswith("Wed, 21 Oct")
    assert "authorization" not in request.headers
    assert "cookie" not in request.headers
    assert "x-forwarded-for" not in request.headers


async def test_head_and_bodyless_upstream_responses_release_capacity() -> None:
    for status_code in (200, 304):
        stream = _Stream()
        service, client, limiter = _service(
            lambda _request, status=status_code, body_stream=stream: _response(
                status,
                stream=body_stream,
            )
        )
        try:
            response = await service.handle(_TOKEN, "HEAD", {})
            assert response.status_code == status_code
            assert response.body == b""
            assert stream.closed is True
            assert limiter.borrowed_tokens == 0
        finally:
            await client.aclose()


async def test_maps_missing_and_non_file_statuses() -> None:
    for upstream_status, expected_status, expected_code in (
        (404, 404, "upstream_not_found"),
        (410, 404, "upstream_not_found"),
        (500, 502, "upstream_unavailable"),
        (302, 502, "invalid_redirect"),
    ):
        service, client, limiter = _service(
            lambda _request, status=upstream_status: _response(
                status,
                {"set-cookie": "secret=true"},
                _Stream() if status == 302 else None,
            )
        )
        try:
            if upstream_status == 302:
                with pytest.raises(AppError) as raised:
                    _ = await service.handle(_TOKEN, "GET", {})
                assert raised.value.code == expected_code
            else:
                with pytest.raises(AppError) as raised:
                    _ = await service.handle(_TOKEN, "GET", {})
                assert raised.value.status_code == expected_status
                assert raised.value.code == expected_code
            assert limiter.borrowed_tokens == 0
        finally:
            await client.aclose()


async def test_maps_range_not_satisfiable_without_forwarding_upstream_body() -> None:
    service, client, limiter = _service(
        lambda _request: _response(
            416,
            {"content-range": "bytes */100", "content-length": "4", "set-cookie": "secret=true"},
        )
    )
    try:
        with pytest.raises(AppError) as raised:
            _ = await service.handle(_TOKEN, "GET", {"Range": "bytes=200-300"})
        assert raised.value.code == "range_not_satisfiable"
        assert raised.value.headers["content-range"] == "bytes */100"
        assert limiter.borrowed_tokens == 0
    finally:
        await client.aclose()


@pytest.mark.parametrize("upstream_status", [403, 429])
async def test_maps_upstream_throttling_to_retryable_503(upstream_status: int) -> None:
    service, client, limiter = _service(
        lambda _request, status=upstream_status: _response(
            status,
            {"retry-after": "42", "set-cookie": "secret=true"},
        )
    )
    try:
        with pytest.raises(AppError) as raised:
            _ = await service.handle(_TOKEN, "GET", {})
        assert raised.value.code == "upstream_rate_limited"
        assert raised.value.status_code == 503
        assert raised.value.headers["retry-after"] == "42"
        assert "set-cookie" not in raised.value.headers
        assert limiter.borrowed_tokens == 0
    finally:
        await client.aclose()


async def test_drops_upstream_retry_after_that_is_not_a_delay() -> None:
    service, client, limiter = _service(
        lambda _request: _response(429, {"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"})
    )
    try:
        with pytest.raises(AppError) as raised:
            _ = await service.handle(_TOKEN, "GET", {})
        assert raised.value.code == "upstream_rate_limited"
        assert "retry-after" not in raised.value.headers
        assert limiter.borrowed_tokens == 0
    finally:
        await client.aclose()


async def test_refuses_capacity_exhaustion_when_queueing_is_disabled() -> None:
    gate = anyio.Event()
    first_stream = _Stream((b"first",), gate=gate)
    service, client, limiter = _service(
        lambda _request: _response(200, stream=first_stream), capacity=1
    )
    try:
        first = await service.handle(_TOKEN, "GET", {})
        assert limiter.borrowed_tokens == 1
        with pytest.raises(AppError) as raised:
            _ = await service.handle(_TOKEN, "GET", {})
        assert raised.value.code == "capacity_exhausted"
        assert raised.value.status_code == 503
        gate.set()
        assert await _body(first) == b"first"
        assert first_stream.closed is True
        assert limiter.borrowed_tokens == 0
    finally:
        await client.aclose()


async def test_queues_for_a_free_slot_instead_of_failing_at_once() -> None:
    gate = anyio.Event()
    first_stream = _Stream((b"first",), gate=gate)
    calls: list[int] = []

    def handler(_request: httpx2.Request) -> httpx2.Response:
        calls.append(len(calls))
        if len(calls) == 1:
            return _response(200, stream=first_stream)
        return _response(200, content=b"second")

    service, client, limiter = _service(handler, capacity=1, queue_timeout=5.0)
    try:
        first = await service.handle(_TOKEN, "GET", {})
        assert limiter.borrowed_tokens == 1

        results: list[Response] = []

        async def queued() -> None:
            results.append(await service.handle(_TOKEN, "GET", {}))

        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(queued)
            # The waiter must not steal the slot from the request that holds it.
            await anyio.sleep(0.1)
            assert limiter.borrowed_tokens == 1
            gate.set()
            assert await _body(first) == b"first"

        assert [await _body(response) for response in results] == [b"second"]
        assert limiter.borrowed_tokens == 0
    finally:
        await client.aclose()


async def test_gives_up_when_the_queue_wait_expires() -> None:
    gate = anyio.Event()
    first_stream = _Stream((b"first",), gate=gate)
    service, client, limiter = _service(
        lambda _request: _response(200, stream=first_stream),
        capacity=1,
        queue_timeout=0.05,
    )
    try:
        first = await service.handle(_TOKEN, "GET", {})
        with pytest.raises(AppError) as raised:
            _ = await service.handle(_TOKEN, "GET", {})
        assert raised.value.code == "capacity_exhausted"
        assert raised.value.status_code == 503
        assert limiter.borrowed_tokens == 1
        gate.set()
        assert await _body(first) == b"first"
        assert limiter.borrowed_tokens == 0
    finally:
        await client.aclose()


async def test_midstream_error_closes_response_and_releases_capacity() -> None:
    stream = _Stream((b"first",), error=httpx2.ReadTimeout("midstream"))
    service, client, limiter = _service(lambda _request: _response(200, stream=stream))
    try:
        response = await service.handle(_TOKEN, "GET", {})
        with pytest.raises(httpx2.ReadTimeout):
            _ = await _body(response)
        assert stream.closed is True
        assert limiter.borrowed_tokens == 0
    finally:
        await client.aclose()


async def test_setup_errors_release_capacity() -> None:
    service, client, limiter = _service(lambda _request: _response(200, content=b"ok"))
    try:
        with pytest.raises(AppError):
            _ = await service.handle("bad token", "GET", {})
        assert limiter.borrowed_tokens == 0
    finally:
        await client.aclose()


async def test_concurrent_cache_misses_coalesce_into_one_upstream_fetch(
    tmp_path: Path,
) -> None:
    """The second request waits for the first instead of opening its own fetch.

    The whole point of the single flight is that a burst of requests for one
    payload costs one upstream call, so this is driven at the service level
    where the interleaving is exact: one request holds the upstream open while
    the other arrives, and the arrival must read the publisher's file rather
    than start a second fetch.
    """
    requests: list[httpx2.Request] = []
    gate = anyio.Event()

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        stream = _Stream((b"payload",), gate=gate)
        return _response(200, {"content-type": "application/zip"}, stream)

    cache = DownloadCache(tmp_path, 1_000_000)
    service, client, _limiter = _service(handler, capacity=4, cache=cache)
    streamed: list[bytes] = []
    from_disk: list[str] = []

    async def fetch() -> None:
        response = await service.handle(_ARCHIVE_TOKEN, "GET", {})
        if isinstance(response, FileResponse):
            from_disk.append(str(response.path))
        else:
            streamed.append(await _body(response))

    try:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(fetch)
            _ = tasks.start_soon(fetch)
            await anyio.sleep(0.2)
            gate.set()
    finally:
        await client.aclose()

    assert len(requests) == 1
    assert streamed == [b"payload"]
    assert len(from_disk) == 1
    assert _read_file(from_disk[0]) == b"payload"


async def test_cache_fill_keeps_partial_headers_out_of_the_upstream_request(
    tmp_path: Path,
) -> None:
    """A ranged request fills the cache from an unconditional whole-payload fetch.

    Storing the bytes of a 206 under the URL of the whole file would poison the
    cache, so the fill has to ask for everything -- while every other
    conditional header still travels upstream.
    """
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _response(200, {"content-type": "application/zip"}, _Stream((b"whole",)))

    cache = DownloadCache(tmp_path, 1_000_000)
    service, client, _limiter = _service(handler, capacity=4, cache=cache)
    try:
        response = await service.handle(
            _ARCHIVE_TOKEN,
            "GET",
            {"Range": "bytes=0-2", "If-Range": '"etag"', "If-None-Match": '"etag"'},
        )

        assert await _body(response) == b"whole"
    finally:
        await client.aclose()

    assert requests[0].headers.get("range") is None
    assert requests[0].headers.get("if-range") is None
    assert requests[0].headers["if-none-match"] == '"etag"'
    assert _entry_suffixes(tmp_path) == [".entry"]


async def test_raw_content_never_enters_the_cache(tmp_path: Path) -> None:
    """Raw URLs follow a branch, so they are fetched every time."""
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _response(200, {"content-type": "text/plain"}, _Stream((b"hello",)))

    cache = DownloadCache(tmp_path, 1_000_000)
    service, client, _limiter = _service(handler, capacity=4, cache=cache)
    try:
        first = await service.handle(_TOKEN, "GET", {"Range": "bytes=0-1"})
        second = await service.handle(_TOKEN, "GET", {"Range": "bytes=0-1"})

        assert await _body(first) == b"hello"
        assert await _body(second) == b"hello"
    finally:
        await client.aclose()

    assert len(requests) == 2
    assert requests[0].headers["range"] == "bytes=0-1"
    assert _entry_suffixes(tmp_path) == []
