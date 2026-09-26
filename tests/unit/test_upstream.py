"""Deterministic tests for manual upstream redirect handling."""

from collections.abc import AsyncIterator
from typing import override

import httpx2
import pytest
from app.config import Settings
from app.errors import AppError
from app.upstream import MAX_REDIRECTS, build_upstream_client, open_upstream
from app.url_policy import SourceTarget, parse_source_url

pytestmark = pytest.mark.asyncio


_TIMEOUT_MESSAGE = "slow redirect"
_OFFLINE_MESSAGE = "offline"


class _TrackingStream(httpx2.AsyncByteStream):
    def __init__(self) -> None:
        self.closed: bool = False

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b""

    @override
    async def aclose(self) -> None:
        self.closed = True


def _target(url: str) -> SourceTarget:
    return parse_source_url(url)


def _response(
    status_code: int,
    location: str | None = None,
    *,
    stream: httpx2.AsyncByteStream | None = None,
) -> httpx2.Response:
    headers = {} if location is None else {"location": location}
    if stream is None:
        return httpx2.Response(status_code, headers=headers, content=b"")
    return httpx2.Response(status_code, headers=headers, stream=stream)


async def _close_client(client: httpx2.AsyncClient) -> None:
    await client.aclose()


@pytest.mark.parametrize(
    ("source_url", "redirect_url"),
    [
        (
            "https://github.com/acme/widgets/releases/download/v1/widget.zip",
            "https://release-assets.githubusercontent.com/assets/widget",
        ),
        (
            "https://github.com/acme/widgets/archive/refs/heads/main.zip",
            "https://codeload.github.com/acme/widgets/zip/main",
        ),
        (
            "https://raw.githubusercontent.com/acme/widgets/main/README.md",
            "https://raw.githubusercontent.com/acme/widgets/main/docs/README.md",
        ),
    ],
)
async def test_allows_redirect_for_each_resource_role(
    source_url: str,
    redirect_url: str,
) -> None:
    calls: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        if len(calls) == 1:
            return _response(302, redirect_url)
        return _response(200)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        result = await open_upstream(client, _target(source_url))
        await result.response.aclose()

    target = _target(source_url)
    assert len(calls) == 2
    assert result.target == target
    assert result.final_url == redirect_url


async def test_preserves_signed_query_and_accepts_relative_redirect() -> None:
    calls: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        if len(calls) == 1:
            return _response(302, "/objects/file.bin?X-Signature=abc%2F123&part=4")
        return _response(200)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        result = await open_upstream(
            client,
            _target("https://raw.githubusercontent.com/acme/widgets/main/file.bin"),
        )
        await result.response.aclose()

    assert str(calls[1].url) == (
        "https://raw.githubusercontent.com/objects/file.bin?X-Signature=abc%2F123&part=4"
    )
    assert result.final_url == str(calls[1].url)


@pytest.mark.parametrize(
    "location",
    [
        "http://release-assets.githubusercontent.com/assets/file",
        "//release-assets.githubusercontent.com/assets/file",
        "https://release-assets.githubusercontent.com:444/assets/file",
        "https://user:pass@release-assets.githubusercontent.com/assets/file",
        "https://127.0.0.1/assets/file",
        "https://evil.example/assets/file",
        "/file#fragment",
        "/file%ZZ",
        "/file%2Fname",
    ],
)
async def test_rejects_unsafe_redirects(location: str) -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return _response(302, location)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with pytest.raises(AppError) as raised:
            _ = await open_upstream(
                client,
                _target("https://github.com/acme/widgets/releases/download/v1/file.zip"),
            )

    assert raised.value.code == "invalid_redirect"
    assert raised.value.status_code == 502


async def test_rejects_missing_location() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return _response(302)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with pytest.raises(AppError) as raised:
            _ = await open_upstream(
                client,
                _target("https://github.com/acme/widgets/releases/download/v1/file.zip"),
            )

    assert raised.value.code == "invalid_redirect"


async def test_rejects_redirect_loop() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return _response(302, "/same-location")

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with pytest.raises(AppError) as raised:
            _ = await open_upstream(
                client,
                _target("https://raw.githubusercontent.com/acme/widgets/main/file.bin"),
            )

    assert raised.value.code == "invalid_redirect"


async def test_rejects_more_than_five_redirects() -> None:
    calls = 0

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return _response(302, f"/hop-{calls}")

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with pytest.raises(AppError) as raised:
            _ = await open_upstream(
                client,
                _target("https://raw.githubusercontent.com/acme/widgets/main/file.bin"),
            )

    assert raised.value.code == "upstream_unavailable"
    assert len(raised.value.message) > 0
    assert MAX_REDIRECTS == 5


@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_preserves_get_and_head(method: str) -> None:
    methods: list[str] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        methods.append(request.method)
        return _response(200)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        result = await open_upstream(
            client,
            _target("https://raw.githubusercontent.com/acme/widgets/main/file.bin"),
            method=method,
        )
        await result.response.aclose()

    assert methods == [method]


async def test_maps_timeout_and_closes_intermediate_response() -> None:
    streams: list[_TrackingStream] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("file.bin"):
            stream = _TrackingStream()
            streams.append(stream)
            return _response(302, "/final", stream=stream)
        raise httpx2.ReadTimeout(_TIMEOUT_MESSAGE, request=request)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with pytest.raises(AppError) as raised:
            _ = await open_upstream(
                client,
                _target("https://raw.githubusercontent.com/acme/widgets/main/file.bin"),
            )

    assert raised.value.code == "upstream_timeout"
    assert raised.value.status_code == 504
    assert streams[0].closed is True


async def test_builds_explicit_safe_client() -> None:
    settings = Settings.model_validate(
        {
            "connect_timeout_seconds": 3,
            "read_timeout_seconds": 20,
            "pool_timeout_seconds": 4,
            "max_connections": 17,
            "max_keepalive_connections": 7,
        }
    )
    client = build_upstream_client(settings)
    try:
        assert client.follow_redirects is False
        assert client.timeout.connect == 3
        assert client.timeout.read == 20
        assert client.timeout.pool == 4
    finally:
        await _close_client(client)


async def test_maps_connection_error() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(_OFFLINE_MESSAGE, request=request)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with pytest.raises(AppError) as raised:
            _ = await open_upstream(
                client,
                _target("https://raw.githubusercontent.com/acme/widgets/main/file.bin"),
            )

    assert raised.value.code == "upstream_unavailable"
    assert raised.value.status_code == 502
