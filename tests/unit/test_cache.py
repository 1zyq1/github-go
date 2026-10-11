"""Unit tests for the disk-backed download cache."""

import os
from pathlib import Path

import pytest
from app.cache import DownloadCache, SingleFlight, media_type_for

pytestmark = pytest.mark.asyncio

_URL = "https://github.com/acme/widgets/archive/refs/tags/v1.0.0.zip"
_OTHER_URL = "https://github.com/acme/widgets/archive/refs/tags/v2.0.0.zip"


def _names(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


def _read(path: Path) -> bytes:
    return path.read_bytes()


def _write(path: Path, payload: bytes) -> None:
    _ = path.write_bytes(payload)


def _exists(path: Path) -> bool:
    return path.exists()


async def _publish(cache: DownloadCache, source_url: str, payload: bytes) -> None:
    pending = cache.begin(source_url)
    await pending.write(payload)
    await cache.publish(pending)


@pytest.mark.parametrize(
    ("source_url", "expected"),
    [
        ("https://github.com/acme/widgets/archive/refs/tags/v1.zip", "application/zip"),
        ("https://github.com/acme/widgets/archive/refs/tags/v1.tar.gz", "application/gzip"),
        ("https://github.com/acme/widgets/archive/refs/heads/main.tgz", "application/gzip"),
        ("https://github.com/acme/widgets/archive/refs/heads/main.tar", "application/x-tar"),
        ("https://github.com/acme/widgets/archive/refs/heads/main", "application/octet-stream"),
        (
            "https://github.com/acme/widgets/releases/download/v1.2.0/widget.exe",
            "application/octet-stream",
        ),
        (
            "https://github.com/acme/widgets/releases/download/v1.2.0/widget.tar.gz",
            "application/gzip",
        ),
    ],
)
async def test_media_type_for_maps_download_extensions(source_url: str, expected: str) -> None:
    assert media_type_for(source_url) == expected


async def test_lookup_returns_none_for_a_missing_entry(tmp_path: Path) -> None:
    cache = DownloadCache(tmp_path, 1_000_000)

    assert cache.lookup(_URL) is None


async def test_published_entry_is_retrievable(tmp_path: Path) -> None:
    cache = DownloadCache(tmp_path, 1_000_000)

    await _publish(cache, _URL, b"helloworld")

    cached = cache.lookup(_URL)
    assert cached is not None
    assert cached.size == 10
    assert cached.media_type == "application/zip"
    assert _read(cached.path) == b"helloworld"


async def test_aborted_entry_is_not_retrievable(tmp_path: Path) -> None:
    cache = DownloadCache(tmp_path, 1_000_000)
    pending = cache.begin(_URL)
    await pending.write(b"partial")

    await pending.abort()

    assert cache.lookup(_URL) is None
    assert _names(tmp_path) == []


async def test_entry_without_payload_is_discarded(tmp_path: Path) -> None:
    cache = DownloadCache(tmp_path, 1_000_000)

    await _publish(cache, _URL, b"")

    assert cache.lookup(_URL) is None
    assert _names(tmp_path) == []


async def test_entry_larger_than_the_cap_is_refused(tmp_path: Path) -> None:
    cache = DownloadCache(tmp_path, 4)

    await _publish(cache, _URL, b"12345")

    assert cache.lookup(_URL) is None
    assert _names(tmp_path) == []


async def test_least_recently_used_entry_is_evicted_when_the_cap_is_exceeded(
    tmp_path: Path,
) -> None:
    cache = DownloadCache(tmp_path, 10)
    await _publish(cache, _URL, b"12345678")
    oldest = cache.lookup(_URL)
    assert oldest is not None
    os.utime(oldest.path, (1000, 1000))

    await _publish(cache, _OTHER_URL, b"abcdefgh")

    assert cache.lookup(_URL) is None
    assert cache.lookup(_OTHER_URL) is not None


async def test_stale_partial_files_are_removed(tmp_path: Path) -> None:
    cache = DownloadCache(tmp_path, 1_000_000)
    stale = tmp_path / f"{'0' * 64}.entry.part"
    _write(stale, b"leftover")
    os.utime(stale, (1000, 1000))

    await _publish(cache, _URL, b"payload")

    assert not _exists(stale)
    assert cache.lookup(_URL) is not None


async def test_single_flight_hands_followers_the_leader_event() -> None:
    flight = SingleFlight()

    leader, event = flight.join_or_lead("key")
    follower, same_event = flight.join_or_lead("key")

    assert leader is True
    assert follower is False
    assert same_event is event

    flight.finish("key", event)

    assert event.is_set()
    next_leader, _ = flight.join_or_lead("key")
    assert next_leader is True
