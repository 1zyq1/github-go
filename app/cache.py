"""Disk-backed cache for immutable archive payloads."""

import hashlib
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import anyio

_MEDIA_TYPES: tuple[tuple[str, str], ...] = (
    (".tar.gz", "application/gzip"),
    (".tgz", "application/gzip"),
    (".tar", "application/x-tar"),
    (".zip", "application/zip"),
)
_FALLBACK_MEDIA_TYPE = "application/octet-stream"
_ENTRY_SUFFIX = ".entry"
_TEMPORARY_SUFFIX = ".part"
_STALE_TEMPORARY_SECONDS = 3600.0


def media_type_for(source_url: str) -> str:
    """Return the media type implied by an archive URL's extension."""
    lowered = source_url.lower()
    for suffix, media_type in _MEDIA_TYPES:
        if lowered.endswith(suffix):
            return media_type
    return _FALLBACK_MEDIA_TYPE


@dataclass(frozen=True, slots=True)
class CachedArchive:
    """A complete archive payload living on disk."""

    path: Path
    media_type: str
    size: int


class PendingArchive:
    """A cache entry being written, published atomically once complete."""

    def __init__(self, temporary: Path, final: Path) -> None:
        """Bind a temporary file to the cache entry it will become."""
        self._temporary: Path = temporary
        self._final: Path = final
        self._handle: anyio.AsyncFile[bytes] | None = None
        self._written: int = 0

    @property
    def written(self) -> int:
        """Return how many payload bytes have been written so far."""
        return self._written

    async def write(self, chunk: bytes) -> None:
        """Append one payload chunk to the temporary file."""
        handle = self._handle
        if handle is None:
            handle = await anyio.open_file(self._temporary, "wb")
            self._handle = handle
        _ = await handle.write(chunk)
        self._written += len(chunk)

    async def commit(self) -> None:
        """Close the temporary file and move it into place."""
        await self._close()
        if self._written == 0:
            self.discard()
            return
        _ = self._temporary.replace(self._final)

    async def abort(self) -> None:
        """Close and delete a partial entry."""
        await self._close()
        self.discard()

    def discard(self) -> None:
        """Delete the temporary file without publishing it."""
        self._handle = None
        self._temporary.unlink(missing_ok=True)

    async def _close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            await handle.aclose()


class ArchiveCache:
    """Store archive payloads on disk and evict the least recently used."""

    def __init__(self, root: Path, max_bytes: int) -> None:
        """Point the cache at a directory and a total size cap."""
        self._root: Path = root
        self._max_bytes: int = max_bytes

    def lookup(self, source_url: str) -> CachedArchive | None:
        """Return a complete entry, refreshing its recency, or None."""
        path = self._entry_path(source_url)
        try:
            stat = path.stat()
        except OSError:
            return None
        if stat.st_size == 0:
            return None
        try:
            os.utime(path, None)
        except OSError:
            return None
        return CachedArchive(path, media_type_for(source_url), stat.st_size)

    def begin(self, source_url: str) -> PendingArchive:
        """Start writing a new entry for an archive URL."""
        self._root.mkdir(parents=True, exist_ok=True)
        final = self._entry_path(source_url)
        temporary = final.with_name(final.name + _TEMPORARY_SUFFIX)
        return PendingArchive(temporary, final)

    async def publish(self, pending: PendingArchive) -> None:
        """Commit a pending entry, then enforce the size cap."""
        if pending.written > self._max_bytes:
            await pending.abort()
            return
        await pending.commit()
        await anyio.to_thread.run_sync(self._evict)

    def _entry_path(self, source_url: str) -> Path:
        digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
        return self._root / f"{digest}{_ENTRY_SUFFIX}"

    def _evict(self) -> None:
        try:
            children = list(self._root.iterdir())
        except OSError:
            return
        now = time.time()
        total = 0
        entries: list[tuple[float, int, Path]] = []
        for child in children:
            try:
                stat = child.stat()
            except OSError:
                continue
            if child.name.endswith(_TEMPORARY_SUFFIX):
                if now - stat.st_mtime > _STALE_TEMPORARY_SECONDS:
                    with suppress(OSError):
                        child.unlink()
                continue
            entries.append((stat.st_mtime, stat.st_size, child))
            total += stat.st_size
        if total <= self._max_bytes:
            return
        entries.sort(key=lambda entry: entry[0])
        for _mtime, size, child in entries:
            if total <= self._max_bytes:
                break
            try:
                child.unlink()
            except OSError:
                continue
            total -= size


class SingleFlight:
    """Keep at most one in-flight producer per key so callers can coalesce."""

    def __init__(self) -> None:
        """Start with no in-flight producers."""
        self._events: dict[str, anyio.Event] = {}

    def join_or_lead(self, key: str) -> tuple[bool, anyio.Event]:
        """Return whether the caller leads, plus the completion event."""
        existing = self._events.get(key)
        if existing is not None:
            return False, existing
        event = anyio.Event()
        self._events[key] = event
        return True, event

    def finish(self, key: str, event: anyio.Event) -> None:
        """Wake waiters and forget the key."""
        _ = self._events.pop(key, None)
        event.set()


__all__ = [
    "ArchiveCache",
    "CachedArchive",
    "PendingArchive",
    "SingleFlight",
    "media_type_for",
]
