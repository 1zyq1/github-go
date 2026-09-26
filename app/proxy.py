"""Bounded, non-buffering proxy responses for validated GitHub targets."""

import re
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass, field

import anyio
import httpx2
from starlette.responses import FileResponse, Response, StreamingResponse

from app.cache import ArchiveCache, CachedArchive, PendingArchive, SingleFlight
from app.errors import AppError
from app.upstream import UpstreamResponse, open_upstream
from app.url_policy import ResourceKind, SourceTarget, decode_proxy_token

_SAFE_REQUEST_HEADERS = ("range", "if-range", "if-none-match", "if-modified-since")
_SAFE_RESPONSE_HEADERS = frozenset(
    {
        "accept-ranges",
        "cache-control",
        "content-encoding",
        "content-language",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "expires",
        "last-modified",
        "vary",
    }
)
_CONTENT_RANGE_PATTERN = re.compile(r"bytes (?:[0-9]+-[0-9]+|[0-9]+|\*)/(?:\*|[0-9]+)\Z")
_RETRY_AFTER_PATTERN = re.compile(r"[0-9]{1,5}\Z")
_RATE_LIMITED_STATUSES = frozenset({403, 429})
_DELETE_CHARACTER = 0x7F
_OK_STATUS = 200
_NOT_MODIFIED_STATUS = 304
_RANGE_NOT_SATISFIABLE_STATUS = 416


class _CapacityLease:
    def __init__(self, limiter: anyio.CapacityLimiter, borrower: object) -> None:
        self._limiter: anyio.CapacityLimiter = limiter
        self._borrower: object = borrower
        self._released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._limiter.release_on_behalf_of(self._borrower)


class _ArchiveSink:
    """Publish one fetched archive into the cache and release its flight."""

    def __init__(
        self,
        cache: ArchiveCache,
        pending: PendingArchive,
        flight: SingleFlight,
        key: str,
        event: anyio.Event,
    ) -> None:
        self._cache: ArchiveCache = cache
        self._pending: PendingArchive = pending
        self._flight: SingleFlight = flight
        self._key: str = key
        self._event: anyio.Event = event
        self._settled: bool = False

    async def feed(self, chunk: bytes) -> None:
        """Append payload bytes to the pending entry."""
        await self._pending.write(chunk)

    async def complete(self) -> None:
        """Publish the entry, releasing waiters even if the disk write fails."""
        if self._settled:
            return
        self._settled = True
        try:
            await self._cache.publish(self._pending)
        except OSError:
            with suppress(OSError):
                await self._pending.abort()
        finally:
            self._release()

    async def abandon(self) -> None:
        """Drop a partial entry and release waiters."""
        if self._settled:
            return
        self._settled = True
        try:
            await self._pending.abort()
        finally:
            self._release()

    def _release(self) -> None:
        self._flight.finish(self._key, self._event)


@dataclass(slots=True)
class ProxyService:
    """Open upstream files while holding a bounded capacity lease."""

    client: httpx2.AsyncClient
    limiter: anyio.CapacityLimiter
    cache: ArchiveCache | None = None
    flight: SingleFlight = field(default_factory=SingleFlight)

    async def handle(
        self,
        token: str,
        method: str,
        request_headers: Mapping[str, str],
    ) -> Response:
        """Resolve a token and return a bounded upstream response."""
        target = decode_proxy_token(token)
        action = await self._cache_action(target, method, request_headers)
        if isinstance(action, Response):
            return action
        if isinstance(action, _ArchiveSink):
            try:
                return await self._fetch(target, method, request_headers, action)
            except AppError:
                await action.abandon()
                raise
        return await self._fetch(target, method, request_headers, None)

    async def _cache_action(
        self,
        target: SourceTarget,
        method: str,
        request_headers: Mapping[str, str],
    ) -> Response | _ArchiveSink | None:
        """Serve a cached archive, join an in-flight fetch, or lead a new one."""
        cache = self.cache
        if cache is None or target.kind is not ResourceKind.ARCHIVE:
            return None
        cached = cache.lookup(target.url)
        if cached is not None:
            return _cached_response(cached)
        if not _may_populate_cache(method, request_headers):
            return None
        leader, event = self.flight.join_or_lead(target.url)
        if leader:
            return _ArchiveSink(cache, cache.begin(target.url), self.flight, target.url, event)
        await event.wait()
        cached = cache.lookup(target.url)
        if cached is not None:
            return _cached_response(cached)
        return None

    async def _fetch(
        self,
        target: SourceTarget,
        method: str,
        request_headers: Mapping[str, str],
        sink: _ArchiveSink | None,
    ) -> Response:
        borrower = object()
        try:
            self.limiter.acquire_on_behalf_of_nowait(borrower)
        except anyio.WouldBlock as error:
            code = "capacity_exhausted"
            message = "下载服务繁忙, 请稍后重试."
            raise AppError(code, message, 503) from error
        lease = _CapacityLease(self.limiter, borrower)
        try:
            upstream = await open_upstream(
                self.client,
                target,
                method=method,
                headers=_forwarded_request_headers(request_headers),
            )
        except AppError:
            lease.release()
            raise
        return await self._response(upstream, method, lease, sink)

    async def _response(
        self,
        upstream: UpstreamResponse,
        method: str,
        lease: _CapacityLease,
        sink: _ArchiveSink | None,
    ) -> Response:
        response = upstream.response
        response_open = True
        try:
            if method == "HEAD":
                response_open = False
                await _close_response(response, lease)
                return _bodyless_response(
                    response.status_code,
                    _safe_response_headers(response, bodyless=True),
                )
            if response.status_code in {200, 206}:
                headers = _safe_response_headers(response, bodyless=False)
                body_sink = sink if response.status_code == _OK_STATUS else None
                result = StreamingResponse(
                    _stream_response(response, lease, body_sink),
                    status_code=response.status_code,
                    headers=headers,
                )
                response_open = False
                if body_sink is not None:
                    sink = None
                return result
            response_open = False
            await _close_response(response, lease)
            if response.status_code == _NOT_MODIFIED_STATUS:
                return _bodyless_response(
                    _NOT_MODIFIED_STATUS,
                    _safe_response_headers(response, bodyless=True),
                )
            if response.status_code == _RANGE_NOT_SATISFIABLE_STATUS:
                raise _range_error(response.headers.get("content-range"))
            if response.status_code in {404, 410}:
                code = "upstream_not_found"
                message = "上游文件不存在。"
                raise AppError(code, message, 404)
            if response.status_code in _RATE_LIMITED_STATUSES:
                raise _rate_limited_error(response)
            code = "upstream_unavailable"
            message = "上游下载服务暂时不可用。"
            raise AppError(code, message, 502)
        finally:
            if response_open:
                response_open = False
                await _close_response(response, lease)
            if sink is not None:
                await sink.abandon()


