"""Unit tests for bounded streaming proxy behavior."""

from collections.abc import AsyncIterator, Callable, Mapping
from typing import override

import anyio
import httpx2
import pytest
from app.errors import AppError
from app.proxy import ProxyService
from app.url_policy import encode_proxy_token
from starlette.responses import Response, StreamingResponse

pytestmark = pytest.mark.asyncio
_TOKEN = encode_proxy_token("https://raw.githubusercontent.com/acme/widgets/main/file.bin")


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
) -> tuple[ProxyService, httpx2.AsyncClient, anyio.CapacityLimiter]:
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    limiter = anyio.CapacityLimiter(capacity)
    return ProxyService(client=client, limiter=limiter), client, limiter


async def _body(response: Response) -> bytes:
    if not isinstance(response, StreamingResponse):
        return bytes(response.body)
    body = bytearray()
    async for chunk in response.body_iterator:
        assert isinstance(chunk, bytes)
        body.extend(chunk)
    return bytes(body)


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


async def test_rejects_capacity_exhaustion_without_queueing() -> None:
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
