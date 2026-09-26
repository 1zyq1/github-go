"""Validated HTTPX2 upstream requests with manual redirect handling."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import unquote_to_bytes, urljoin, urlsplit, urlunsplit

import httpx2

from app.config import Settings
from app.errors import AppError
from app.url_policy import RedirectHostRole, SourceTarget

MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_ALLOWED_REDIRECT_HOSTS: dict[RedirectHostRole, frozenset[str]] = {
    RedirectHostRole.RELEASE_ASSET: frozenset(
        {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}
    ),
    RedirectHostRole.ARCHIVE: frozenset({"codeload.github.com"}),
    RedirectHostRole.RAW_CONTENT: frozenset({"raw.githubusercontent.com"}),
}
_CONTROL_CHARACTER_LIMIT = 0x20
_DELETE_CHARACTER = 0x7F
_MAX_DECODE_PASSES = 4
_MAX_REDIRECT_URL_LENGTH = 4096
_MALFORMED_ESCAPE_PATTERN = re.compile(r"%(?![0-9A-Fa-f]{2})")
_SEPARATOR_ESCAPE_PATTERN = re.compile(r"%(?:2f|5c)", re.IGNORECASE)


@dataclass(slots=True)
class UpstreamResponse:
    """Keep the final HTTPX2 response open with its routing metadata."""

    response: httpx2.Response
    target: SourceTarget
    final_url: str


def _invalid_redirect() -> AppError:
    return AppError("invalid_redirect", "下载地址重定向无效。", 502)


def _upstream_unavailable() -> AppError:
    return AppError("upstream_unavailable", "上游下载服务暂时不可用。", 502)


def _upstream_timeout() -> AppError:
    return AppError("upstream_timeout", "上游下载超时。", 504)


def _has_control(value: str) -> bool:
    return any(
        ord(character) < _CONTROL_CHARACTER_LIMIT or ord(character) == _DELETE_CHARACTER
        for character in value
    )


def _is_ip_literal(host: str) -> bool:
    try:
        _ = ip_address(host)
    except ValueError:
        return False
    return True


def _validate_path(path: str) -> None:
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        raise _invalid_redirect()
    if _MALFORMED_ESCAPE_PATTERN.search(path) or _SEPARATOR_ESCAPE_PATTERN.search(path):
        raise _invalid_redirect()
    decoded = path
    for _ in range(_MAX_DECODE_PASSES):
        if _MALFORMED_ESCAPE_PATTERN.search(decoded):
            raise _invalid_redirect()
        if _SEPARATOR_ESCAPE_PATTERN.search(decoded):
            raise _invalid_redirect()
        try:
            next_path = unquote_to_bytes(decoded).decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise _invalid_redirect() from error
        if _has_control(next_path) or "\\" in next_path or "//" in next_path:
            raise _invalid_redirect()
        if next_path == decoded:
            break
        decoded = next_path
    else:
        raise _invalid_redirect()
    segments = decoded[1:].split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise _invalid_redirect()


def _validate_redirect_url(
    location: str,
    base_url: str,
    role: RedirectHostRole,
) -> str:
    if (
        not location
        or len(location) > _MAX_REDIRECT_URL_LENGTH
        or _has_control(location)
        or any(character.isspace() for character in location)
        or "\\" in location
        or location.startswith("//")
        or "#" in location
    ):
        raise _invalid_redirect()
    try:
        raw_parts = urlsplit(location)
        if any(segment in {".", ".."} for segment in raw_parts.path.split("/")):
            raise _invalid_redirect()
        absolute_url = urljoin(base_url, location)
        parts = urlsplit(absolute_url)
        port = parts.port
    except ValueError as error:
        raise _invalid_redirect() from error
    if parts.scheme.lower() != "https" or parts.username is not None or parts.password is not None:
        raise _invalid_redirect()
    if port not in {None, 443} or not parts.hostname:
        raise _invalid_redirect()
    host = parts.hostname.lower()
    if _is_ip_literal(host) or host.isdigit():
        raise _invalid_redirect()
    base_host = urlsplit(base_url).hostname
    relative = not raw_parts.scheme and not raw_parts.netloc
    if host not in _ALLOWED_REDIRECT_HOSTS[role] and not (
        relative and base_host is not None and host == base_host.lower()
    ):
        raise _invalid_redirect()
    _validate_path(parts.path)
    authority = host
    return urlunsplit(("https", authority, parts.path, parts.query, ""))


def build_upstream_client(settings: Settings) -> httpx2.AsyncClient:
    """Build the pooled client used by the application lifespan."""
    limits = httpx2.Limits(
        max_connections=settings.max_connections,
        max_keepalive_connections=settings.max_keepalive_connections,
    )
    transport = httpx2.AsyncHTTPTransport(
        verify=True,
        trust_env=False,
        http1=True,
        http2=True,
        limits=limits,
        retries=1,
    )
    timeout = httpx2.Timeout(
        connect=settings.connect_timeout_seconds,
        read=settings.read_timeout_seconds,
        write=settings.read_timeout_seconds,
        pool=settings.pool_timeout_seconds,
    )
    return httpx2.AsyncClient(
        transport=transport,
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
        trust_env=False,
        headers={
            "User-Agent": settings.user_agent,
            "Accept-Encoding": "identity",
        },
    )


async def open_upstream(
    client: httpx2.AsyncClient,
    target: SourceTarget,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
) -> UpstreamResponse:
    """Open a source response while validating every redirect by resource role."""
    if method not in {"GET", "HEAD"}:
        code = "invalid_request"
        message = "请求方法无效。"
        raise AppError(code, message, 405)
    current_url = target.url
    visited = {current_url}
    request_headers = dict(headers or {})
    for redirect_count in range(MAX_REDIRECTS + 1):
        try:
            request = client.build_request(method, current_url, headers=request_headers)
            response = await client.send(request, stream=True, follow_redirects=False)
        except httpx2.TimeoutException as error:
            raise _upstream_timeout() from error
        except httpx2.RequestError as error:
            raise _upstream_unavailable() from error
        if response.status_code not in _REDIRECT_STATUSES:
            return UpstreamResponse(response, target, str(request.url))
        try:
            if redirect_count >= MAX_REDIRECTS:
                raise _upstream_unavailable()
            location = response.headers.get("location")
            if location is None:
                raise _invalid_redirect()
            next_url = _validate_redirect_url(location, current_url, target.redirect_host_role)
            if next_url in visited:
                raise _invalid_redirect()
        except AppError:
            await response.aclose()
            raise
        visited.add(next_url)
        await response.aclose()
        current_url = next_url
    raise _upstream_unavailable()


__all__ = [
    "MAX_REDIRECTS",
    "UpstreamResponse",
    "build_upstream_client",
    "open_upstream",
]