async def _close_response(response: httpx2.Response, lease: _CapacityLease) -> None:
    try:
        await response.aclose()
    finally:
        lease.release()


def _bodyless_response(status_code: int, headers: Mapping[str, str]) -> Response:
    """Create a response without Starlette's implicit zero content length."""
    response = Response(status_code=status_code, headers=dict(headers))
    if "content-length" in response.headers:
        del response.headers["content-length"]
    return response


async def _stream_response(
    response: httpx2.Response,
    lease: _CapacityLease,
    sink: _ArchiveSink | None,
) -> AsyncIterator[bytes]:
    try:
        async for chunk in response.aiter_bytes():
            if sink is not None:
                await sink.feed(chunk)
            yield chunk
        if sink is not None:
            await sink.complete()
    finally:
        try:
            if sink is not None:
                # Shielded so a disconnecting client still releases the flight
                # and closes the partial file instead of stranding a .part entry.
                with anyio.CancelScope(shield=True):
                    await sink.abandon()
        finally:
            try:
                await response.aclose()
            finally:
                lease.release()


def _cached_response(cached: CachedArchive) -> Response:
    """Serve a stored archive from disk, with range and HEAD support."""
    return FileResponse(cached.path, media_type=cached.media_type)


def _may_populate_cache(method: str, request_headers: Mapping[str, str]) -> bool:
    """Allow caching only for a complete GET, never a partial range request."""
    if method != "GET":
        return False
    return not any(name.lower() == "range" for name in request_headers)


def _forwarded_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    normalized = {name.lower(): value for name, value in headers.items()}
    forwarded: dict[str, str] = {}
    for name in _SAFE_REQUEST_HEADERS:
        value = normalized.get(name)
        if value:
            forwarded[name] = value
    return forwarded


def _safe_response_headers(
    response: httpx2.Response,
    *,
    bodyless: bool,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in _SAFE_RESPONSE_HEADERS:
        value = response.headers.get(name)
        if value is None or not _safe_header_value(value):
            continue
        if name == "content-length" and (bodyless or not value.isdigit()):
            continue
        if name == "content-range" and _CONTENT_RANGE_PATTERN.fullmatch(value) is None:
            continue
        headers[name] = value
    return headers


def _safe_header_value(value: str) -> bool:
    return all(
        character not in {"\r", "\n", "\x00"} and ord(character) != _DELETE_CHARACTER
        for character in value
    )


def _rate_limited_error(response: httpx2.Response) -> AppError:
    """Report upstream throttling instead of hiding it behind a generic 502."""
    headers: dict[str, str] = {}
    retry_after = response.headers.get("retry-after")
    if retry_after is not None:
        candidate = retry_after.strip()
        if _RETRY_AFTER_PATTERN.fullmatch(candidate):
            headers["retry-after"] = candidate
    return AppError(
        "upstream_rate_limited",
        "上游服务限流, 请稍后重试.",
        503,
        headers,
    )


def _range_error(content_range: str | None) -> AppError:
    headers: dict[str, str] = {}
    if content_range is not None and _CONTENT_RANGE_PATTERN.fullmatch(content_range):
        headers["content-range"] = content_range
    return AppError(
        "range_not_satisfiable",
        "请求范围无法满足。",
        416,
        headers,
    )


__all__ = ["ProxyService"]
