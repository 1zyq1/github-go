"""Unit tests for the outbound gzip helpers."""

import gzip
from collections.abc import AsyncIterator

import pytest
from app.compression import accepts_gzip, gzip_stream, note_accept_encoding_vary


async def _stream(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_gzip_stream_round_trips_and_shrinks() -> None:
    original = b"<p>mirrored github page</p>" * 200
    source = _stream(original[:500], original[500:])

    compressed = b"".join([chunk async for chunk in gzip_stream(source)])

    assert gzip.decompress(compressed) == original
    assert len(compressed) < len(original) // 4


@pytest.mark.asyncio
async def test_gzip_stream_yields_a_valid_empty_member() -> None:
    compressed = b"".join([chunk async for chunk in gzip_stream(_stream())])

    assert gzip.decompress(compressed) == b""


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, False),
        ("identity", False),
        ("deflate, br", False),
        ("gzip", True),
        ("gzip, deflate, br", True),
        ("br;q=1.0, gzip;q=0.8", True),
    ],
)
def test_accepts_gzip_follows_starlette(header: str | None, expected: bool) -> None:
    assert accepts_gzip(header) is expected


def test_vary_is_added_when_absent() -> None:
    headers: dict[str, str] = {}

    note_accept_encoding_vary(headers)

    assert headers == {"vary": "Accept-Encoding"}


def test_vary_keeps_existing_names_and_deduplicates() -> None:
    already_listed = {"vary": "Cookie, accept-encoding"}
    messy = {"vary": " , Cookie , "}

    note_accept_encoding_vary(already_listed)
    note_accept_encoding_vary(messy)

    assert already_listed["vary"] == "Cookie, accept-encoding"
    assert messy["vary"] == "Cookie, Accept-Encoding"
